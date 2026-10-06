"""账号 —— 规范 §3.2（M1a · 7 个接口：列表/创建/详情/改资料/停用/传头像/取头像）

权限要点（对照规范 §4 矩阵）：
  GET    /api/accounts        公开：未登录只给基本字段；带 token 且 admin 看全量（含已停用）
  POST   /api/accounts        admin 建老师或学生；teacher 只能建学生；角色值仅 teacher/student
  GET    /api/accounts/{id}   需登录：本人/admin 看全字段，他人只看基本字段
  PATCH  /api/accounts/{id}   本人改昵称/描述符；status 仅 admin；不能动别人
  DELETE /api/accounts/{id}   仅 admin、不能删自己 → 软删（status=deleted + 进回收站 + 踢下线）
  POST   /api/accounts/{id}/avatar  仅学生本人、≤2MB、必须是图片
  GET    /api/accounts/{id}/avatar  公开直取（没传过 → 404，前端自行降级）

安全纪律：写操作第一行鉴权（C-20）；密码哈希、路径落盘都走地基，不在此处手写。
"""
import json
import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from .. import config
from ..db import get_db
from ..envelope import (
    E_ACCOUNT_NOT_FOUND,
    E_CANNOT_DELETE_SELF,
    E_FILE_TOO_LARGE,
    E_FORBIDDEN,
    E_PARAM_INVALID,
    E_ROLE_NOT_ALLOWED,
    E_USERNAME_TAKEN,
    ApiError,
    ok,
)
from ..models import Account, AuthSession, Trash, new_id
from ..security import (
    account_public,
    account_self,
    get_current_account,
    get_optional_account,
    get_token,
    hash_password,
)

router = APIRouter(prefix="/api/accounts", tags=["accounts"])
presets_router = APIRouter(prefix="/api/avatars", tags=["accounts"])

_VALID_ROLES = {"teacher", "student"}
_IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_MEDIA_BY_EXT = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp", "svg": "image/svg+xml"}
_PRESET_NAME_RE = re.compile(r"^avatar_\d{2}$")


class AccountCreate(BaseModel):
    username: str = Field(min_length=1, max_length=config.MAX_USERNAME_LEN)
    password: str = Field(min_length=1)
    role: str = Field(min_length=1)
    display_name: str = Field(default="", max_length=64)


class AccountPatch(BaseModel):
    display_name: str | None = Field(default=None, max_length=64)
    descriptors: str | None = None
    status: str | None = None


class BatchRegister(BaseModel):
    """批量注册（启动器导入用）：已存在的用户名跳过，不整体失败"""
    role: str = "student"
    password: str | None = None          # 缺省用 config.DEFAULT_ACCOUNT_PASSWORD
    usernames: list[str] = Field(min_length=1, max_length=200)


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=1)


class PresetChoose(BaseModel):
    preset: str = Field(min_length=1)


def _load(db: DbSession, accountId: str) -> Account:
    target = db.get(Account, accountId)
    if target is None:
        raise ApiError(E_ACCOUNT_NOT_FOUND, "账号不存在", 404)
    return target


@router.get("")
def list_accounts(
    role: str | None = None,
    db: DbSession = Depends(get_db),
    account: Account | None = Depends(get_optional_account),
):
    stmt = select(Account)
    if role:
        stmt = stmt.where(Account.role == role)
    if account is None:                      # 游客只能看到在用账号
        stmt = stmt.where(Account.status == "active")
    rows = db.scalars(stmt.order_by(Account.username)).all()

    full = account is not None and account.role == "admin"
    items = [account_self(row) if full else account_public(row) for row in rows]
    return ok({"items": items, "total": len(items)})


@router.post("")
def create_account(
    body: AccountCreate,
    db: DbSession = Depends(get_db),
    account: Account = Depends(get_current_account),
):
    if body.role not in _VALID_ROLES:
        raise ApiError(E_ROLE_NOT_ALLOWED, "角色只能是 teacher 或 student", 400)
    if not (account.role == "admin" or (account.role == "teacher" and body.role == "student")):
        raise ApiError(E_ROLE_NOT_ALLOWED, "无权创建该角色", 403)

    username = body.username.strip()
    if not username:
        raise ApiError(E_PARAM_INVALID, "用户名不能为空", 400)
    if len(body.password) < config.MIN_PASSWORD_LEN:
        raise ApiError(E_PARAM_INVALID, f"密码至少 {config.MIN_PASSWORD_LEN} 位", 400)
    if db.scalar(select(Account).where(Account.username == username)):
        raise ApiError(E_USERNAME_TAKEN, "用户名已存在", 409)

    created = Account(
        id=new_id(),
        username=username,
        password_hash=hash_password(body.password),
        role=body.role,
        display_name=body.display_name.strip() or username,
    )
    db.add(created)
    db.commit()
    return ok(account_self(created), "创建成功")


