"""注册 / 登录路由"""
import json
import os
import random
from typing import List, Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from models import UserModel
from utils import get_db, get_current_user, require_role, generate_token, ensure_user_dirs, hash_password, verify_password, ACTIVE_TOKENS, RegisterForm, LoginForm

router = APIRouter()

# ============================================================
#  登录限流：同 IP 5 次失败 → 锁定 30 秒
# ============================================================
import time
from collections import defaultdict

LOGIN_FAILURES: dict[str, list[float]] = defaultdict(list)

# 测试用：重置登录限流状态
def _reset_login_failures():
    LOGIN_FAILURES.clear()

def _check_login_rate(request: Request):
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    failures = [t for t in LOGIN_FAILURES[ip] if now - t < 30]
    LOGIN_FAILURES[ip] = failures
    if len(failures) >= 5:
        raise HTTPException(status_code=429, detail="登录尝试过于频繁，请 30 秒后再试")


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
def login_user(request: Request, form: LoginForm, db: Session = Depends(get_db)):
    _check_login_rate(request)
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
    # 记录失败次数（限流用）
    ip = request.client.host if request.client else "unknown"
    LOGIN_FAILURES[ip].append(time.time())
    raise HTTPException(status_code=401, detail="账号或密码错误")


# ============================================================
#  退出登录
# ============================================================

@router.post("/api/logout")
def logout(request: Request):
    auth = request.headers.get("authorization")
    if auth:
        parts = auth.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            ACTIVE_TOKENS.pop(parts[1], None)
    return {"status": "success", "message": "已退出登录"}


# ============================================================
#  批量注册（供启动器调用）
# ============================================================

class BatchRegisterItem(BaseModel):
    username: str
    password: str
    role: str  # "teacher" or "student"
    teacher: Optional[str] = None  # 学生所属老师

class BatchRegisterForm(BaseModel):
    users: List[BatchRegisterItem]
    default_password: str = "111111"

@router.post("/api/admin/batch-register")
def batch_register(request: Request, form: BatchRegisterForm, db: Session = Depends(get_db)):
    """批量注册老师和学生（启动器专用）"""
    caller = get_current_user(request)
    require_role(request, ["admin"])
    
    results = {"success": 0, "fail": 0, "skip": 0, "details": []}
    
    for item in form.users:
        try:
            # 检查是否已存在
            existing = db.query(UserModel).filter(UserModel.username == item.username).first()
            if existing:
                if existing.role != item.role:
                    results["details"].append({"username": item.username, "status": "skip", "msg": f"已是 {existing.role}"})
                    results["skip"] += 1
                else:
                    results["details"].append({"username": item.username, "status": "skip", "msg": "已存在"})
                    results["skip"] += 1
                continue
            
            # 创建用户
            avatar_name = f"avatar_{str(random.randint(1, 78)).zfill(2)}.jpg"
            qc_desc = json.dumps({"teacher": item.teacher}, ensure_ascii=False) if item.teacher else "{}"
            
            new_user = UserModel(
                username=item.username,
                password=hash_password(item.password or form.default_password),
                role=item.role,
                avatar_path=avatar_name,
                qc_descriptors=qc_desc
            )
            db.add(new_user)
            db.commit()
            ensure_user_dirs(new_user)
            
            results["details"].append({"username": item.username, "status": "success", "msg": "创建成功"})
            results["success"] += 1
            
        except Exception as e:
            db.rollback()
            results["details"].append({"username": item.username, "status": "fail", "msg": str(e)})
            results["fail"] += 1
    
    return {"status": "success", "message": f"批量注册完成：成功 {results['success']}，跳过 {results['skip']}，失败 {results['fail']}", **results}


# ============================================================
#  班级管理（供启动器调用）
# ============================================================
class ClassItem(BaseModel):
    name: str
    students: List[dict] = []  # [{"user_id": 1, "name": "xxx"}]

class SaveClassesForm(BaseModel):
    teacher: str
    classes: List[ClassItem]

def _get_classes_file(teacher: str) -> str:
    """获取班级配置文件路径（统一按用户 id 目录）"""
    from models import SessionLocal, UserModel
    from utils import get_user_dir
    db = SessionLocal()
    try:
        user = db.query(UserModel).filter(UserModel.username == teacher).first()
        if not user:
            raise HTTPException(status_code=404, detail=f"老师 [{teacher}] 不存在")
        teacher_dir = get_user_dir(user)
        os.makedirs(teacher_dir, exist_ok=True)
        return os.path.join(teacher_dir, "classes.json")
    finally:
        db.close()

@router.get("/api/admin/classes/{teacher}")
def get_classes(teacher: str, request: Request, db: Session = Depends(get_db)):
    """获取老师的班级列表"""
    caller = get_current_user(request)
    require_role(request, ["admin"])
    
    classes_file = _get_classes_file(teacher)
    if not os.path.exists(classes_file):
        return {"status": "success", "classes": []}
    
    try:
        with open(classes_file, "r", encoding="utf-8") as f:
            classes = json.load(f)
        return {"status": "success", "classes": classes}
    except Exception:
        return {"status": "success", "classes": []}

@router.post("/api/admin/classes/{teacher}")
def save_classes(teacher: str, request: Request, form: SaveClassesForm, db: Session = Depends(get_db)):
    """保存老师的班级列表"""
    caller = get_current_user(request)
    require_role(request, ["admin"])
    
    classes_file = _get_classes_file(teacher)
    try:
        classes_data = [c.model_dump() for c in form.classes]
        with open(classes_file, "w", encoding="utf-8") as f:
            json.dump(classes_data, f, ensure_ascii=False, indent=2)
        return {"status": "success", "message": f"班级数据已保存"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"保存失败: {e}")
