"""总装配：把 app/ 里的零件拼成一个能跑的服务（启动总开关）

启动：cd backend_v2 && python -m uvicorn main:app --port 8001
开发期 V1 照常跑 8000，互不干扰。

启动时依次做三件事（lifespan）：
  ① config.ensure_dirs()   创建数据目录 data_v2/{users,courses,resources,trash}
  ② init_db()              建 15 张表（models.py）
  ③ _ensure_admin()        没有 admin 就种一个：admin / 111111（提示立即改密）

装配四件事：
  install_error_handlers(app)             挂统一信封的 4 个错误处理器（envelope.py）
  ProxyHeadersMiddleware(仅信 127.0.0.1)  信任本机反代传来的真实 IP，限流才准
  app.include_router(...)                 挂各域接口（现：auth、accounts；M1b~M4 继续加）
  GET /api/health                         健康检查，前端/部署探活用
"""
import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy import select
from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

from app import config
from app.db import SessionLocal, init_db
from app.envelope import install_error_handlers, ok
from app.models import Account, new_id
from app.routers import accounts, auth, classes
from app.security import hash_password

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("codenova.v2")

DEFAULT_ADMIN_PASSWORD = "111111"


def _ensure_admin() -> None:
    """启动自检：不存在 admin 则按默认密码种子（提示立即修改）"""
    db = SessionLocal()
    try:
        if db.scalar(select(Account).where(Account.username == "admin")):
            logger.info("admin already exists")
            return
        db.add(
            Account(
                id=new_id(),
                username="admin",
                password_hash=hash_password(DEFAULT_ADMIN_PASSWORD),
                role="admin",
                display_name="管理员",
            )
        )
        db.commit()
        logger.warning("admin created with default password (%s) — change it now", DEFAULT_ADMIN_PASSWORD)
    except Exception:
        db.rollback()
        logger.exception("admin seed failed")
        raise
    finally:
        db.close()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    config.ensure_dirs()
    init_db()
    _ensure_admin()
    yield


app = FastAPI(
    title="CodeNova API V2",
    version="2.0.0",
    description="少儿编程教学平台后端 V2（规范见 docs/backend-v2-api-spec.md）",
    lifespan=lifespan,
)

install_error_handlers(app)

# 只信来自本机反代（Caddy）的真实 IP 头，登录限流才不会"全校共用一把锁"
app.add_middleware(ProxyHeadersMiddleware, trusted_hosts=["127.0.0.1"])

app.include_router(auth.router)
app.include_router(accounts.router)
app.include_router(classes.router)


@app.get("/api/health", tags=["health"])
def health():
    return ok({"status": "up", "version": "2.0.0"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="127.0.0.1", port=int(os.environ.get("QC_V2_PORT", "8001")))
