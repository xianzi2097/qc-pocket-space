"""课程 + 课件素材路由"""
import os
import json
import shutil
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from utils import (
    get_db, get_current_user, require_role,
    _safe_path, _get_teacher_course_dir, _get_teacher_key,
    _move_to_trash, _gen_course_id, _resolve_course_format,
    _read_course_meta, _write_course_meta,
    CourseCreateForm, RenameForm
)

router = APIRouter()


def _get_course_path(caller: dict, course_id: str) -> str:
    """按当前登录人定位其名下的课程目录（谁的值谁动，跨老师物理隔离）"""
    base = _get_teacher_course_dir(caller["username"])
    course_path = _safe_path(base, course_id)
    if not os.path.exists(course_path):
        raise HTTPException(status_code=404, detail="课程不存在")
    return course_path


@router.get("/api/courses")
def list_courses(teacher: str = ""):
    course_dir = _get_teacher_course_dir(teacher)
    if not os.path.exists(course_dir):
        return {"status": "success", "courses": []}
    courses = []
    for folder_name in sorted(os.listdir(course_dir)):
        folder_path = os.path.join(course_dir, folder_name)
        if not os.path.isdir(folder_path) or folder_name.startswith("."):
            continue
        meta = _read_course_meta(folder_path)
        materials = [
            d for d in os.listdir(folder_path)
            if os.path.isdir(os.path.join(folder_path, d)) and not d.startswith(".")
        ]
        courses.append({
            "id": folder_name,  # 文件夹名 = API key（新课程为 id，存量课程为旧名）
            "name": meta.get("name") or folder_name,
            "type": meta.get("type", "scratch"),
            "format": _resolve_course_format(meta.get("type", "scratch"), meta.get("format", "")),
            "description": meta.get("description", ""),
            "material_count": len(materials),
            "publish": meta.get("publish", {"status": "idle"}),
            "teacher_key": _get_teacher_key(teacher),  # 用于构造 /course/<老师>/<id>/ 的 URL
        })
    return {"status": "success", "courses": courses}