@router.post("/batch")
def batch_register(
    body: BatchRegister,
    db: DbSession = Depends(get_db),
    account: Account = Depends(get_current_account),
):
    if account.role != "admin":
        raise ApiError(E_FORBIDDEN, "只有管理员可以批量注册", 403)
    if body.role not in _VALID_ROLES:
        raise ApiError(E_ROLE_NOT_ALLOWED, "角色只能是 teacher 或 student", 400)
    password = body.password or config.DEFAULT_ACCOUNT_PASSWORD
    if len(password) < config.MIN_PASSWORD_LEN:
        raise ApiError(E_PARAM_INVALID, f"密码至少 {config.MIN_PASSWORD_LEN} 位", 400)

    created: list[str] = []
    skipped: list[dict] = []
    seen: set[str] = set()
    for raw in body.usernames:
        username = (raw or "").strip()
        if not username or len(username) > config.MAX_USERNAME_LEN:
            skipped.append({"username": raw, "reason": "用户名不合法"})
        elif username in seen or db.scalar(select(Account).where(Account.username == username)):
            skipped.append({"username": username, "reason": "已存在"})
        else:
            seen.add(username)
            db.add(
                Account(
                    id=new_id(),
                    username=username,
                    password_hash=hash_password(password),
                    role=body.role,
                    display_name=username,
                )
            )
            created.append(username)

    db.commit()
    return ok(
        {"created": len(created), "usernames": created, "skipped": skipped},
        f"批量注册完成：新建 {len(created)} 个，跳过 {len(skipped)} 个",
    )


@router.get("/{accountId}")
def get_account(
    accountId: str,
    db: DbSession = Depends(get_db),
    account: Account = Depends(get_current_account),
):
    target = _load(db, accountId)
    is_self = target.id == account.id
    if target.status != "active" and not (is_self or account.role == "admin"):
        raise ApiError(E_ACCOUNT_NOT_FOUND, "账号不存在", 404)
    return ok(account_self(target) if (is_self or account.role == "admin") else account_public(target))


@router.patch("/{accountId}")
def patch_account(
    accountId: str,
    body: AccountPatch,
    db: DbSession = Depends(get_db),
    account: Account = Depends(get_current_account),
):
    target = _load(db, accountId)
    is_self = target.id == account.id
    if not (is_self or account.role == "admin"):
        raise ApiError(E_FORBIDDEN, "没有权限", 403)

    if body.status is not None:
        if account.role != "admin":
            raise ApiError(E_FORBIDDEN, "只有管理员可以修改状态", 403)
        if body.status not in ("active", "deleted"):
            raise ApiError(E_PARAM_INVALID, "状态只能是 active 或 deleted", 400)
        target.status = body.status

    if body.display_name is not None:
        target.display_name = body.display_name.strip()

    if body.descriptors is not None:
        try:
            json.loads(body.descriptors)
        except (ValueError, TypeError):
            raise ApiError(E_PARAM_INVALID, "描述符必须是合法 JSON", 400)
        target.descriptors = body.descriptors

    db.commit()
    return ok(account_self(target) if (is_self or account.role == "admin") else account_public(target))


@router.delete("/{accountId}")
def delete_account(
    accountId: str,
    db: DbSession = Depends(get_db),
    account: Account = Depends(get_current_account),
):
    if account.role != "admin":
        raise ApiError(E_FORBIDDEN, "只有管理员可以停用账号", 403)

    target = _load(db, accountId)
    if target.status == "deleted":
        raise ApiError(E_ACCOUNT_NOT_FOUND, "账号不存在", 404)
    if target.id == account.id:
        raise ApiError(E_CANNOT_DELETE_SELF, "不能停用自己的账号", 403)

    target.status = "deleted"
    db.add(
        Trash(
            id=new_id(),
            type="account",
            owner_id=target.id,
            name=target.username,
            payload={
                "id": target.id,
                "username": target.username,
                "role": target.role,
                "display_name": target.display_name,
                "avatar_path": target.avatar_path,
                "descriptors": target.descriptors,
                "created_at": target.created_at,
            },
        )
    )
    db.query(AuthSession).filter(AuthSession.account_id == target.id).delete()  # 踢下线
    db.commit()
    return ok(None, "账号已停用")


