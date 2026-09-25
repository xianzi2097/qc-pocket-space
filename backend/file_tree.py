"""共享文件树 — 课程 / 资料（含软件下载）共用的单一事实来源

================================================================
历史教训（2026-09-25 修复）：
reorder 拖拽排序 2026-08-19 随课程模块诞生；2026-08-31 建资料模块时
从课程"复用文件树逻辑"，但只抄了 创建/上传/重命名/删除 四项，漏了 reorder，
前端拖拽 UI 却照抄了过来 → POST /api/resources/{id}/tree/reorder 一直 404 静默失败。
本模块把 walk + reorder + _order.json 读写收成一份，路由只做薄封装，杜绝再次分叉。

两边的真实差异用参数表达：
- meta_files   元数据文件名不同（course.json / resource.json），walk 和 reorder 都要排除
- dirs_first   无自定义顺序时：课程=目录文件交错自然排序（现状），资料=文件夹置顶（现状）
- with_path / with_dir_size  资料树多返回 path 与目录 size 字段（现状），课程树保持原字段
"""
import os

from fastapi import HTTPException

from utils import (
    _atomic_save, _file_lock, _natural_sort_key, _read_order_file, _safe_path,
)

# 各模块的元数据文件（walk 与 reorder 的 siblings 均需排除）
COURSE_META = ("course.json", "_order.json")
RESOURCE_META = ("resource.json", "_order.json")


def split_rel(rel: str) -> list:
    """把相对路径拆成安全的路径段，过滤 . / .."""
    rel = (rel or "").strip().strip("/\\")
    if not rel:
        return []
    return [p for p in rel.replace("\\", "/").split("/") if p not in (".", "..")]


def walk_tree(base_path: str, order_map: dict = None, rel_prefix: str = "",
              *, meta_files: tuple, dirs_first: bool = False,
              with_path: bool = False, with_dir_size: bool = False) -> list:
    """递归列出目录树。排序规则：
    1. _order.json 自定义顺序优先（含目录+文件的单一列表，完全按用户排的来）
    2. 无自定义顺序：dirs_first=True → 文件夹置顶后各自自然排序；
       False → 目录文件混在一起自然排序
    隐藏文件（. 开头）与 meta_files 一律不出现。
    """
    entries = []
    try:
        all_names = [n for n in os.listdir(base_path)
                     if not n.startswith(".") and n not in meta_files]
    except OSError:
        return entries
    dir_names = [n for n in all_names if os.path.isdir(os.path.join(base_path, n))]
    file_names = [n for n in all_names if os.path.isfile(os.path.join(base_path, n))]
    custom_order = (order_map or {}).get(rel_prefix)
    if custom_order:
        order_index = {name: i for i, name in enumerate(custom_order)}
        names = sorted(dir_names + file_names, key=lambda n: order_index.get(n, 9999))
    elif dirs_first:
        names = (sorted(dir_names, key=_natural_sort_key)
                 + sorted(file_names, key=_natural_sort_key))
    else:
        names = sorted(dir_names + file_names, key=_natural_sort_key)
    for name in names:
        fp = os.path.join(base_path, name)
        child_rel = f"{rel_prefix}/{name}" if rel_prefix else name
        if os.path.isdir(fp):
            node = {"name": name, "type": "dir"}
            if with_dir_size:
                node["size"] = os.stat(fp).st_size
            if with_path:
                node["path"] = child_rel
            node["children"] = walk_tree(
                fp, order_map, child_rel, meta_files=meta_files,
                dirs_first=dirs_first, with_path=with_path,
                with_dir_size=with_dir_size,
            )
            entries.append(node)
        else:
            node = {"name": name, "type": "file", "size": os.path.getsize(fp)}
            if with_path:
                node["path"] = child_rel
            entries.append(node)
    return entries


def reorder_entry(base_path: str, name: str, dir: str = "", direction: str = "up",
                  target: str = "", *, meta_files: tuple) -> dict:
    """调整同级条目顺序并持久化到 _order.json：
    direction=up/down 逐格移动，direction=swap + target 直接拖拽定位。
    整体文件锁 + 原子写，防并发 reorder 互相覆盖；边界/位置未变不写盘。
    """
    if direction not in ("up", "down", "swap"):
        raise HTTPException(status_code=400, detail="direction 必须是 up、down 或 swap")
    # 找到父目录在 base_path 内的相对路径
    dir_path = _safe_path(base_path, *split_rel(dir)) if dir else base_path
    if not os.path.isdir(dir_path):
        raise HTTPException(status_code=404, detail="目录不存在")
    rel_key = dir.strip("/")  # _order.json 的 key，空字符串 = 根目录
    order_path = os.path.join(base_path, "_order.json")
    # 读 order_map → 改 → 写 整体上锁（fp 与 _read_order_file 内部路径一致）
    with _file_lock(order_path):
        order_map = _read_order_file(base_path)
        # 获取当前同级条目列表
        siblings = [n for n in os.listdir(dir_path)
                    if not n.startswith(".") and n not in meta_files]
        current_order = order_map.get(rel_key, siblings)
        # 保留当前 order 中存在且磁盘上也存在的条目，追加磁盘上有但 order 中没有的
        existing_set = set(siblings)
        ordered = [n for n in current_order if n in existing_set]
        ordered.extend(n for n in siblings if n not in ordered)
        if name not in ordered:
            raise HTTPException(status_code=404, detail=f"未找到 {name}")
        if direction == "swap" and target:
            # 拖拽排序：把 name 移到 target 的位置
            if target not in ordered:
                raise HTTPException(status_code=404, detail=f"未找到 {target}")
            old_idx = ordered.index(name)
            new_idx = ordered.index(target)
            if old_idx == new_idx:
                return {"status": "success", "message": "位置未变"}
            # arrayMove 语义：移除 old_idx 插入到 new_idx
            item = ordered.pop(old_idx)
            ordered.insert(new_idx, item)
        else:
            idx = ordered.index(name)
            new_idx = idx - 1 if direction == "up" else idx + 1
            if new_idx < 0 or new_idx >= len(ordered):
                return {"status": "success", "message": "已在边界，无需调整"}
            ordered[idx], ordered[new_idx] = ordered[new_idx], ordered[idx]
        # 写回 _order.json：_atomic_save 原子写
        order_map[rel_key] = ordered
        _atomic_save(order_path, order_map)
    return {"status": "success", "message": f"{name} 已调整顺序", "order": ordered}
