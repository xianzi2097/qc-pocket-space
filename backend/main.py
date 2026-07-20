"""FastAPI hub: startup + middleware + route mounting"""
import os
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from models import Base, engine, SessionLocal, UserModel
from utils import verify_token, hash_password


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
