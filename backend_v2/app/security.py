"""门禁：验证"你是谁"和"你能不能做这件事"（规范 §1.4）

本文件提供：
  密码
    hash_password / verify_password    PBKDF2 20 万轮加密存储（V1 是裸 sha256）
  会话（token 存库，重启不丢、可踢下线，7 天过期）
    create_session / destroy_session   登录时写一行、登出时删一行
    get_token                          从请求头 Authorization: Bearer xxx 取 token
    get_current_account                token 换账号；过期/停用自动报 401
  接口鉴权（写在接口第一行）
    require_roles("teacher")           角色白名单；admin 不隐式通吃，逐接口声明
    account_self / account_public      本人看全字段 / 公开只看账号名头像（规范 §1.5）
  登录梯度限流（按真实 IP，存库、重启不清零）
    check_login_allowed                查是否在锁定期，锁了报 429
    record_login_failure               记一次失败：5 次锁 30s / 10 次 5min /
                                       15 次 30min / 20 次 2h
    clear_login_failures               登录成功即清零
  其他
    client_ip                          取真实客户端 IP（配合反代头）

依赖注入提示：接口参数写 Depends(get_current_account)，框架会自动走完
"取 token → 查库 → 验过期 → 取账号"，失败自动 401。
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import Depends, Header, Request
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from . import config
from .db import get_db
from .envelope import (
    E_ACCOUNT_DISABLED,
    E_BAD_CREDENTIALS,
    E_NOT_LOGGED_IN,
    E_RATE_LIMITED,
    E_TOKEN_EXPIRED,
    ApiError,
)
from .models import Account, AuthSession, LoginAttempt, now_str

PBKDF2_ITERATIONS = 200_000

# 梯度限流：失败次数 → 锁定时长（秒），与 V1 行为一致
_LOCK_STEPS = [(20, 7200), (15, 1800), (10, 300), (5, 30)]
_FAIL_WINDOW_SECONDS = 7200  # 超过 2 小时无失败记录则重新计数


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), PBKDF2_ITERATIONS)
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    if not stored or not stored.startswith("pbkdf2_sha256$"):
        return False
    parts = stored.split("$", 3)
    if len(parts) != 4:
        return False
    try:
        iterations = int(parts[1])
    except ValueError:
        return False
    salt, expected = parts[2], parts[3]
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt.encode(), iterations)
    return hmac.compare_digest(digest.hex(), expected)


def _expiry_str() -> str:
    return (datetime.now() + timedelta(days=config.TOKEN_TTL_DAYS)).strftime("%Y-%m-%d %H:%M:%S")


def create_session(db: DbSession, account: Account, ip: str = "", user_agent: str = "") -> str:
    token = secrets.token_urlsafe(32)
    db.add(
        AuthSession(
            token=token,
            account_id=account.id,
            ip=ip,
            user_agent=user_agent[:255],
            expires_at=_expiry_str(),
        )
    )
    db.commit()
    return token


def destroy_session(db: DbSession, token: str) -> None:
    db.query(AuthSession).filter(AuthSession.token == token).delete()
    db.commit()


def get_token(
    authorization: str | None = Header(None),
) -> str:
    if not authorization:
        raise ApiError(E_NOT_LOGGED_IN, "未登录", 401)
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise ApiError(E_NOT_LOGGED_IN, "登录已过期，请重新登录", 401)
    return parts[1].strip()


def get_current_account(
    token: str = Depends(get_token),
    db: DbSession = Depends(get_db),
) -> Account:
    session = db.get(AuthSession, token)
    if session is None:
        raise ApiError(E_NOT_LOGGED_IN, "登录已过期，请重新登录", 401)
    if session.expires_at <= now_str():
        destroy_session(db, token)
        raise ApiError(E_TOKEN_EXPIRED, "登录已过期，请重新登录", 401)
    account = db.get(Account, session.account_id)
    if account is None or account.status != "active":
        destroy_session(db, token)
        raise ApiError(E_ACCOUNT_DISABLED, "账号已停用", 401)
    return account


def get_optional_account(
    authorization: str | None = Header(None),
    db: DbSession = Depends(get_db),
) -> Account | None:
    """公开接口用的鉴权：不带头 = 游客(None)；带头了就按正式规则验
    （无效/过期照样 401——不能让前端拿着废 token 误以为还登录着）"""
    if not authorization or not authorization.strip():
        return None
    token = get_token(authorization)
    return get_current_account(token=token, db=db)


def require_roles(*roles: str):
    """handler 第一行依赖：角色白名单（admin 不隐式通吃，逐接口显式声明）"""

    def dep(account: Account = Depends(get_current_account)) -> Account:
        if account.role not in roles:
            raise ApiError(E_NOT_LOGGED_IN, "没有权限", 403)
        return account

    return dep


# ---------------- 登录梯度限流（按真实 IP） ----------------

def _lock_seconds(fail_count: int) -> int:
    for threshold, seconds in _LOCK_STEPS:
        if fail_count >= threshold:
            return seconds
    return 0


def _parse(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")


def check_login_allowed(db: DbSession, ip: str) -> None:
    row = db.scalar(select(LoginAttempt).where(LoginAttempt.ip == ip))
    if row is None or row.fail_count < 5:
        return
    locked_until = _parse(row.last_fail_at) + timedelta(seconds=_lock_seconds(row.fail_count))
    if datetime.now() < locked_until:
        wait = int((locked_until - datetime.now()).total_seconds()) + 1
        raise ApiError(E_RATE_LIMITED, f"登录尝试过于频繁，请 {wait} 秒后再试", 429)


def record_login_failure(db: DbSession, ip: str) -> None:
    row = db.scalar(select(LoginAttempt).where(LoginAttempt.ip == ip))
    if row is None:
        db.add(LoginAttempt(ip=ip, fail_count=1, last_fail_at=now_str()))
    else:
        try:
            expired = (datetime.now() - _parse(row.last_fail_at)).total_seconds() > _FAIL_WINDOW_SECONDS
        except ValueError:
            expired = True
        row.fail_count = 1 if expired else row.fail_count + 1
        row.last_fail_at = now_str()
    db.commit()


def clear_login_failures(db: DbSession, ip: str) -> None:
    db.query(LoginAttempt).filter(LoginAttempt.ip == ip).delete()
    db.commit()


def account_self(account: Account) -> dict:
    """本人可见字段"""
    return {
        "id": account.id,
        "username": account.username,
        "role": account.role,
        "avatar_path": account.avatar_path,
        "descriptors": account.descriptors,
        "status": account.status,
        "created_at": account.created_at,
    }


def account_public(account: Account) -> dict:
    """他人可见字段（公开接口裁剪，不泄漏描述符/状态）"""
    return {
        "id": account.id,
        "username": account.username,
        "role": account.role,
        "avatar_path": account.avatar_path,
    }


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "-"