@router.post("/api/courses")
def create_course(request: Request, form: CourseCreateForm):
    caller = require_role(request, ["admin", "teacher"])
    course_dir = _get_teacher_course_dir(caller["username"])
    os.makedirs(course_dir, exist_ok=True)
    # 同名校验：同一老师名下不允许同名课程（比较 json 显示名）
    for existing in os.listdir(course_dir):
        existing_path = os.path.join(course_dir, existing)
        if os.path.isdir(existing_path) and _read_course_meta(existing_path).get("name") == form.name:
            raise HTTPException(status_code=400, detail="课程已存在")
    course_id = _gen_course_id()
    course_path = _safe_path(course_dir, course_id)
    os.makedirs(course_path)
    fmt = _resolve_course_format(form.course_type, form.format)
    meta = {
        "id": course_id, "name": form.name, "type": form.course_type,
        "format": fmt, "description": "", "tags": [],
        "publish": {"status": "idle"},
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _write_course_meta(course_path, meta)
    course = {"id": course_id, "name": form.name, "type": form.course_type, "format": fmt}
    return {"status": "success", "message": f"课程 [{form.name}] 已创建", "course": course}


@router.get("/api/courses/{course_name}/materials")
def list_materials(course_name: str, teacher: str = ""):
    base = _get_teacher_course_dir(teacher)
    course_path = _safe_path(base, course_name)
    if not os.path.exists(course_path):
        raise HTTPException(status_code=404, detail="课程不存在")
    course_type = "scratch"
    meta_path = os.path.join(course_path, "course.json")
    if os.path.exists(meta_path):
        try:
            course_type = json.load(open(meta_path, "r", encoding="utf-8")).get("type", "scratch")
        except (json.JSONDecodeError, OSError):
            pass
    items = []
    for name in sorted(os.listdir(course_path)):
        fp = os.path.join(course_path, name)
        if not os.path.isdir(fp) or name.startswith("."):
            continue
        url = f"/share/{teacher}_course/{course_name}/{name}/"
        if course_type == "scratch":
            sb3_files = [f for f in os.listdir(fp) if f.endswith(".sb3")]
            items.append({
                "name": name, "has_main": "main.sb3" in sb3_files,
                "has_student": "student.sb3" in sb3_files,
                "sb3_count": len(sb3_files), "url": url,
            })
        else:
            files = []
            for fname in sorted(os.listdir(fp)):
                fpath = os.path.join(fp, fname)
                if os.path.isfile(fpath) and not fname.startswith("."):
                    files.append({"name": fname, "size": os.path.getsize(fpath)})
            items.append({"name": name, "files": files, "file_count": len(files), "url": url})
    return {"status": "success", "materials": items}


@router.delete("/api/courses/{course_name}/materials/{lesson_name}")
def delete_material(request: Request, course_name: str, lesson_name: str):
    caller = require_role(request, ["admin", "teacher"])
    base = _get_teacher_course_dir(caller["username"])
    course_path = _safe_path(base, course_name)
    if not os.path.exists(course_path):
        raise HTTPException(status_code=404, detail="课程不存在")
    lesson_path = _safe_path(course_path, lesson_name)
    if not os.path.exists(lesson_path):
        raise HTTPException(status_code=404, detail="课件不存在")
    shutil.rmtree(lesson_path)
    return {"status": "success", "message": f"课件 [{lesson_name}] 已删除"}


@router.delete("/api/courses/{course_name}/materials/{lesson_name}/{filename}")
def delete_file(request: Request, course_name: str, lesson_name: str, filename: str):
    caller = require_role(request, ["admin", "teacher"])
    base = _get_teacher_course_dir(caller["username"])
    course_path = _safe_path(base, course_name)
    if not os.path.exists(course_path):
        raise HTTPException(status_code=404, detail="课程不存在")
    lesson_path = _safe_path(course_path, lesson_name)
    if not os.path.exists(lesson_path):
        raise HTTPException(status_code=404, detail="课件不存在")
    file_path = _safe_path(lesson_path, filename)
    if not os.path.isfile(file_path):
        raise HTTPException(status_code=404, detail="文件不存在")
    os.remove(file_path)
    return {"status": "success", "message": f"文件 [{filename}] 已删除"}


@router.post("/api/courses/{course_name}/materials/{lesson_name}/upload")
async def upload_file(
    request: Request, course_name: str, lesson_name: str, file: UploadFile = File(...),
):
    caller = require_role(request, ["admin", "teacher"])
    if lesson_name == course_name:
        raise HTTPException(status_code=400, detail="课件名不能与课程名相同")
    # 文件名安全过滤：防止路径穿越
    safe_name = os.path.basename(file.filename or "unnamed").lstrip(".")
    if not safe_name:
        raise HTTPException(status_code=400, detail="文件名无效")
    base = _get_teacher_course_dir(caller["username"])
    course_path = _safe_path(base, course_name)
    if not os.path.exists(course_path):
        raise HTTPException(status_code=404, detail="课程不存在")
    lesson_path = _safe_path(course_path, lesson_name)
    os.makedirs(lesson_path, exist_ok=True)
    content = await file.read()
    # 文件大小限制：50MB
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大，最大 50MB")
    with open(os.path.join(lesson_path, safe_name), "wb") as f:
        f.write(content)
    return {
        "status": "success", "message": f"文件 [{safe_name}] 已上传",
        "path": f"/share/{caller['username']}_course/{course_name}/{lesson_name}/"
    }


@router.post("/api/courses/{course_name}/materials/{lesson_name}/folder")
def create_material_folder(request: Request, course_name: str, lesson_name: str):
    """新建课件文件夹（Python/C++ 课程）：直接 mkdir，无需上传 .keep 占位文件"""
    caller = require_role(request, ["admin", "teacher"])
    if lesson_name == course_name:
        raise HTTPException(status_code=400, detail="课件名不能与课程名相同")
    base = _get_teacher_course_dir(caller["username"])
    course_path = _safe_path(base, course_name)
    if not os.path.exists(course_path):
        raise HTTPException(status_code=404, detail="课程不存在")
    lesson_path = _safe_path(course_path, lesson_name)
    if os.path.exists(lesson_path):
        raise HTTPException(status_code=400, detail="课件已存在")
    os.makedirs(lesson_path)
    return {"status": "success", "message": f"课件文件夹 [{lesson_name}] 已创建"}


def _valid_new_name(new_name: str) -> str:
    """校验并清洗重命名目标：非空、不含路径分隔符"""
    new_name = (new_name or "").strip()
    if not new_name or "/" in new_name or "\\" in new_name:
        raise HTTPException(status_code=400, detail="名称无效（不能为空或含 / \\）")
    return new_name


@router.post("/api/courses/{course_name}/rename")
def rename_course(request: Request, course_name: str, form: RenameForm):
    """重命名课程：只改 course.json 的显示名，不动文件夹（文件夹名=id 是真相源）"""
    caller = require_role(request, ["admin", "teacher"])
    new_name = _valid_new_name(form.new_name)
    base = _get_teacher_course_dir(caller["username"])
    course_path = _safe_path(base, course_name)
    if not os.path.exists(course_path):
        raise HTTPException(status_code=404, detail="课程不存在")
    meta = _read_course_meta(course_path)
    if new_name == meta.get("name"):
        raise HTTPException(status_code=400, detail="新名称与原名相同")
    for existing in os.listdir(base):
        if existing == course_name:
            continue
        existing_path = os.path.join(base, existing)
        if os.path.isdir(existing_path) and _read_course_meta(existing_path).get("name") == new_name:
            raise HTTPException(status_code=400, detail="课程已存在")
    meta["name"] = new_name
    _write_course_meta(course_path, meta)
    return {"status": "success", "message": f"课程已重命名为 [{new_name}]", "name": new_name}


@router.post("/api/courses/{course_name}/materials/{lesson_name}/rename")
def rename_material(request: Request, course_name: str, lesson_name: str, form: RenameForm):
    """重命名课件文件夹（Scratch 与 Python/C++ 通用）"""
    caller = require_role(request, ["admin", "teacher"])
    new_name = _valid_new_name(form.new_name)
    if new_name == lesson_name:
        raise HTTPException(status_code=400, detail="新名称与原名相同")
    base = _get_teacher_course_dir(caller["username"])
    course_path = _safe_path(base, course_name)
    if not os.path.exists(course_path):
        raise HTTPException(status_code=404, detail="课程不存在")
    old_path = _safe_path(course_path, lesson_name)
    if not os.path.exists(old_path):
        raise HTTPException(status_code=404, detail="课件不存在")
    new_path = _safe_path(course_path, new_name)
    if os.path.exists(new_path):
        raise HTTPException(status_code=400, detail="课件已存在")
    os.rename(old_path, new_path)
    return {"status": "success", "message": f"课件已重命名为 [{new_name}]"}


@router.post("/api/courses/{course_name}/materials/{lesson_name}/{filename}/rename")
def rename_file(request: Request, course_name: str, lesson_name: str, filename: str, form: RenameForm):
    """重命名课件文件夹内的文件"""
    caller = require_role(request, ["admin", "teacher"])
    new_name = _valid_new_name(form.new_name)
    if new_name == filename:
        raise HTTPException(status_code=400, detail="新名称与原名相同")
    base = _get_teacher_course_dir(caller["username"])
    course_path = _safe_path(base, course_name)
    if not os.path.exists(course_path):
        raise HTTPException(status_code=404, detail="课程不存在")
    lesson_path = _safe_path(course_path, lesson_name)
    if not os.path.exists(lesson_path):
        raise HTTPException(status_code=404, detail="课件不存在")
    old_path = _safe_path(lesson_path, filename)
    if not os.path.isfile(old_path):
        raise HTTPException(status_code=404, detail="文件不存在")
    new_path = _safe_path(lesson_path, new_name)
    if os.path.exists(new_path):
        raise HTTPException(status_code=400, detail="文件已存在")
    os.rename(old_path, new_path)
    return {"status": "success", "message": f"文件已重命名为 [{new_name}]"}


@router.post("/api/courses/{course_name}/materials/{lesson_name}/save-sb3")
async def save_sb3(
    request: Request, course_name: str, lesson_name: str,
    file_type: str = "main", file: UploadFile = File(...),
):
    caller = require_role(request, ["admin", "teacher"])
    if lesson_name == course_name:
        raise HTTPException(status_code=400, detail="课件名不能与课程名相同")
    base = _get_teacher_course_dir(caller["username"])
    course_path = _safe_path(base, course_name)
    if not os.path.exists(course_path):
        raise HTTPException(status_code=404, detail="课程不存在")
    lesson_path = _safe_path(course_path, lesson_name)
    os.makedirs(lesson_path, exist_ok=True)
    filename = "main.sb3" if file_type == "main" else "student.sb3"
    content = await file.read()
    # 文件大小限制：50MB
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大，最大 50MB")
    with open(os.path.join(lesson_path, filename), "wb") as f:
        f.write(content)
    return {
        "status": "success", "message": f"{filename} 已保存",
        "path": f"/share/{caller['username']}_course/{course_name}/{lesson_name}/"
    }


# ==========================================
# 文件树（资源管理器）：后台所有课程统一使用，支持任意层级嵌套
# ==========================================
def _split_rel(rel: str) -> list:
    """把相对路径拆成安全的路径段，过滤 . / .. """
    rel = (rel or "").strip().strip("/\\")
    if not rel:
        return []
    return [p for p in rel.replace("\\", "/").split("/") if p not in (".", "..")]


def _walk_tree(path: str) -> list:
    entries = []
    try:
        names = sorted(os.listdir(path))
    except OSError:
        return entries
    for name in names:
        if name.startswith(".") or name == "course.json":
            continue
        fp = os.path.join(path, name)
        if os.path.isdir(fp):
            entries.append({"name": name, "type": "dir", "children": _walk_tree(fp)})
        elif os.path.isfile(fp):
            entries.append({"name": name, "type": "file", "size": os.path.getsize(fp)})
    return entries


@router.get("/api/courses/{course_id}/tree")
def get_course_tree(request: Request, course_id: str):
    caller = require_role(request, ["admin", "teacher"])
    course_path = _get_course_path(caller, course_id)
    return {"status": "success", "tree": _walk_tree(course_path)}


@router.post("/api/courses/{course_id}/tree/folder")
def create_tree_folder(request: Request, course_id: str, path: str = ""):
    caller = require_role(request, ["admin", "teacher"])
    course_path = _get_course_path(caller, course_id)
    target = _safe_path(course_path, *_split_rel(path))
    os.makedirs(target, exist_ok=True)
    return {"status": "success", "message": "文件夹已创建", "path": path}


@router.post("/api/courses/{course_id}/tree/upload")
async def upload_tree_file(request: Request, course_id: str, path: str = "", file: UploadFile = File(...)):
    caller = require_role(request, ["admin", "teacher"])
    course_path = _get_course_path(caller, course_id)
    target_dir = _safe_path(course_path, *_split_rel(path))
    os.makedirs(target_dir, exist_ok=True)
    safe_name = os.path.basename(file.filename or "unnamed").lstrip(".")
    if not safe_name:
        raise HTTPException(status_code=400, detail="文件名无效")
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="文件过大，最大 50MB")
    with open(os.path.join(target_dir, safe_name), "wb") as f:
        f.write(content)
    return {"status": "success", "message": f"文件 [{safe_name}] 已上传", "name": safe_name}


