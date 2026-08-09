"""课堂记录路由"""
import os
import json
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from models import UserModel, SessionLocal, USERS_DIR
from utils import (
    get_db, get_current_user, require_role,
    _resolve_user_dir, _load_sessions, _save_sessions,
    SessionForm
)

router = APIRouter()


@router.post("/api/sessions/{class_name}")
def save_session(request: Request, class_name: str, form: SessionForm):
    caller = require_role(request, ["teacher"])
    month = form.date[:7]
    sessions = _load_sessions(caller["username"], class_name, month)
    new_record = {
        "date": form.date,
        "students": [s.model_dump() for s in form.students]
    }
    for i, s in enumerate(sessions):
        if s["date"] == form.date:
            sessions[i] = new_record
            break
    else:
        sessions.append(new_record)
    sessions.sort(key=lambda x: x["date"], reverse=True)
    _save_sessions(caller["username"], class_name, month, sessions)
    return {"status": "success", "message": f"{form.date} 课堂记录已保存"}


@router.get("/api/sessions/{class_name}")
def list_sessions(request: Request, class_name: str, month: str = ""):
    caller = get_current_user(request)
    if month:
        return {"status": "success", "sessions": _load_sessions(caller["username"], class_name, month)}
    user_dir = _resolve_user_dir(caller["username"])
    if not user_dir:
        return {"status": "success", "sessions": []}
    sessions_dir = os.path.join(user_dir, "sessions", class_name)
    if not os.path.exists(sessions_dir):
        return {"status": "success", "sessions": []}
    all_sessions = []
    for fname in sorted(os.listdir(sessions_dir), reverse=True):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(sessions_dir, fname), "r", encoding="utf-8") as f:
                all_sessions.extend(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    all_sessions.sort(key=lambda x: x["date"], reverse=True)
    return {"status": "success", "sessions": all_sessions}


# 注意：必须在 /api/sessions/{class_name}/{date} 之前定义
@router.get("/api/sessions/student/{student_name}")
def list_student_sessions(request: Request, student_name: str):
    """全局扫描所有老师的课堂记录"""
    require_role(request, ["teacher"])
    teachers_dir = os.path.join(USERS_DIR, "teachers")
    if not os.path.exists(teachers_dir):
        return {"status": "success", "sessions": []}
    db_local = SessionLocal()
    try:
        results = []
        for t_folder in os.listdir(teachers_dir):
            if not t_folder.isdigit():
                continue
            tid = int(t_folder)
            teacher_user = db_local.query(UserModel).filter(UserModel.id == tid).first()
            if not teacher_user:
                continue
            teacher_name = teacher_user.username
            sessions_root = os.path.join(teachers_dir, t_folder, "sessions")
            if not os.path.exists(sessions_root):
                continue
            for class_name in sorted(os.listdir(sessions_root)):
                class_dir = os.path.join(sessions_root, class_name)
                if not os.path.isdir(class_dir):
                    continue
                for fname in sorted(os.listdir(class_dir), reverse=True):
                    if not fname.endswith(".json"):
                        continue
                    try:
                        with open(os.path.join(class_dir, fname), "r", encoding="utf-8") as f:
                            month_sessions = json.load(f)
                    except (json.JSONDecodeError, OSError):
                        continue
                    for s in month_sessions:
                        for stu in s.get("students", []):
                            if stu.get("name") == student_name:
                                results.append({
                                    "date": s["date"], "teacher": teacher_name, "class": class_name,
                                    "status": stu.get("status", "present"),
                                    "content": stu.get("content", ""),
                                    "remark": stu.get("remark", ""),
                                    "next_plan": stu.get("next_plan", ""),
                                })
                                break
        results.sort(key=lambda x: x["date"], reverse=True)
        return {"status": "success", "sessions": results}
    finally:
        db_local.close()


@router.get("/api/sessions/{class_name}/{date}")
def get_session(request: Request, class_name: str, date: str):
    caller = get_current_user(request)
    month = date[:7]
    sessions = _load_sessions(caller["username"], class_name, month)
    for s in sessions:
        if s["date"] == date:
            return {"status": "success", "session": s}
    raise HTTPException(status_code=404, detail=f"{date} 没有课堂记录")


@router.delete("/api/sessions/{class_name}/{date}")
def delete_session(request: Request, class_name: str, date: str):
    caller = require_role(request, ["teacher"])
    month = date[:7]
    sessions = _load_sessions(caller["username"], class_name, month)
    filtered = [s for s in sessions if s["date"] != date]
    if len(filtered) == len(sessions):
        raise HTTPException(status_code=404, detail=f"{date} 没有课堂记录")
    _save_sessions(caller["username"], class_name, month, filtered)
    return {"status": "success", "message": f"{date} 课堂记录已删除"}
