"""站内信 —— 规范 §3.7（M3 · L 板块 · 10 接口）

两张表：message_folders（夹，单层 D18）+ messages（信，一行一封）
方案 A：信只存一份挂收件人；"已发送" = 同表按 sender 查（?box=sent）

权限（逐条对应 tests/test_messages.py）：
  读      仅本人的信/夹；详情收发双方可见，陌生人 7004（不泄露存在性）
  发      仅 teacher——admin 也不发（2026-10-09 拍板）；收件人必须在世学生
  改/删   仅收件人；删除真删（响应 msg 带"不可恢复"），单份语义
  夹      内置收件箱：不可改名/不可删（7010）；删夹 = 信退回收件箱（一条 UPDATE）
  读即已读 收件人读 → is_read=true；发件人读自己的已发送 → 不动状态

路径参数 camelCase（§1.6），前缀 /api 由本 router 统一给。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from ..db import get_db
from ..envelope import (
    E_ACCOUNT_NOT_FOUND,
    E_CONFLICT,
    E_FOLDER_PROTECTED,
    E_FORBIDDEN,
    E_MESSAGE_NOT_FOUND,
    E_NOT_FOUND,
    E_PARAM_INVALID,
    ApiError,
    ok,
)
from ..models import Account, Message, MessageFolder, new_id, now_str
from ..security import get_current_account

router = APIRouter(prefix="/api", tags=["messages"])

INBOX_NAME = "收件箱"
_PAGE_SIZE_DEFAULT = 20
_PAGE_SIZE_MAX = 100


# ---------------- 模型 ----------------

class MessageSend(BaseModel):
    receiver_id: str = Field(min_length=1, max_length=12)
    title: str = Field(min_length=1, max_length=255)
    content: str = Field(min_length=1, max_length=200_000)


class MessagePatch(BaseModel):
    folder_id: str | None = Field(default=None, max_length=12)
    is_read: bool | None = None


class FolderCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class FolderPatch(BaseModel):
    name: str = Field(min_length=1, max_length=64)


# ---------------- 公共辅助 ----------------

def _ensure_inbox(db: DbSession, owner_id: str) -> MessageFolder:
    """内置收件箱懒创建：首次收信/首次列夹时自然出现，登录不用预建"""
    inbox = db.scalar(select(MessageFolder).where(
        MessageFolder.owner_id == owner_id, MessageFolder.builtin.is_(True)))
    if inbox is None:
        inbox = MessageFolder(id=new_id(), owner_id=owner_id,
                              name=INBOX_NAME, builtin=True)
        db.add(inbox)
        db.commit()
    return inbox


def _load_message(db: DbSession, message_id: str) -> Message:
    row = db.get(Message, message_id)
    if row is None:
        raise ApiError(E_MESSAGE_NOT_FOUND, "消息不存在", 404)
    return row


def _load_folder(db: DbSession, folder_id: str, owner_id: str) -> MessageFolder:
    row = db.get(MessageFolder, folder_id)
    if row is None or row.owner_id != owner_id:     # 别人的夹按"不存在"报，不泄露
        raise ApiError(E_NOT_FOUND, "文件夹不存在", 404)
    return row


def _check_name_free(db: DbSession, owner_id: str, name: str, exclude_id: str | None = None):
    stmt = select(MessageFolder).where(MessageFolder.owner_id == owner_id,
                                       MessageFolder.name == name)
    if exclude_id:
        stmt = stmt.where(MessageFolder.id != exclude_id)
    if db.scalar(stmt):
        raise ApiError(E_CONFLICT, "文件夹已存在", 409)


def _peer_name(db: DbSession, account_id: str, cache: dict) -> str:
    if account_id not in cache:
        acc = db.get(Account, account_id)
        cache[account_id] = acc.username if acc else ""
    return cache[account_id]


# ---------------- L1/L2/L6 消息列表与发送 ----------------

@router.get("/messages")
def list_messages(
    folder: str | None = None,
    box: str = "inbox",
    page: int = 1,
    page_size: int = _PAGE_SIZE_DEFAULT,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    if box not in ("inbox", "sent"):
        raise ApiError(E_PARAM_INVALID, "box 只能是 inbox 或 sent", 400)
    page = max(1, page)
    page_size = max(1, min(page_size, _PAGE_SIZE_MAX))

    stmt = select(Message)
    if box == "inbox":
        stmt = stmt.where(Message.receiver_id == account.id)
        if folder:
            target = _load_folder(db, folder, account.id)   # 只能筛自己的夹
            stmt = stmt.where(Message.folder_id == target.id)
    else:
        stmt = stmt.where(Message.sender_id == account.id)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Message.created_at.desc(), Message.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()

    cache: dict[str, str] = {}
    items = [{
        "id": r.id,
        "title": r.title,
        "is_read": r.is_read,
        "created_at": r.created_at,
        "folder_id": r.folder_id,
        "peer": _peer_name(db, r.sender_id if box == "inbox" else r.receiver_id, cache),
    } for r in rows]
    return ok({"items": items, "total": total, "page": page, "page_size": page_size})


@router.post("/messages")
def send_message(
    body: MessageSend,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    if account.role != "teacher":
        raise ApiError(E_FORBIDDEN, "只有老师可以发消息", 403)   # admin 也不发
    receiver = db.get(Account, body.receiver_id)
    if receiver is None or receiver.status != "active":
        raise ApiError(E_ACCOUNT_NOT_FOUND, "学生不存在", 404)
    if receiver.role != "student":
        raise ApiError(E_PARAM_INVALID, "只能发给学生", 400)

    inbox = _ensure_inbox(db, receiver.id)
    row = Message(
        id=new_id(),
        sender_id=account.id,
        receiver_id=receiver.id,
        folder_id=inbox.id,
        title=body.title,
        content=body.content,
        is_read=False,
        created_at=now_str(),
    )
    db.add(row)
    db.commit()
    return ok({"id": row.id, "receiver_id": row.receiver_id,
               "title": row.title, "folder_id": row.folder_id,
               "created_at": row.created_at}, "消息发送成功")


@router.get("/messages/unread-count")     # 必须注册在 /messages/{messageId} 之前
def unread_count(
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    count = db.scalar(select(func.count()).where(
        Message.receiver_id == account.id, Message.is_read.is_(False))) or 0
    return ok({"count": count})


# ---------------- L3/L4/L5 单封：读（=已读）/ 改 / 删 ----------------

@router.get("/messages/{messageId}")
def read_message(
    messageId: str,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    row = _load_message(db, messageId)
    is_receiver = row.receiver_id == account.id
    if not is_receiver and row.sender_id != account.id:
        raise ApiError(E_MESSAGE_NOT_FOUND, "消息不存在", 404)    # 陌生人不泄露

    if is_receiver and not row.is_read:
        row.is_read = True                                        # 读 = 自动已读
        db.commit()

    sender = db.get(Account, row.sender_id)
    receiver = db.get(Account, row.receiver_id)
    return ok({
        "id": row.id,
        "sender_id": row.sender_id,
        "sender_username": sender.username if sender else "",
        "receiver_id": row.receiver_id,
        "receiver_username": receiver.username if receiver else "",
        "folder_id": row.folder_id,
        "title": row.title,
        "content": row.content,
        "is_read": row.is_read,
        "created_at": row.created_at,
    })


@router.patch("/messages/{messageId}")
def patch_message(
    messageId: str,
    body: MessagePatch,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    row = _load_message(db, messageId)
    if row.receiver_id != account.id:
        raise ApiError(E_FORBIDDEN, "只有收件人可以操作这封信", 403)

    if body.folder_id is not None:
        target = _load_folder(db, body.folder_id, account.id)    # 只能挪进自己的夹
        row.folder_id = target.id
    if body.is_read is not None:
        row.is_read = body.is_read
    db.commit()
    return ok({"id": row.id, "folder_id": row.folder_id,
               "is_read": row.is_read}, "已更新")


@router.delete("/messages/{messageId}")
def delete_message(
    messageId: str,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    row = _load_message(db, messageId)
    if row.receiver_id != account.id:
        raise ApiError(E_FORBIDDEN, "只有收件人可以删除这封信", 403)
    db.delete(row)                                                # 真删（单份语义：两箱皆无）
    db.commit()
    return ok(None, "已删除（不可恢复）")


# ---------------- L7/L8/L9/L10 文件夹 ----------------

@router.get("/message-folders")
def list_folders(
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    _ensure_inbox(db, account.id)                                  # 登录天然有收件箱
    rows = db.scalars(select(MessageFolder)
                      .where(MessageFolder.owner_id == account.id)
                      .order_by(MessageFolder.builtin.desc(), MessageFolder.name)).all()

    counts = dict(db.execute(
        select(Message.folder_id, func.count())
        .where(Message.receiver_id == account.id)
        .group_by(Message.folder_id)).all())
    unread = dict(db.execute(
        select(Message.folder_id, func.count())
        .where(Message.receiver_id == account.id, Message.is_read.is_(False))
        .group_by(Message.folder_id)).all())

    items = [{"id": f.id, "name": f.name, "builtin": f.builtin,
              "count": counts.get(f.id, 0), "unread": unread.get(f.id, 0)}
             for f in rows]
    total_unread = sum(i["unread"] for i in items)
    return ok({"items": items, "total_unread": total_unread})


@router.post("/message-folders")
def create_folder(
    body: FolderCreate,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    name = body.name.strip()
    if not name:
        raise ApiError(E_PARAM_INVALID, "文件夹名不能为空", 400)
    _check_name_free(db, account.id, name)
    row = MessageFolder(id=new_id(), owner_id=account.id, name=name, builtin=False)
    db.add(row)
    db.commit()
    return ok({"id": row.id, "name": row.name, "builtin": False}, "文件夹已创建")


@router.patch("/message-folders/{folderId}")
def rename_folder(
    folderId: str,
    body: FolderPatch,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    row = _load_folder(db, folderId, account.id)
    if row.builtin:
        raise ApiError(E_FOLDER_PROTECTED, "内置收件箱不可改名", 403)
    name = body.name.strip()
    if not name:
        raise ApiError(E_PARAM_INVALID, "文件夹名不能为空", 400)
    _check_name_free(db, account.id, name, exclude_id=row.id)
    row.name = name                                               # 只改这一行，信件零动（folder_id 指针）
    db.commit()
    return ok({"id": row.id, "name": row.name}, "已重命名")


@router.delete("/message-folders/{folderId}")
def delete_folder(
    folderId: str,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    row = _load_folder(db, folderId, account.id)
    if row.builtin:
        raise ApiError(E_FOLDER_PROTECTED, "内置收件箱不可删除", 403)

    inbox = _ensure_inbox(db, account.id)
    db.query(Message).where(Message.folder_id == row.id).update(
        {"folder_id": inbox.id}, synchronize_session=False)       # 信退收件箱：一条 UPDATE，一封不丢
    db.delete(row)
    db.commit()
    return ok(None, "文件夹已删除，里面的信已退回收件箱")
