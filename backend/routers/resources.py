"""资源路由 — 复用课程的文件管理逻辑，加 category 字段"""
import os
import json
import shutil
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from utils import (
    get_db, get_current_user, require_role,
    _safe_path, _get_teacher_key,
    _move_to_trash, _gen_unique_course_id,
    _read_course_meta, _write_course_meta, _natural_sort_key, _split_rel,
    RESOURCE_BASE
)

router = APIRouter()


# ── 路径工具 ──────────────────────────────────────────────

def _get_teacher_resource_dir(teacher_username: str) -> str:
    return os.path.join(RESOURCE_BASE, f"{_get_teacher_key(teacher_username)}_resource")


def _get_resource_path(caller: dict, resource_id: str) -> str:
    base = _get_teacher_resource_dir(caller["username"])
    path = _safe_path(base, resource_id)
    if not os.path.exists(path):
        raise HTTPException(status_code=404, detail="资源不存在")
    return path


# ── 文件树（复用课程逻辑）─────────────────────────────────

def _walk_resource_tree(dirpath: str) -> list:
    items = []
    try:
        names = os.listdir(dirpath)
    except OSError:
        return items
    dirs = sorted(
        (n for n in names if os.path.isdir(os.path.join(dirpath, n)) and not n.startswith(".")),
        key=_natural_sort_key,
    )
    files = sorted(
        (n for n in names if os.path.isfile(os.path.join(dirpath, n)) and not n.startswith(".")),
        key=_natural_sort_key,
    )
    for d in dirs:
        sub = os.path.join(dirpath, d)
        items.append({"name": d, "type": "folder", "size": os.stat(sub).st_size, "children": _walk_resource_tree(sub)})
    for f in files:
        fp = os.path.join(dirpath, f)
        items.append({"name": f, "type": "file", "size": os.stat(fp).st_size})
    return items


# ── CRUD ──────────────────────────────────────────────────

@router.get("/api/resources")
def list_resources(teacher: str = ""):
    if teacher:
        teacher_dirs = [_get_teacher_resource_dir(teacher)]
    else:
        teacher_dirs = []
        if os.path.exists(RESOURCE_BASE):
            for d in sorted(os.listdir(RESOURCE_BASE)):
                full = os.path.join(RESOURCE_BASE, d)
                if os.path.isdir(full) and d.endswith("_resource"):
                    teacher_dirs.append(full)
    items = []
    for tdir in teacher_dirs:
        teacher_name = os.path.basename(tdir).replace("_resource", "")
        if not os.path.exists(tdir):
            continue
        for rid in sorted(os.listdir(tdir), key=_natural_sort_key):
            if rid.startswith(".") or rid == "_index.json":
                continue
            rpath = os.path.join(tdir, rid)
            if not os.path.isdir(rpath):
                continue
            meta = _read_course_meta(rpath)
            items.append({
                "id": rid, "name": meta.get("name") or rid,
                "category": meta.get("category", "reference"),
                "description": meta.get("description", ""),
                "teacher": teacher_name,
                "url": f"/resources/{teacher_name}_resource/{rid}/",
                "created_at": meta.get("created_at", ""),
            })
    return {"status": "success", "resources": items}


@router.post("/api/resources")
def create_resource(request: Request, name: str = "", category: str = "reference"):
    caller = require_role(request, ["admin", "teacher"])
    res_dir = _get_teacher_resource_dir(caller["username"])
    os.makedirs(res_dir, exist_ok=True)
    for existing in os.listdir(res_dir):
        ep = os.path.join(res_dir, existing)
        if os.path.isdir(ep) and _read_course_meta(ep).get("name") == name:
            raise HTTPException(status_code=400, detail="资源已存在")
    resource_id = _gen_unique_course_id()
    resource_path = _safe_path(res_dir, resource_id)
    os.makedirs(resource_path)
    meta = {
        "id": resource_id, "name": name.strip() or "未命名资源",
        "category": category if category in ("reference", "download") else "reference",
        "description": "", "teacher": caller["username"],
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write_course_meta(resource_path, meta)
    return {"status": "success", "message": f"资源 [{name}] 已创建", "resource": {"id": resource_id, "name": meta["name"], "category": meta["category"]}}


@router.get("/api/resources/{resource_id}/tree")
def get_resource_tree(request: Request, resource_id: str, path: str = ""):
    caller = require_role(request, ["admin", "teacher"])
    resource_path = _get_resource_path(caller, resource_id)
    target = _safe_path(resource_path, *_split_rel(path)) if path else resource_path
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="路径不存在")
    meta = _read_course_meta(resource_path)
    items = _walk_resource_tree(target) if os.path.isdir(target) else []
    return {"status": "success", "tree": items, "meta": meta}


