"""账号 —— 规范 §3.2（M1a · 10 个接口）

权限要点（对照规范 §4 矩阵）：
  GET    /api/accounts            公开：未登录只给基本字段；带 token 且 admin 看全量（含已停用）
  POST   /api/accounts            admin 建老师或学生；teacher 只能建学生；角色值仅 teacher/student
  POST   /api/accounts/batch      仅 admin 批量注册（已存在跳过；密码缺省用 DEFAULT_ACCOUNT_PASSWORD）
  GET    /api/accounts/{accountId}        需登录：本人/admin 看全字段，他人只看基本字段
  PATCH  /api/accounts/{accountId}        本人改昵称/描述符；status 仅 admin；不能动别人
  DELETE /api/accounts/{accountId}        仅 admin、不能删自己 → 软删（status=deleted + 进回收站 + 踢下线）
  POST   /api/accounts/{accountId}/password  仅 admin 重置密码（重置即清该账号全部会话）
  POST   /api/accounts/{accountId}/avatar    仅学生本人、≤5MB、必须是图片；磁盘满 → 507/9001
  PUT    /api/accounts/{accountId}/avatar/preset  选 emoji 预设 → 存标记 "avatar_07"（仅本人，任意角色）
  GET    /api/accounts/{accountId}/avatar    只服务自定义图片；标记/文件丢失 → 404（前端画默认 emoji）

头像设计（2026-10-06 方案一）：emoji 库活在前端常量（V1 的 AVATARS，20+ 个），
后端只存标记串——不存预设图片、不维护清单接口（两个真相源必坏其一）。

安全纪律：写操作第一行鉴权（C-20）；密码哈希、路径落盘都走地基，不在此处手写。
"""
import json
import re

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
    E_NOT_FOUND,
    E_PARAM_INVALID,
    E_ROLE_NOT_ALLOWED,
    E_STORAGE_FULL,
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

_VALID_ROLES = {"teacher", "student"}
_IMAGE_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}
_MEDIA_BY_EXT = {"png": "image/png", "jpg": "image/jpeg", "webp": "image/webp"}
# emoji 预设标记（如 "avatar_07"）：清单归前端，后端只认格式
_AVATAR_MARKER_RE = re.compile(r"^avatar_\d{1,2}$")


class AccountCreate(BaseModel):
    username: str = Field(min_length=1, max_length=config.MAX_USERNAME_LEN)
    password: str = Field(min_length=1)
    role: str = Field(min_length=1)
    display_name: str = Field(default="", max_length=64)


class AccountPatch(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=config.MAX_USERNAME_LEN)
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

    # 登录名可改（2026-10-07 拍板）：学生一律不可改（防捣乱），teacher/admin 可改自己，admin 可改任何人
    if body.username is not None:
        if account.role == "student":
            raise ApiError(E_FORBIDDEN, "学生不能修改登录名", 403)
        new_name = body.username.strip()
        if not new_name:
            raise ApiError(E_PARAM_INVALID, "登录名不能为空", 400)
        if new_name != target.username:
            if db.scalar(select(Account).where(Account.username == new_name)):
                raise ApiError(E_USERNAME_TAKEN, "登录名已存在", 409)
            target.username = new_name          # 文件路径都用 id，改名零副作用

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
    try:
        folder.mkdir(parents=True, exist_ok=True)
        for old in folder.glob("avatar.*"):          # 换格式时清掉旧文件，不留孤儿
            old.unlink()
        (folder / f"avatar.{ext}").write_bytes(content)
    except OSError:                                  # 磁盘满 / 权限问题 → 明确报错，不装 500
        raise ApiError(E_STORAGE_FULL, "磁盘空间不足，头像保存失败", 507)

    account.avatar_path = f"users/{account.id}/avatar.{ext}"
    db.commit()
    return ok({"avatar_path": account.avatar_path}, "头像已更新")


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
    if not _AVATAR_MARKER_RE.match(name):          # 只验格式；清单归前端（可取模兜底）
        raise ApiError(E_PARAM_INVALID, "预设头像标记不合法（形如 avatar_07）", 400)

    folder = config.DATA_ROOT / "users" / account.id
    if folder.is_dir():
        for old in folder.glob("avatar.*"):        # 换回预设时清掉自定义文件
            old.unlink()

    account.avatar_path = name                     # 存标记串，不落任何图片
    db.commit()
    return ok({"avatar_path": account.avatar_path}, "头像已更新")


@router.get("/{accountId}/avatar")
def get_avatar(accountId: str, db: DbSession = Depends(get_db)):
    """只服务"自定义上传"的图片；预设标记或文件丢失 → 404，前端据此画 emoji"""
    target = db.get(Account, accountId)
    if target is None:
        raise ApiError(E_ACCOUNT_NOT_FOUND, "账号不存在", 404)

    if _AVATAR_MARKER_RE.match(target.avatar_path):
        raise ApiError(E_NOT_FOUND, "该账号使用预设头像（由前端渲染）", 404)

    path = config.DATA_ROOT / target.avatar_path
    if not path.is_file():
        raise ApiError(E_NOT_FOUND, "头像文件不存在", 404)

    media = _MEDIA_BY_EXT.get(path.suffix.lstrip(".").lower(), "application/octet-stream")
    return FileResponse(path, media_type=media)
