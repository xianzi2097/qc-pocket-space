"""电子书发布队列 —— C9/C10（后台单线程串行，拍板：不做并行）

状态机：queued → building → done / failed，写 assets.publish JSON 列（入库不落盘）。
一个后台 worker + 一个队列：一次只渲染一门（Node build 并发争抢模板目录会翻车）。
入队即返回，前端 C10 轮询状态 —— 正如 DMA：C9 写命令启动，C10 读状态标志位。

真实渲染 = V1 build.py 平移：清模板 → 拷 md → 自动首页 → sidebar → 便携 Node
vitepress build → 原子替换产物（失败旧产物保留）。产物路径按资产 id（D 路径教训）。
测试 monkeypatch _run_build 模拟成功/失败；真实跑一次 = M4 收口（规范 §8.5）。
"""
import json
import logging
import os
import queue
import re
import shutil
import subprocess
import threading
from datetime import datetime
from pathlib import Path

from . import config
from .db import SessionLocal
from .models import Asset, FileOrder

logger = logging.getLogger("codenova.v2.publish")

RUNTIME_DIR = config.QC_DIR / "runtime"
TEMPLATE_DIR = RUNTIME_DIR / "vitepress-template"
NODE_EXE = RUNTIME_DIR / "node" / "node.exe"
SITES_DIR = config.QC_DIR / "frontend" / "course_vitepress_dist"
LOGS_DIR = config.DATA_ROOT / "build_logs"
_BUILD_TIMEOUT = 1200  # 秒（2026-11-16 实测：2GB 服务器换页抖动下 23 文件要 910s，原 300s 必超时）

_q: "queue.Queue[str]" = queue.Queue()
_start_lock = threading.Lock()
_worker_started = False


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _set_publish(asset_id: str, **fields) -> None:
    db = SessionLocal()
    try:
        asset = db.get(Asset, asset_id)
        if asset is None:
            return                                  # 资产已被删：状态无人接收，静默丢弃
        publish_state = dict(asset.publish or {})
        publish_state.update(fields)
        asset.publish = publish_state
        db.commit()
    finally:
        db.close()


def is_active(state: str | None) -> bool:
    return state in ("queued", "building")


def enqueue(asset_id: str) -> None:
    global _worker_started
    with _start_lock:
        if not _worker_started:
            threading.Thread(target=_worker, daemon=True, name="qc-publish").start()
            _worker_started = True
    _q.put(asset_id)


def _worker() -> None:
    while True:
        asset_id = _q.get()
        _set_publish(asset_id, state="building", started_at=_now())
        try:
            _run_build(asset_id)
        except Exception as exc:
            logger.exception("publish failed: %s", asset_id)
            _set_publish(asset_id, state="failed", message=str(exc)[:500],
                         finished_at=_now())
        else:
            _set_publish(asset_id, state="done", message="", finished_at=_now())
        finally:
            _q.task_done()


# ---------------- 真实渲染（V1 build.py 平移） ----------------