@router.post("/api/courses/{course_id}/tree/rename")
def rename_tree_entry(request: Request, course_id: str, form: RenameForm, path: str = ""):
    caller = require_role(request, ["admin", "teacher"])
    course_path = _get_course_path(caller, course_id)
    old_path = _safe_path(course_path, *_split_rel(path))
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


@router.delete("/api/courses/{course_id}/tree")
def delete_tree_entry(request: Request, course_id: str, path: str = ""):
    caller = require_role(request, ["admin", "teacher"])
    course_path = _get_course_path(caller, course_id)
    target = _safe_path(course_path, *_split_rel(path))
    if not os.path.exists(target):
        raise HTTPException(status_code=404, detail="目标不存在")
    if os.path.isdir(target):
        shutil.rmtree(target)
    else:
        os.remove(target)
    return {"status": "success", "message": "已删除"}


# ==========================================
# 电子书发布（vitepress 运行时渲染）
# ==========================================
@router.post("/api/courses/{course_id}/publish")
def publish_course(request: Request, course_id: str):
    caller = require_role(request, ["admin", "teacher"])
    course_path = _get_course_path(caller, course_id)
    meta = _read_course_meta(course_path)
    fmt = _resolve_course_format(meta.get("type", "scratch"), meta.get("format", ""))
    if fmt != "ebook":
        raise HTTPException(status_code=400, detail="只有电子书课程需要渲染")
    from build import queue_publish
    state = queue_publish(_get_teacher_key(caller["username"]), course_id, course_path)
    return {"status": "success", "publish": state}


