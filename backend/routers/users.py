"""用户管理：资料、头像、密码、描述符"""
import os
import re
import json
from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile, File
from sqlalchemy.orm import Session
from models import UserModel, SessionLocal
from utils import (
    get_db, get_current_user, require_role, get_user_dir, move_to_trash,
    hash_password, verify_password,
    AvatarUpdateForm, ChangePasswordForm, DescriptorsUpdateForm
)

router = APIRouter()


@router.get("/api/users")
def list_all_users(db: Session = Depends(get_db)):
    users = db.query(UserModel).all()
    return {
        "status": "success",
        "users": [{"id": u.id, "username": u.username, "role": u.role, "avatar_path": u.avatar_path} for u in users]
    }


@router.delete("/api/users/{username}")
def delete_user(request: Request, username: str, db: Session = Depends(get_db)):
    require_role(request, ["admin"])
    user = db.query(UserModel).filter(UserModel.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.role == "admin":
        raise HTTPException(status_code=403, detail="不能删除管理员")
    trash_info = move_to_trash(user, db)
    return {"status": "success", "message": f"用户 [{username}] 已移入垃圾桶", "trash": trash_info}


@router.get("/api/user/profile")
def user_profile(request: Request, db: Session = Depends(get_db)):
    caller = get_current_user(request)
    user = db.query(UserModel).filter(UserModel.username == caller["username"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    try:
        descriptors = json.loads(user.qc_descriptors or "{}")
    except json.JSONDecodeError:
        descriptors = {}
    return {
        "status": "success",
        "user": {
            "id": user.id, "username": user.username, "role": user.role,
            "avatar_path": user.avatar_path, "descriptors": descriptors
        }
    }


@router.post("/api/update-avatar")
def update_avatar(request: Request, form: AvatarUpdateForm, db: Session = Depends(get_db)):
    caller = get_current_user(request)
    user = db.query(UserModel).filter(UserModel.username == caller["username"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if form.avatar_path == "custom":
        user.avatar_path = "custom"
    elif re.fullmatch(r"avatar_(0[1-9]|[1-6][0-9]|7[0-8])\.jpg", form.avatar_path):
        user.avatar_path = form.avatar_path
    elif re.fullmatch(r"avatar_\d+", form.avatar_path):
        user.avatar_path = form.avatar_path
    else:
        raise HTTPException(status_code=400, detail="头像文件名不合法")
    db.commit()
    return {"status": "success", "message": "头像更新成功！", "avatar_path": user.avatar_path}


@router.post("/api/upload-avatar")
async def upload_avatar(request: Request, file: UploadFile = File(...), db: Session = Depends(get_db)):
    caller = get_current_user(request)
    user = db.query(UserModel).filter(UserModel.username == caller["username"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.role != "student":
        raise HTTPException(status_code=400, detail="仅学生可上传自定义头像")
    student_dir = get_user_dir(user)
    os.makedirs(student_dir, exist_ok=True)
    contents = await file.read()
    if len(contents) > 2 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="头像文件不能超过 2MB")
    with open(os.path.join(student_dir, "avatar.jpg"), "wb") as f:
        f.write(contents)
    user.avatar_path = "custom"
    db.commit()
    return {"status": "success", "message": "头像上传成功！"}


@router.post("/api/user/change-password")
def change_password(request: Request, form: ChangePasswordForm, db: Session = Depends(get_db)):
    caller = get_current_user(request)
    user = db.query(UserModel).filter(UserModel.username == caller["username"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if not verify_password(form.old_password, user.password):
        raise HTTPException(status_code=400, detail="原密码错误")
    if not form.new_password.strip():
        raise HTTPException(status_code=400, detail="新密码不能为空")
    user.password = hash_password(form.new_password)
    db.commit()
    return {"status": "success", "message": "密码修改成功！"}


@router.post("/api/update-descriptors")
def update_descriptors(request: Request, form: DescriptorsUpdateForm, db: Session = Depends(get_db)):
    caller = get_current_user(request)
    user = db.query(UserModel).filter(UserModel.username == caller["username"]).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    try:
        json.loads(form.qc_descriptors)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="qc_descriptors 不是合法 JSON")
    user.qc_descriptors = form.qc_descriptors
    db.commit()
    return {"status": "success", "message": "描述符更新成功！"}
