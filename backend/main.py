"""FastAPI hub: startup + middleware + route mounting"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
from models import Base, engine, SessionLocal, UserModel
from utils import verify_token, hash_password, self_check_user_dirs


def _ensure_admin():
    db = SessionLocal()
    try:
        if db.query(UserModel).filter(UserModel.username == "admin").first():
            print("[startup] admin already exists")
            return
        user = UserModel(
            username="admin", password=hash_password("111111"),
            role="admin", avatar_path="default.jpg", qc_descriptors="{}"
        )
        db.add(user)
        db.commit()
        print("[startup] admin created (default password, change immediately)")
    except Exception as e:
        db.rollback()
        print(f"[startup] admin create failed: {e}")
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    _ensure_admin()
    self_check_user_dirs()
    # 每日定时自动备份（服务常驻期间每天备份整个 data/，保留最近 N 份）
    try:
        from backup import start_auto_backup
        start_auto_backup()
    except Exception as e:
        print(f"[startup] 自动备份初始化失败（不影响启动）: {e}")
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


# 信任来自 Caddy(127.0.0.1) 的 X-Forwarded-For 头，让 request.client.host 拿到真实客户端 IP。
# 修复登录限流：反代下所有请求都来自 127.0.0.1，原按 IP 计数变成“全校共用一把锁”，
# 任何一个人输错 5 次密码，全校 30 秒登不进来。加了它之后“谁乱试锁谁”。
# 只信 127.0.0.1（Caddy）带来的头，客户端自己伪造的 X-Forwarded-For 一律忽略。
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["127.0.0.1"])


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    print(f"!!! Validation error: {exc.errors()}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})


from routers import auth, users, messages, notes, classes, sessions, courses, resources, trash
app.include_router(auth.router)
app.include_router(users.router)
app.include_router(messages.router)
app.include_router(notes.router)
app.include_router(classes.router)
app.include_router(sessions.router)
app.include_router(courses.router)
app.include_router(resources.router)
app.include_router(trash.router)
