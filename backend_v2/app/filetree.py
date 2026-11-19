"""资产管理 service —— 文件树七动作 + 打包下载（D13/D17 的"大一统"层）

磁盘布局：DATA_ROOT/assets/{assetId}/（建资产时建目录，软删整棵移进 trash/{assetId}）
本文件是 app/files.py（路径安全）的唯一业务调用方，路由层只做鉴权与信封：

  列目录     文件夹在前；组内"排序记录优先、没记录按名称升序"（默认绝不用磁盘时间）
  上传       自动创建路径中间层目录（D14③，魔改 Scratch 只给完整路径）；超限 413
  下载       F2：浏览器天生会开的格式 inline，其余 attachment（D15：按钮露出归前端）
  改名       账随货走：目录改名 → file_order 前缀批量跟随；同名幂等
  删除       树内物理删（不进回收站，C8 前端须二次确认）+ 清子树排序账
  排序       F7：把拖拽后的完整顺序全量写进 file_order（幂等）
  打包       单文件直发；目录实时打 zip 到临时文件，发完即弃（盘上不留包）
"""
import shutil
import tempfile
import urllib.parse
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import or_
from sqlalchemy.orm import Session as DbSession

from . import config
from .envelope import (
    E_FILE_TOO_LARGE,
    E_NOT_FOUND,
    E_PARAM_INVALID,
    E_PATH_EXISTS,
    E_PATH_INVALID,
    E_STORAGE_FULL,
    ApiError,
)
from .files import safe_join, valid_new_name, validate_rel_path
from .models import FileOrder

# 浏览器原生能"打开"的扩展名 → inline；其余一律 attachment（下载）
_INLINE_EXT = {
    ".txt", ".md", ".pdf", ".json",
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".mp4", ".webm", ".mp3", ".wav", ".ogg",
}
_HTML_EXT = {".html", ".htm"}
# D35：viz 演示页内嵌响应头 —— 脚本可跑（allow-scripts）但不给同源（摸不到登录态/父页面）
_HTML_INLINE_CSP = "Content-Security-Policy: sandbox allow-scripts"


# ---------------- 目录布局 ----------------

def asset_root(asset_id: str) -> Path:
    return config.DATA_ROOT / "assets" / asset_id


def create_asset_dir(asset_id: str) -> Path:
    root = asset_root(asset_id)
    root.mkdir(parents=True, exist_ok=True)
    return root


def asset_trash_dir(asset_id: str) -> Path:
    return config.DATA_ROOT / "trash" / asset_id


# ---------------- F1 列目录 ----------------

def list_entries(db: DbSession, asset_id: str, path: str | None) -> dict:
    rel = validate_rel_path(path)
    target = safe_join(asset_root(asset_id), rel)
    if not target.is_dir():
        raise ApiError(E_NOT_FOUND, "目录不存在", 404)

    order = {
        row.name: row.position
        for row in db.query(FileOrder).filter_by(asset_id=asset_id, parent_path=rel)
    }

    items = []
    for child in target.iterdir():
        is_dir = child.is_dir()
        items.append((0 if is_dir else 1, child.name, order.get(child.name), child))

    def sort_key(item):
        group, name, pos, _ = item
        # 组内：有记录按 position；没记录按名称升序（默认不碰磁盘时间）
        return (group, 0, pos, "") if pos is not None else (group, 1, 0, name)

    entries = []
    for _, name, _, child in sorted(items, key=sort_key):
        if child.is_dir():
            entries.append({"name": name, "type": "dir"})
        else:
            stat = child.stat()
            entries.append({
                "name": name,
                "type": "file",
                "size": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
            })
    return {"path": rel, "entries": entries, "total": len(entries)}


# ---------------- F3 上传 ----------------