@router.post("/api/resources/{resource_id}/tree/upload")
async def upload_resource_tree(request: Request, resource_id: str, path: str = "", file: UploadFile = File(...)):
    caller = require_role(request, ["admin", "teacher"])
    resource_path = _get_resource_path(caller, resource_id)
    target_dir = _safe_path(resource_path, *_split_rel(path))
    os.makedirs(target_dir, exist_ok=True)
    safe_name = os.path.basename(file.filename or "unnamed").lstrip(".")
    if not safe_name:
        raise HTTPException(status_code=400, detail="文件名无效")
    content = await file.read()
    with open(os.path.join(target_dir, safe_name), "wb") as f:
        f.write(content)
    return {"status": "success", "message": f"文件 [{safe_name}] 已上传", "name": safe_name}


@router.post("/api/resources/{resource_id}/tree/folder")
def create_resource_folder(request: Request, resource_id: str, path: str = ""):
    caller = require_role(request, ["admin", "teacher"])
    resource_path = _get_resource_path(caller, resource_id)
    target = _safe_path(resource_path, *_split_rel(path))
    os.makedirs(target, exist_ok=True)
    return {"status": "success", "message": "文件夹已创建", "path": path}


@router.post("/api/resources/{resource_id}/tree/rename")
def rename_resource_entry(request: Request, resource_id: str, form, path: str = ""):
    caller = require_role(request, ["admin", "teacher"])
    resource_path = _get_resource_path(caller, resource_id)
    from utils import _valid_new_name
    old_path = _safe_path(resource_path, *_split_rel(path))
    if not os.path.exists(old_path):
        raise HTTPException(status_code=404, detail="目标不存在")
    new_name = _valid_new_name(form.new_name)
    if new_name == os.path.basename(old_path.rstrip("/\\")):
        raise HTTPException(status_code=400, detail="新名称与原名相同")
    new_path = _safe_path(os.path.dirname(old_path), new_name)
    if os.path.exists(new_path):
        raise HTTPException(status_code=400, detail="同名项目已存在")
    os.rename(old_path, new_path)
    return {"status": "success", "message": f"已重命名为 [{new_name}]", "name": new_name}


@router.delete("/api/resources/{resource_id}/tree")
def delete_resource_entry(request: Request, resource_id: str, path: str = ""):
    caller = require_role(request, ["admin", "teacher"])
    resource_path = _get_resource_path(caller, resource_id)
    target = _safe_path(resource_path, *_split_rel(path))
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="目标不存在")
    if os.path.isdir(target):
        shutil.rmtree(target)
    else:
        os.remove(target)
    return {"status": "success", "message": "已删除"}


@router.put("/api/resources/{resource_id}")
def update_resource(request: Request, resource_id: str, name: str = "", category: str = ""):
    caller = require_role(request, ["admin", "teacher"])
    resource_path = _get_resource_path(caller, resource_id)
    meta = _read_course_meta(resource_path)
    if name:
        meta["name"] = name.strip()
    if category and category in ("reference", "download"):
        meta["category"] = category
    _write_course_meta(resource_path, meta)
    return {"status": "success", "resource": meta}


@router.delete("/api/resources/{resource_id}")
def delete_resource(request: Request, resource_id: str):
    caller = require_role(request, ["admin", "teacher"])
    resource_path = _get_resource_path(caller, resource_id)
    shutil.rmtree(resource_path)
    return {"status": "success", "message": "资源已删除"}

