"""班级 + 学生管理路由"""
import os
import json
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from models import UserModel, SessionLocal, USERS_DIR
from utils import (
    get_db, get_current_user, require_role, _ensure_user,
    _load_classes, _save_classes, _get_classes_path,
    ClassForm, StudentForm
)

router = APIRouter()


@router.get("/api/classes")
def list_classes(request: Request, db: Session = Depends(get_db)):
    caller = get_current_user(request)
    classes = _load_classes(caller["username"])
    for c in classes:
        enriched = []
        for s in c.get("students", []):
            user_id = s.get("user_id")
            if user_id:
                user = db.query(UserModel).filter(UserModel.id == user_id).first()
                enriched.append({
                    "user_id": user_id,
                    "username": user.username if user else "未知",
                    "name": s.get("name", user.username if user else "未知"),
                })
            else:
                enriched.append(s)
        c["students"] = enriched
    return {"status": "success", "classes": classes}


@router.post("/api/classes")
def create_class(request: Request, form: ClassForm):
    caller = require_role(request, ["admin", "teacher"])
    classes = _load_classes(caller["username"])
    if any(c["name"] == form.name for c in classes):
        raise HTTPException(status_code=400, detail="班级已存在")
    classes.append({"name": form.name, "schedule": form.schedule, "students": []})
    _save_classes(caller["username"], classes)
    return {"status": "success", "message": f"班级 [{form.name}] 已创建"}


@router.put("/api/classes/{class_name}")
def update_class(request: Request, class_name: str, form: ClassForm):
    caller = require_role(request, ["admin", "teacher"])
    classes = _load_classes(caller["username"])
    for c in classes:
        if c["name"] == class_name:
            c["name"] = form.name
            c["schedule"] = form.schedule
            _save_classes(caller["username"], classes)
            return {"status": "success"}
    raise HTTPException(status_code=404, detail="班级不存在")


@router.delete("/api/classes/{class_name}")
def delete_class(request: Request, class_name: str):
    caller = require_role(request, ["admin", "teacher"])
    from utils import TRASH_DIR, _move_to_trash
    classes = _load_classes(caller["username"])
    deleted = None
    for c in classes:
        if c["name"] == class_name:
            deleted = c
            break
    if not deleted:
        raise HTTPException(status_code=404, detail="班级不存在")
    tmp = os.path.join(TRASH_DIR, f"__tmp_{caller['username']}_{class_name}.json")
    os.makedirs(TRASH_DIR, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"class_data": deleted}, f, ensure_ascii=False, indent=2)
    try:
        _move_to_trash(tmp, "class", caller["username"], class_name)
    except Exception:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise HTTPException(status_code=500, detail="移动到回收站失败，班级未删除")
    classes = [c for c in classes if c["name"] != class_name]
    _save_classes(caller["username"], classes)
    return {"status": "success", "message": f"班级 [{class_name}] 已移到回收站"}


@router.post("/api/classes/{class_name}/students")
def add_student(request: Request, class_name: str, form: StudentForm, db: Session = Depends(get_db)):
    caller = require_role(request, ["admin", "teacher"])
    db_local = SessionLocal()
    try:
        classes = _load_classes(caller["username"])
        for c in classes:
            if c["name"] == class_name:
                if form.existing_id:
                    user_id = int(form.existing_id)
                    user = db_local.query(UserModel).filter(UserModel.id == user_id).first()
                    if not user:
                        raise HTTPException(status_code=404, detail="学生不存在")
                else:
                    new_user = _ensure_user(
                        db_local, form.name, "student", "default.jpg",
                        {"teacher": caller["username"]}
                    )
                    user_id = new_user.id
                c["students"].append({"user_id": user_id, "name": form.name})
                _save_classes(caller["username"], classes)
                return {"status": "success", "user_id": user_id}
        raise HTTPException(status_code=404, detail="班级不存在")
    finally:
        db_local.close()


@router.get("/api/classes/find-student")
def find_student(request: Request, name: str = ""):
    require_role(request, ["admin", "teacher"])
    teachers_dir = os.path.join(USERS_DIR, "teachers")
    if not os.path.exists(teachers_dir):
        return {"status": "success", "matches": []}
    db_local = SessionLocal()
    try:
        matches = []
        for t_folder in os.listdir(teachers_dir):
            t_path = os.path.join(teachers_dir, t_folder)
            if not os.path.isdir(t_path):
                continue
            classes_path = os.path.join(t_path, "classes.json")
            if not os.path.exists(classes_path):
                continue
            with open(classes_path, "r", encoding="utf-8") as f:
                try:
                    classes = json.load(f)
                except json.JSONDecodeError:
                    continue
            tid = int(t_folder)
            teacher_user = db_local.query(UserModel).filter(UserModel.id == tid).first()
            teacher_name = teacher_user.username if teacher_user else t_folder
            for c in classes:
                for s in c.get("students", []):
                    if s.get("name") == name and s.get("user_id"):
                        matches.append({
                            "teacher": teacher_name, "class_name": c["name"],
                            "user_id": s["user_id"],
                        })
    finally:
        db_local.close()
    return {"status": "success", "matches": matches}


@router.get("/api/students/list")
def list_all_students(request: Request):
    require_role(request, ["admin", "teacher"])
    students_dir = os.path.join(USERS_DIR, "students")
    if not os.path.exists(students_dir):
        return {"status": "success", "students": []}
    result = []
    for folder in sorted(os.listdir(students_dir), key=lambda x: int(x) if x.isdigit() else 0):
        info_path = os.path.join(students_dir, folder, "info.json")
        if not os.path.exists(info_path):
            continue
        try:
            with open(info_path, "r", encoding="utf-8") as f:
                info = json.load(f)
            result.append({
                "user_id": int(folder),
                "username": info.get("username", ""),
                "avatar_path": info.get("avatar_path", "default.jpg"),
            })
        except (json.JSONDecodeError, ValueError):
            continue
    return {"status": "success", "students": result}


@router.delete("/api/classes/{class_name}/students/{student_id}")
def remove_student(request: Request, class_name: str, student_id: str):
    caller = require_role(request, ["admin", "teacher"])
    classes = _load_classes(caller["username"])
    target_id = int(student_id)
    for c in classes:
        if c["name"] == class_name:
            c["students"] = [s for s in c["students"] if s.get("user_id") != target_id]
            _save_classes(caller["username"], classes)
            return {"status": "success"}
    raise HTTPException(status_code=404, detail="班级不存在")
