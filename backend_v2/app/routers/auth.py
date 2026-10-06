"""认证与会话 —— 规范 §3.1（V2 第一批接口，也是后续所有接口的写法模板）

提供 4 个接口：
  POST /api/auth/login      登录：限流检查 → 验密码 → 发 7 天 token
                            （失败记一笔，错 5 次锁 30 秒）
  POST /api/auth/logout     登出：把这行 token 从库里删掉
  GET  /api/auth/me         我是谁：返回当前账号完整资料（前端拿它判断登录态）
  PATCH /api/auth/password  改密：验原密码 → 改哈希 → 其他设备全部踢下线
                            （PATCH 而非 POST：这是更新不是创建）

模板四步（规范 §8.1，M1 起每个接口都这么写）：
  解析参数 → 第一行鉴权 → 执行逻辑 → return ok(...) 包信封
"""
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from .. import config
from ..db import get_db
from ..envelope import E_BAD_CREDENTIALS, E_PARAM_INVALID, ApiError, ok
from ..models import Account, AuthSession
from ..security import (
    account_self,
    check_login_allowed,
    clear_login_failures,
    client_ip,
    create_session,
    destroy_session,
    get_current_account,
    get_token,
    hash_password,
    record_login_failure,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str
    password: str


class PasswordIn(BaseModel):
    old_password: str = Field(min_length=1)
    new_password: str = Field(min_length=1)


@router.post("/login")
def login(body: LoginIn, request: Request, db: DbSession = Depends(get_db)):
    ip = client_ip(request)
    check_login_allowed(db, ip)

    username = body.username.strip()
    account = db.scalar(select(Account).where(Account.username == username))
    if account is None or account.status != "active" or not verify_password(body.password, account.password_hash):
        record_login_failure(db, ip)
        raise ApiError(E_BAD_CREDENTIALS, "账号或密码错误", 401)

    clear_login_failures(db, ip)
    token = create_session(db, account, ip=ip, user_agent=request.headers.get("user-agent", ""))
    return ok(
        {
            "token": token,
            "expires_in": config.TOKEN_TTL_SECONDS,
            "account": account_self(account),
        },
        "登录成功",
    )


@router.post("/logout")
def logout(
    db: DbSession = Depends(get_db),
    token: str = Depends(get_token),
    _account: Account = Depends(get_current_account),
):
    destroy_session(db, token)
    return ok(None, "已注销")


@router.get("/me")
def me(account: Account = Depends(get_current_account)):
    return ok(account_self(account))


@router.patch("/password")
def change_password(
    body: PasswordIn,
    db: DbSession = Depends(get_db),
    token: str = Depends(get_token),
    account: Account = Depends(get_current_account),
):
    if not verify_password(body.old_password, account.password_hash):
        raise ApiError(E_BAD_CREDENTIALS, "原密码错误", 401)
    if len(body.new_password) < config.MIN_PASSWORD_LEN:
        raise ApiError(E_PARAM_INVALID, f"新密码至少 {config.MIN_PASSWORD_LEN} 位")

    account.password_hash = hash_password(body.new_password)
    # 改密后其他会话全部失效，仅当前会话保留
    db.query(AuthSession).filter(
        AuthSession.account_id == account.id,
        AuthSession.token != token,
    ).delete()
    db.commit()
    return ok(None, "密码已修改")