def _run_build(asset_id: str) -> None:
    """失败抛异常（worker 转 failed）；成功静默返回（worker 转 done）"""
    db = SessionLocal()
    try:
        asset = db.get(Asset, asset_id)
        if asset is None:
            raise RuntimeError("资产已删除，取消渲染")
        title = asset.name
        description = asset.description or ""
        order_map: dict[str, list[str]] = {}
        for row in db.query(FileOrder).filter_by(asset_id=asset_id).order_by(
                FileOrder.parent_path, FileOrder.position):
            order_map.setdefault(row.parent_path, []).append(row.name)
    finally:
        db.close()

    source = config.DATA_ROOT / "assets" / asset_id
    if not source.is_dir():
        raise RuntimeError("资产目录缺失")
    docs_dir = TEMPLATE_DIR / "docs"
    vp_dir = docs_dir / ".vitepress"
    if not vp_dir.is_dir():
        raise RuntimeError(f"模板缺失: {vp_dir}")
    if not NODE_EXE.exists():
        raise RuntimeError(f"便携 Node 缺失: {NODE_EXE}")

    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / f"{asset_id}.log"

    with open(log_path, "w", encoding="utf-8") as logf:
        def log(msg: str) -> None:
            logf.write(msg + "\n")
            logf.flush()

        _clear_workspace_docs()
        try:
            # 1. 拷贝源内容到模板 docs（保留 V1 单文件夹 unwrap：只传一个文件夹时拷其内容）
            children = [c for c in source.iterdir() if not c.name.startswith(".")]
            if len(children) == 1 and children[0].is_dir():
                for child in children[0].iterdir():
                    _copy(child, docs_dir / child.name)
                log(f"📚 拷贝内容: {children[0].name}/* → docs/")
            else:
                for child in children:
                    _copy(child, docs_dir / child.name)

            # 2. 无首页自动生成 index.md（否则 vitepress 不产 index.html）
            _ensure_index(docs_dir, title, description)

            # 3. sidebar（file_order 自定义排序 → gen/sidebar.mjs）
            sidebar = _build_sidebar(docs_dir, order_map)
            gen_dir = vp_dir / "gen"
            gen_dir.mkdir(exist_ok=True)
            (gen_dir / "sidebar.mjs").write_text(
                "export const sidebar = "
                + json.dumps(sidebar, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            # 4. 跑 build（VP_BASE 只用资产 id，纯 ASCII，避开中文 base 编码坑）
            env = dict(os.environ)
            env["VP_BASE"] = f"/course/{asset_id}/"
            env["VP_TITLE"] = title
            # 学科主题（2026-11-16）：按课程类型选调色板，模板 [data-vp-theme] 覆盖
            env["VP_THEME"] = {"scratch": "orange", "cpp": "blue"}.get(
                (asset.type or "").lower(), "green")
            cmd = [str(NODE_EXE), "node_modules/vitepress/bin/vitepress.js", "build", "docs"]
            log("=== 开始渲染 ===")
            proc = subprocess.run(
                cmd, cwd=str(TEMPLATE_DIR), env=env,
                capture_output=True, text=True, timeout=_BUILD_TIMEOUT,
                encoding="utf-8", errors="replace",
            )
            log(proc.stdout or "")
            if proc.stderr:
                log("--- stderr ---\n" + proc.stderr)
            if proc.returncode != 0:
                raise RuntimeError(f"vitepress build 失败，退出码 {proc.returncode}")

            out_dir = docs_dir / "out"
            if not (out_dir / "index.html").exists():
                raise RuntimeError("渲染产物缺失 index.html")

            # 5. 原子替换正式产物（失败旧产物原样保留）
            final_dir = SITES_DIR / asset_id
            final_dir.parent.mkdir(parents=True, exist_ok=True)
            bak = Path(str(final_dir) + ".bak")
            if final_dir.exists():
                shutil.rmtree(bak, ignore_errors=True)
                final_dir.rename(bak)
            try:
                out_dir.rename(final_dir)
            except Exception:
                if bak.exists() and not final_dir.exists():
                    bak.rename(final_dir)
                raise
            shutil.rmtree(bak, ignore_errors=True)
            log("=== 渲染成功 ===")
        finally:
            # 构建后清空模板工作区（源文件在 data，随时可重建）
            try:
                _clear_workspace_docs()
            except Exception:
                pass


# ---------------- 模板工具（V1 平移） ----------------

def _clear_workspace_docs() -> None:
    docs_dir = TEMPLATE_DIR / "docs"
    if not docs_dir.is_dir():
        return
    for name in os.listdir(docs_dir):
        if name == ".vitepress":
            continue
        p = docs_dir / name
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=True)
        else:
            p.unlink(missing_ok=True)


def _copy(src: Path, dst: Path) -> None:
    if src.is_dir():
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        shutil.copy2(src, dst)


def _ensure_index(docs_dir: Path, title: str, description: str = "") -> None:
    """根目录没有 index.md 时自动生成首页（课程名 + 描述 + 顶层条目）"""
    if (docs_dir / "index.md").exists():
        return
    lines = [f"# {title}"]
    if description.strip():
        lines += ["", description.strip()]
    lines.append("")
    for name in sorted(os.listdir(docs_dir)):
        if name.startswith("."):
            continue
        p = docs_dir / name
        if p.is_dir():
            if (p / "index.md").exists():
                lines.append(f"- [{name}](/{name}/)")
            else:
                lines.append(f"- 📁 {name}")
        elif name.endswith(".md"):
            lines.append(f"- [{name[:-3]}](/{name[:-3]})")
    if len(lines) == 1:
        lines += ["", "本课程暂无内容，请上传 md 文件。"]
    (docs_dir / "index.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _natural_sort_key(name: str):
    return [int(t) if t.isdigit() else t.lower() for t in re.split(r"(\d+)", name)]


def _build_sidebar(docs_dir: Path, order_map: dict[str, list[str]],
                   rel_prefix: str = "") -> list:
    """递归生成 vitepress sidebar：
    - 目录 = 分组；目录内有 index.md 则分组标题链到该目录首页
    - md = 页面；index.md 不进侧边栏
    - file_order 有该层记录则按它排，否则自然排序
    """
    items = []
    try:
        all_names = [n for n in os.listdir(docs_dir) if not n.startswith(".")]
    except OSError:
        return items

    custom = order_map.get(rel_prefix)
    if custom:
        index = {name: i for i, name in enumerate(custom)}
        all_names.sort(key=lambda n: index.get(n, 9999))
    else:
        all_names.sort(key=_natural_sort_key)

    for name in all_names:
        fp = docs_dir / name
        rel = f"{rel_prefix}/{name}" if rel_prefix else name
        if fp.is_dir():
            has_index = (fp / "index.md").exists()
            sub = _build_sidebar(fp, order_map, rel)
            if has_index or sub:
                item = {"text": name, "collapsible": True, "collapsed": False}
                if has_index:
                    item["link"] = f"/{rel}/"
                if sub:
                    item["items"] = sub
                items.append(item)
        elif name.endswith(".md") and name != "index.md":
            items.append({"text": name[:-3], "link": f"/{rel[:-3]}"})
    return items
