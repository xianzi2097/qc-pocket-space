"""注册 / 登录路由"""
import json
import random
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from models import UserModel
from utils import get_db, get_current_user, require_role, generate_token, ensure_user_dirs, hash_password, verify_password, RegisterForm, LoginForm

router = APIRouter()


@router.post("/api/register")
def register_user(request: Request, form: RegisterForm, db: Session = Depends(get_db)):
    caller = get_current_user(request)
    if form.role == "teacher":
        require_role(request, ["admin"])
    elif form.role == "student":
        require_role(request, ["admin", "teacher"])
    else:
        raise HTTPException(status_code=400, detail="角色只能是 teacher 或 student")
    if db.query(UserModel).filter(UserModel.username == form.username).first():
        raise HTTPException(status_code=400, detail="该账号已存在！")

    avatar_name = f"avatar_{str(random.randint(1, 78)).zfill(2)}.jpg"
    new_user = UserModel(
        username=form.username, password=hash_password(form.password),
        role=form.role, avatar_path=avatar_name, qc_descriptors="{}"
    )
    db.add(new_user)
    db.commit()
    ensure_user_dirs(new_user)
    return {"status": "success", "message": f"账号 [{form.username}] 创建成功！"}


@router.post("/api/login")
def login_user(form: LoginForm, db: Session = Depends(get_db)):
    user = db.query(UserModel).filter(UserModel.username == form.username).first()
    if user and verify_password(form.password, user.password):
        avatar_path = user.avatar_path or "default.jpg"
        if avatar_path.endswith(".png"):
            avatar_path = avatar_path[:-4] + ".jpg"
        try:
            descriptors = json.loads(user.qc_descriptors or "{}")
        except json.JSONDecodeError:
            descriptors = {}
        token = generate_token(user.username, user.role)
        return {
            "status": "success", "message": "登录成功！", "token": token,
            "user": {
                "id": user.id, "username": user.username,
                "role": user.role, "avatar_path": avatar_path,
                "descriptors": descriptors
            }
        }
    raise HTTPException(status_code=401, detail="账号或密码错误")
