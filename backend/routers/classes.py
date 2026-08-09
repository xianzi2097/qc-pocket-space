"""班级 + 学生管理路由"""
import os
import json
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from models import UserModel, SessionLocal, USERS_DIR
from utils import (
    get_db, get_current_user, require_role, _ensure_user,
    _load_classes, _save_classes, _get_classes_path, _resolve_user_dir,
    ClassForm, StudentForm
)

router = APIRouter()


@router.get("/api/classes")
def list_classes(request: Request, db: Session = Depends(get_db)):
    caller = get_current_user(request)
    classes = _load_classes(caller["username"])
    for c in classes:
        # 兼容旧格式：schedule 是 list 则转为 dict
        if isinstance(c.get("schedule"), list):
            c["schedule"] = {"days": c["schedule"], "time": "", "location": ""}
        elif not isinstance(c.get("schedule"), dict):
            c["schedule"] = {"days": [], "time": "", "location": ""}
        enriched = []
        user_ids = [s["user_id"] for s in c.get("students", []) if s.get("user_id")]
        users_map = {}
        if user_ids:
            users_map = {u.id: u for u in db.query(UserModel).filter(UserModel.id.in_(user_ids)).all()}
        for s in c.get("students", []):
            user_id = s.get("user_id")
            if user_id:
                user = users_map.get(user_id)
                entry = {
                    "user_id": user_id,
                    "username": user.username if user else "未知",
                    "name": s.get("name", user.username if user else "未知"),
                }
                # 学生有独立上课日则带上
                if s.get("schedule"):
                    entry["schedule"] = s["schedule"]
                enriched.append(entry)
            else:
                enriched.append(s)
        c["students"] = enriched
    return {"status": "success", "classes": classes}


@router.post("/api/classes")
def create_class(request: Request, form: ClassForm):
    caller = require_role(request, ["teacher"])
    classes = _load_classes(caller["username"])
    if any(c["name"] == form.name for c in classes):
        raise HTTPException(status_code=400, detail="班级已存在")
    classes.append({"name": form.name, "schedule": form.schedule, "students": []})
    _save_classes(caller["username"], classes)
    return {"status": "success", "message": f"班级 [{form.name}] 已创建"}


@router.put("/api/classes/{class_name}")
def update_class(request: Request, class_name: str, form: ClassForm):
    caller = require_role(request, ["teacher"])
    if form.name == class_name:
        # 没改名，只改 schedule
        classes = _load_classes(caller["username"])
        for c in classes:
            if c["name"] == class_name:
                c["schedule"] = form.schedule
                _save_classes(caller["username"], classes)
                return {"status": "success"}
        raise HTTPException(status_code=404, detail="班级不存在")

    classes = _load_classes(caller["username"])
    if any(c["name"] == form.name for c in classes):
        raise HTTPException(status_code=400, detail="目标班级名已存在")

    for c in classes:
        if c["name"] == class_name:
            c["name"] = form.name
            c["schedule"] = form.schedule
            _save_classes(caller["username"], classes)
            # 同步重命名 sessions 目录
            user_dir = _resolve_user_dir(caller["username"])
            if user_dir:
                old_sessions = os.path.join(user_dir, "sessions", class_name)
                new_sessions = os.path.join(user_dir, "sessions", form.name)
                if os.path.isdir(old_sessions):
                    os.rename(old_sessions, new_sessions)
            return {"status": "success", "message": f"班级 [{class_name}] 已改名为 [{form.name}]"}
    raise HTTPException(status_code=404, detail="班级不存在")


@router.delete("/api/classes/{class_name}")
def delete_class(request: Request, class_name: str):
    caller = require_role(request, ["teacher"])
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
    caller = require_role(request, ["teacher"])
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
                entry = {"user_id": user_id, "name": form.name}
                if form.schedule:
                    entry["schedule"] = form.schedule
                c["students"].append(entry)
                _save_classes(caller["username"], classes)
                return {"status": "success", "user_id": user_id}
        raise HTTPException(status_code=404, detail="班级不存在")
    finally:
        db_local.close()


@router.get("/api/classes/find-student")
def find_student(request: Request, name: str = ""):
    require_role(request, ["teacher"])
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
    require_role(request, ["teacher"])
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
    caller = require_role(request, ["teacher"])
    if not student_id.isdigit():
        raise HTTPException(status_code=400, detail="无效的学生ID")
    classes = _load_classes(caller["username"])
    target_id = int(student_id)
    for c in classes:
        if c["name"] == class_name:
            c["students"] = [s for s in c["students"] if s.get("user_id") != target_id]
            _save_classes(caller["username"], classes)
            return {"status": "success"}
    raise HTTPException(status_code=404, detail="班级不存在")


from pydantic import BaseModel as _BaseModel

class _StudentScheduleForm(_BaseModel):
    schedule: dict | None = None  # {"days": ["周六"]} 或 None（恢复默认）

@router.patch("/api/classes/{class_name}/students/{student_id}/schedule")
def update_student_schedule(request: Request, class_name: str, student_id: str, form: _StudentScheduleForm):
    """更新学生的个人上课日"""
    caller = require_role(request, ["teacher"])
    if not student_id.isdigit():
        raise HTTPException(status_code=400, detail="无效的学生ID")
    classes = _load_classes(caller["username"])
    target_id = int(student_id)
    for c in classes:
        if c["name"] == class_name:
            for s in c.get("students", []):
                if s.get("user_id") == target_id:
                    if form.schedule:
                        s["schedule"] = form.schedule
                    else:
                        s.pop("schedule", None)  # 删除个人设置，恢复跟随班级
                    _save_classes(caller["username"], classes)
                    return {"status": "success"}
            raise HTTPException(status_code=404, detail="学生在班级中未找到")
    raise HTTPException(status_code=404, detail="班级不存在")