@router.get("/api/courses/{course_id}/publish/status")
def publish_status(request: Request, course_id: str):
    caller = require_role(request, ["admin", "teacher"])
    course_path = _get_course_path(caller, course_id)
    meta = _read_course_meta(course_path)
    publish = meta.get("publish", {"status": "idle"})
    from build import get_active_status
    live = get_active_status(course_id)
    if live:
        publish = {**publish, **live}
    return {"status": "success", "publish": publish}


# ==========================================
# 课程删除（带 :path 的路由必须放在所有课程子路由之后）
# ==========================================
@router.delete("/api/courses/{course_name:path}/permanent")
def permanent_delete_course(request: Request, course_name: str):
    caller = require_role(request, ["admin", "teacher"])
    base = _get_teacher_course_dir(caller["username"])
    course_path = _safe_path(base, course_name)
    if not os.path.exists(course_path):
        raise HTTPException(status_code=404, detail="课程不存在")
    shutil.rmtree(course_path)
    from build import remove_site
    remove_site(_get_teacher_key(caller["username"]), course_name)
    return {"status": "success", "message": f"课程 [{course_name}] 已永久删除"}


@router.delete("/api/courses/{course_name:path}")
def delete_course(request: Request, course_name: str):
    caller = require_role(request, ["admin", "teacher"])
    base = _get_teacher_course_dir(caller["username"])
    course_path = _safe_path(base, course_name)
    if not os.path.exists(course_path):
        raise HTTPException(status_code=404, detail="课程不存在")
    rel_path = os.path.join("courses", f"{caller['username']}_course", course_name)
    _move_to_trash(course_path, "course", caller["username"], rel_path)
    from build import remove_site
    remove_site(_get_teacher_key(caller["username"]), course_name)
    return {"status": "success", "message": f"课程 [{course_name}] 已移到回收站"}