def upload_file(asset_id: str, path: str | None, file: UploadFile) -> str:
    rel = validate_rel_path(path)
    if not rel:
        raise ApiError(E_PARAM_INVALID, "缺少文件路径", 400)
    target = safe_join(asset_root(asset_id), rel)
    target.parent.mkdir(parents=True, exist_ok=True)   # 中间层目录自动创建（D14③）

    limit = config.UPLOAD_MAX_BYTES                     # 运行时读取，测试可 monkeypatch
    size = 0
    try:
        with open(target, "wb") as out:
            while True:
                chunk = file.file.read(64 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > limit:
                    raise ApiError(E_FILE_TOO_LARGE, "文件过大", 413)
                out.write(chunk)
    except ApiError:
        target.unlink(missing_ok=True)
        raise
    except OSError:
        target.unlink(missing_ok=True)
        raise ApiError(E_STORAGE_FULL, "磁盘写入失败", 507)
    return rel


# ---------------- F4 建文件夹 ----------------

def mkdir(asset_id: str, path: str | None) -> str:
    rel = validate_rel_path(path)
    if not rel:
        raise ApiError(E_PARAM_INVALID, "路径不能为空", 400)
    target = safe_join(asset_root(asset_id), rel)
    if target.exists():
        raise ApiError(E_PATH_EXISTS, "路径已存在", 409)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.mkdir()
    except FileExistsError:
        raise ApiError(E_PATH_EXISTS, "路径已存在", 409)
    except OSError:
        raise ApiError(E_STORAGE_FULL, "磁盘写入失败", 507)
    return rel


# ---------------- F5 重命名（账随货走） ----------------

def rename(db: DbSession, asset_id: str, path: str | None, new_name: str) -> str:
    rel = validate_rel_path(path)
    if not rel:
        raise ApiError(E_PARAM_INVALID, "根目录不能改名", 400)
    name = valid_new_name(new_name)

    src = safe_join(asset_root(asset_id), rel)
    if not src.exists():
        raise ApiError(E_NOT_FOUND, "路径不存在", 404)

    parent, _, old = rel.rpartition("/")
    if old == name:
        return rel                                   # 同名幂等：没变化也算成功

    dst = src.parent / name
    if dst.exists():
        raise ApiError(E_PATH_EXISTS, "路径已存在", 409)
    try:
        src.rename(dst)
    except OSError:
        raise ApiError(E_STORAGE_FULL, "磁盘改名失败", 507)

    # 账随货走：① 自己那行改 name ② 子树行改 parent_path 前缀
    own = db.query(FileOrder).filter_by(
        asset_id=asset_id, parent_path=parent, name=old
    ).first()
    if own:
        own.name = name
    inner = db.query(FileOrder).filter(
        FileOrder.asset_id == asset_id,
        or_(FileOrder.parent_path == rel, FileOrder.parent_path.like(rel + "/%")),
    ).all()
    for row in inner:
        row.parent_path = name + row.parent_path[len(rel):]
    db.commit()

    return f"{parent}/{name}" if parent else name


# ---------------- F6 树内删除 ----------------

def remove(db: DbSession, asset_id: str, path: str | None) -> None:
    rel = validate_rel_path(path)
    if not rel:
        raise ApiError(E_PARAM_INVALID, "根目录不能删除（整资产用 DELETE /api/assets/{id}）", 400)
    target = safe_join(asset_root(asset_id), rel)
    if not target.exists():
        raise ApiError(E_NOT_FOUND, "路径不存在", 404)

    if target.is_dir():
        shutil.rmtree(target, ignore_errors=True)
    else:
        target.unlink(missing_ok=True)

    # 清子树排序账 + 自己在父层的那一行
    db.query(FileOrder).filter(
        FileOrder.asset_id == asset_id,
        or_(FileOrder.parent_path == rel, FileOrder.parent_path.like(rel + "/%")),
    ).delete(synchronize_session=False)
    parent, _, name = rel.rpartition("/")
    own = db.query(FileOrder).filter_by(asset_id=asset_id, parent_path=parent, name=name).first()
    if own:
        db.delete(own)
    db.commit()


# ---------------- F7 拖拽排序 ----------------

def apply_order(db: DbSession, asset_id: str, path: str | None,
                names: list, position: int) -> None:
    rel = validate_rel_path(path)
    target = safe_join(asset_root(asset_id), rel)
    if not target.is_dir():
        raise ApiError(E_NOT_FOUND, "目录不存在", 404)

    current = [e["name"] for e in list_entries(db, asset_id, rel)["entries"]]
    if not isinstance(names, list) or not names:
        raise ApiError(E_PARAM_INVALID, "names 不能为空", 400)
    dragged: list[str] = []
    for item in names:
        if not isinstance(item, str) or item not in current:
            raise ApiError(E_PARAM_INVALID, f"名称不存在：{item}", 400)
        if item not in dragged:
            dragged.append(item)

    pos = max(0, min(int(position), len(current)))
    rest = [n for n in current if n not in set(dragged)]
    final = rest[:pos] + dragged + rest[pos:]

    # 全量重写该层的顺序（幂等：同请求重复提交结果一致）
    db.query(FileOrder).filter_by(asset_id=asset_id, parent_path=rel).delete(
        synchronize_session=False
    )
    for i, name in enumerate(final):
        db.add(FileOrder(asset_id=asset_id, parent_path=rel, name=name, position=i))
    db.commit()


# ---------------- F2 单文件下载 ----------------

def open_target(asset_id: str, path: str | None) -> Path:
    rel = validate_rel_path(path)
    if not rel:
        raise ApiError(E_PARAM_INVALID, "缺少文件路径", 400)
    target = safe_join(asset_root(asset_id), rel)
    if not target.exists():
        raise ApiError(E_NOT_FOUND, "文件不存在", 404)
    if target.is_dir():
        raise ApiError(E_PARAM_INVALID, "目录请走打包下载", 400)
    return target


def file_response(path: Path, inline_html: bool = False) -> FileResponse:
    """F2 单文件响应。
    inline_html=True（仅 D35 viz 族调用方传）：.html 也走 inline 并加 CSP sandbox 头，
    供前端 iframe 内嵌渲染；其余调用方 .html 维持 attachment（老行为不变）。
    """
    ext = path.suffix.lower()
    inline = ext in _INLINE_EXT or (inline_html and ext in _HTML_EXT)
    disposition = "inline" if inline else "attachment"
    filename = urllib.parse.quote(path.name)
    headers = {"Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}"}
    if inline_html and ext in _HTML_EXT:
        headers["Content-Security-Policy"] = "sandbox allow-scripts"
    return FileResponse(path, headers=headers)


# ---------------- E6 打包下载 ----------------

def pack(asset_id: str, path: str | None) -> tuple[Path, bool]:
    """返回 (要发的文件, 是否为临时 zip)。单文件直发；目录实时打包、发完即弃。"""
    rel = validate_rel_path(path or "")
    target = safe_join(asset_root(asset_id), rel)
    if not target.exists():
        raise ApiError(E_NOT_FOUND, "路径不存在", 404)
    if target.is_file():
        return target, False

    children = list(target.iterdir())
    if len(children) == 1 and children[0].is_file():
        return children[0], False                      # 整资产就一个文件 → 直发不套壳

    handle = tempfile.NamedTemporaryFile(delete=False, suffix=".zip", prefix="qc_pack_")
    tmp_path = Path(handle.name)
    try:
        with zipfile.ZipFile(handle, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in sorted(target.rglob("*")):
                if item.is_file():
                    zf.write(item, item.relative_to(target).as_posix())
    finally:
        handle.close()
    return tmp_path, True
