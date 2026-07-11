"""课程 + 课件素材路由"""
import os
import json
import shutil
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from utils import (
    get_db, get_current_user, require_role,
    _safe_path, _get_teacher_course_dir, _move_to_trash, COURSE_BASE,
    CourseCreateForm
)

router = APIRouter()


@router.get("/api/courses")
def list_courses(teacher: str = ""):
    course_dir = _get_teacher_course_dir(teacher)
    if not os.path.exists(course_dir):
        return {"status": "success", "courses": []}
    courses = []
    for name in sorted(os.listdir(course_dir)):
        folder_path = os.path.join(course_dir, name)
        if not os.path.isdir(folder_path) or name.startswith("."):
            continue
        meta = {"name": name, "type": "scratch", "description": "", "material_count": 0}
        meta_path = os.path.join(folder_path, "course.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta.update(json.load(f))
            except json.JSONDecodeError:
                pass
        else:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        materials = [
            d for d in os.listdir(folder_path)
            if os.path.isdir(os.path.join(folder_path, d)) and not d.startswith(".") and d != "course.json"
        ]
        meta["material_count"] = len(materials)
        courses.append(meta)
    return {"status": "success", "courses": courses}


@router.post("/api/courses")
def create_course(request: Request, form: CourseCreateForm):
    caller = require_role(request, ["admin", "teacher"])
    course_dir = _get_teacher_course_dir(caller["username"])
    os.makedirs(course_dir, exist_ok=True)
    course_path = os.path.join(course_dir, form.name)
    if os.path.exists(course_path):
        raise HTTPException(status_code=400, detail="课程已存在")
    os.makedirs(course_path)
    meta = {
        "name": form.name, "type": form.course_type, "description": "",
        "created_at": datetime.now().strftime("%Y-%m-%d"), "material_count": 0
    }
    with open(os.path.join(course_path, "course.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return {"status": "success", "message": f"课程 [{form.name}] 已创建", "course": meta}


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
    base = _get_teacher_course_dir(caller["username"])
    course_path = _safe_path(base, course_name)
    if not os.path.exists(course_path):
        raise HTTPException(status_code=404, detail="课程不存在")
    lesson_path = os.path.join(course_path, lesson_name)
    os.makedirs(lesson_path, exist_ok=True)
    content = await file.read()
    with open(os.path.join(lesson_path, file.filename), "wb") as f:
        f.write(content)
    return {
        "status": "success", "message": f"文件 [{file.filename}] 已上传",
        "path": f"/share/{caller['username']}_course/{course_name}/{lesson_name}/"
    }


@router.post("/api/courses/{course_name}/materials/{lesson_name}/save-sb3")
async def save_sb3(
    request: Request, course_name: str, lesson_name: str,
    file_type: str = "main", file: UploadFile = File(...),
):
    caller = require_role(request, ["admin", "teacher"])
    base = _get_teacher_course_dir(caller["username"])
    course_path = _safe_path(base, course_name)
    if not os.path.exists(course_path):
        raise HTTPException(status_code=404, detail="课程不存在")
    lesson_path = os.path.join(course_path, lesson_name)
    os.makedirs(lesson_path, exist_ok=True)
    filename = "main.sb3" if file_type == "main" else "student.sb3"
    content = await file.read()
    with open(os.path.join(lesson_path, filename), "wb") as f:
        f.write(content)
    return {
        "status": "success", "message": f"{filename} 已保存",
        "path": f"/share/{caller['username']}_course/{course_name}/{lesson_name}/"
    }


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
    return {"status": "success", "message": f"课程 [{course_name}] 已移到回收站"}
