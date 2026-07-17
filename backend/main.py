"""FastAPI hub: startup + middleware + route mounting"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from models import Base, engine, SessionLocal, UserModel, USERS_DIR
from utils import verify_token, hash_password


def _ensure_admin():
    db = SessionLocal()
    try:
        if db.query(UserModel).filter(UserModel.username == "admin").first():
            print("[startup] admin 已存在")
            return
        user = UserModel(
            username="admin", password=hash_password("111111"),
            role="admin", avatar_path="default.jpg", qc_descriptors="{}"
        )
        db.add(user)
        db.commit()
        print(f"[startup] ✅ admin 已创建 (密码: 111111)")
    except Exception as e:
        print(f"[startup] ❌ admin 创建失败: {e}")
    finally:
        db.close()


def check_orphan_folders():
    db = SessionLocal()
    try:
        db_ids = set()
        for row in db.query(UserModel.id).all():
            db_ids.add(str(row[0]))
        orphans = []
        for role_dir in ["admins", "teachers", "students"]:
            role_path = os.path.join(USERS_DIR, role_dir)
            if not os.path.exists(role_path):
                continue
            for name in os.listdir(role_path):
                p = os.path.join(role_path, name)
                if os.path.isdir(p) and not name.startswith(".") and name not in db_ids:
                    orphans.append((role_dir, name, p))
        if orphans:
            print("[WARNING] Orphan folders found (no DB record):")
            for role_dir, name, path in orphans:
                print(f"  {role_dir}/{name}  ->  {path}")
            print("[i] Background mode, keeping orphan folders")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_admin()
    check_orphan_folders()
    yield


app = FastAPI(lifespan=lifespan)


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    if request.method == "OPTIONS":
        return await call_next(request)
    auth = request.headers.get("authorization")
    user = None
    if auth:
        try:
            user = verify_token(auth)
        except HTTPException:
            pass
    request.state.user = user or {"username": "guest", "role": "guest"}
    return await call_next(request)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    print(f"!!! Validation error: {exc.errors()}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


from routers import auth, users, messages, notes, classes, sessions, courses, trash
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(messages.router)
app.include_router(notes.router)
app.include_router(classes.router)
app.include_router(sessions.router)
app.include_router(courses.router)
app.include_router(trash.router)