@router.post("/{accountId}/password")
def reset_password(
    accountId: str,
    body: PasswordReset,
    db: DbSession = Depends(get_db),
    token: str = Depends(get_token),
    account: Account = Depends(get_current_account),
):
    if account.role != "admin":
        raise ApiError(E_FORBIDDEN, "只有管理员可以重置密码", 403)
    target = _load(db, accountId)
    if len(body.new_password) < config.MIN_PASSWORD_LEN:
        raise ApiError(E_PARAM_INVALID, f"密码至少 {config.MIN_PASSWORD_LEN} 位", 400)

    target.password_hash = hash_password(body.new_password)
    sessions = db.query(AuthSession).filter(AuthSession.account_id == target.id)
    if target.id == account.id:            # 重置自己的密码时保留当前会话
        sessions = sessions.filter(AuthSession.token != token)
    sessions.delete()
    db.commit()
    return ok(None, "密码已重置")


@router.post("/{accountId}/avatar")
async def upload_avatar(
    accountId: str,
    file: UploadFile = File(...),
    db: DbSession = Depends(get_db),
    account: Account = Depends(get_current_account),
):
    if account.id != accountId:
        raise ApiError(E_FORBIDDEN, "只能上传自己的头像", 403)
    if account.role != "student":
        raise ApiError(E_FORBIDDEN, "只有学生可以上传自定义头像", 403)
    if file.content_type not in _IMAGE_TYPES:
        raise ApiError(E_PARAM_INVALID, "头像必须是图片（png / jpg / webp）", 400)

    content = await file.read()
    if len(content) > config.AVATAR_MAX_BYTES:
        raise ApiError(E_FILE_TOO_LARGE, "头像不能超过 2MB", 413)

    ext = _IMAGE_TYPES[file.content_type]
    folder = config.DATA_ROOT / "users" / account.id
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("avatar.*"):          # 换格式时清掉旧文件，不留孤儿
        old.unlink()
    path = folder / f"avatar.{ext}"
    path.write_bytes(content)

    account.avatar_path = f"users/{account.id}/avatar.{ext}"
    db.commit()
    return ok({"avatar_path": account.avatar_path}, "头像已更新")


def _resolve_avatar(avatar_path: str):
    """把库里的 avatar_path 解析成真实文件；任何情况都回落到预设（永不 404）"""
    if avatar_path.startswith("preset/"):
        candidate = config.PRESET_AVATAR_DIR / Path(avatar_path).name
        if candidate.is_file():
            return candidate
    else:
        candidate = config.DATA_ROOT / avatar_path
        if candidate.is_file():
            return candidate
    return config.PRESET_AVATAR_DIR / "avatar_01.svg"


@router.put("/{accountId}/avatar/preset")
def choose_preset(
    accountId: str,
    body: PresetChoose,
    db: DbSession = Depends(get_db),
    account: Account = Depends(get_current_account),
):
    if account.id != accountId:
        raise ApiError(E_FORBIDDEN, "只能换自己的头像", 403)

    name = body.preset.strip()
    if not _PRESET_NAME_RE.match(name) or not (config.PRESET_AVATAR_DIR / f"{name}.svg").is_file():
        raise ApiError(E_PARAM_INVALID, "预设头像不存在", 400)

    folder = config.DATA_ROOT / "users" / account.id
    if folder.is_dir():
        for old in folder.glob("avatar.*"):     # 换预设时清掉自定义文件
            old.unlink()

    account.avatar_path = f"preset/{name}.svg"
    db.commit()
    return ok({"avatar_path": account.avatar_path}, "头像已更新")


@presets_router.get("/presets")
def list_presets(_account: Account = Depends(get_current_account)):
    """预设清单由后端目录决定（改文件即生效），前端不写死"""
    names = sorted(p.stem for p in config.PRESET_AVATAR_DIR.glob("avatar_*.svg"))
    items = [{"preset": name} for name in names]
    return ok({"items": items, "total": len(items)})


@router.get("/{accountId}/avatar")
def get_avatar(accountId: str, db: DbSession = Depends(get_db)):
    target = db.get(Account, accountId)
    if target is None:
        raise ApiError(E_ACCOUNT_NOT_FOUND, "账号不存在", 404)

    path = _resolve_avatar(target.avatar_path)
    media = _MEDIA_BY_EXT.get(path.suffix.lstrip(".").lower(), "application/octet-stream")
    return FileResponse(path, media_type=media)
