"""统一回收站 —— 规范 §3.11（M4 · D10 两套合一 · 4 接口）

三类对象、三种手法（软删时各写过一条 trash item）：
  account → 原表行还在（status=deleted），恢复 = 翻回 active（仅 admin；永不彻底删）
  class   → 行删了、快照在 payload；恢复 = 插回行（成员/课堂记录一直没动 → 自动复原）
            彻底删 = 级联清成员 + 课堂记录（铁律：删对象必清引用）
  asset   → 行删了 + 磁盘搬进 trash/{id}；恢复 = 插回行 + 磁盘搬回 + 排序账原样活着
            彻底删 = 清 file_order + 磁盘物理删 + 发布产物清理（彻底删才清账）
            恢复时校准 publish 状态：产物已清 → done 复位 none（状态诚实，防预览 404）

列表：admin 全部，其余只见 owner_id=自己（owner_id = 对象主人）。
越权按 404 不泄露；恢复冲突沿用原域错误码（班 4001 / 资产 5001）。

路径参数 camelCase（§1.6），前缀 /api 由本 router 统一给。
"""
import shutil

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from .. import config
from ..db import get_db
from ..envelope import (
    E_CLASS_NAME_TAKEN,
    E_COURSE_NAME_TAKEN,
    E_FORBIDDEN,
    E_NOT_FOUND,
    E_PARAM_INVALID,
    E_RESOURCE_NAME_TAKEN,
    ApiError,
    ok,
)
from ..models import (
    Account,
    Asset,
    ClassMember,
    ClassRecord,
    ClassRoom,
    FileOrder,
    Trash,
)
from ..publish import remove_site, site_exists
from ..security import get_current_account

router = APIRouter(prefix="/api/trash", tags=["trash"])

_TYPES = {"account", "class", "asset"}
_CONTENT_TYPES = {"class", "asset"}      # 账号条目只可恢复、永不彻底删


# ---------------- 辅助 ----------------

def _load(db: DbSession, trash_id: str) -> Trash:
    item = db.get(Trash, trash_id)
    if item is None:
        raise ApiError(E_NOT_FOUND, "回收站条目不存在", 404)
    return item


def _require_owner_side(item: Trash, account) -> None:
    """class/asset 条目：主人或 admin；越权按不存在"""
    if account.role != "admin" and item.owner_id != account.id:
        raise ApiError(E_NOT_FOUND, "回收站条目不存在", 404)


def _restore(db: DbSession, item: Trash, account) -> None:
    if item.type == "account":
        if account.role != "admin":
            raise ApiError(E_FORBIDDEN, "账号恢复仅管理员", 403)
        acc = db.get(Account, item.payload.get("id"))
        if acc is None:                       # M1 是状态位软删，行必须在
            raise ApiError(E_NOT_FOUND, "账号数据异常（原行缺失）", 404)
        acc.status = "active"                 # 翻回状态即复活（不牵扯文件）
        db.delete(item)
        db.commit()
        return

    _require_owner_side(item, account)
    if item.type == "class":
        payload = item.payload
        row = ClassRoom(
            id=payload["id"], owner_id=payload["owner_id"], name=payload["name"],
            schedule=payload.get("schedule") or {}, created_at=payload["created_at"],
        )
        db.add(row)
        try:
            db.commit()                       # 成员关系没删过 → 插回行即完整复原
        except IntegrityError:
            db.rollback()
            raise ApiError(E_CLASS_NAME_TAKEN, "班级名称已存在", 409)
        db.delete(item)
        db.commit()
        return

    if item.type == "asset":
        payload = item.payload
        bucket = payload.get("bucket", "")
        taken = db.scalar(select(Asset).where(
            Asset.owner_id == payload["owner_id"], Asset.name == payload["name"]))
        if taken:
            code = E_COURSE_NAME_TAKEN if bucket in ("courses", "books") else E_RESOURCE_NAME_TAKEN
            raise ApiError(code, "名称已存在", 409)

        # publish 状态诚实性：产物已随软删清掉（或历史条目本就没有）→
        # 恢复后不得仍报 done（UI 显示"已发布"但预览 404）
        publish_state = payload.get("publish") or {}
        if publish_state.get("state") == "done" and not site_exists(payload["id"]):
            publish_state = {"state": "none"}

        # 先搬磁盘，再落行；任一步失败都回滚另一半
        src = config.DATA_ROOT / (item.disk_path or f"trash/{payload['id']}")
        dst = config.DATA_ROOT / "assets" / payload["id"]
        moved = False
        if src.exists():
            shutil.rmtree(dst, ignore_errors=True)
            shutil.move(str(src), str(dst))
            moved = True
        try:
            db.add(Asset(
                id=payload["id"], owner_id=payload["owner_id"], bucket=bucket,
                name=payload["name"], description=payload.get("description", ""),
                tags=payload.get("tags") or [], type=payload.get("type", ""),
                format=payload.get("format", ""), publish=publish_state,
                created_at=payload["created_at"],
            ))
            db.delete(item)
            db.commit()
        except Exception:
            db.rollback()
            if moved and dst.exists() and not src.exists():
                shutil.move(str(dst), str(src))   # 磁盘搬回去，保持账货一致
            raise
        return

    raise ApiError(E_PARAM_INVALID, "未知条目类型", 400)


def _purge(db: DbSession, item: Trash) -> None:
    payload = item.payload
    if item.type == "class":
        cid = payload.get("id")
        db.query(ClassMember).filter_by(class_id=cid).delete(synchronize_session=False)
        db.query(ClassRecord).filter_by(class_id=cid).delete(synchronize_session=False)  # 清引用
    elif item.type == "asset":
        aid = payload.get("id")
        db.query(FileOrder).filter_by(asset_id=aid).delete(synchronize_session=False)     # 彻底删才清账
        disk = config.DATA_ROOT / (item.disk_path or f"trash/{aid}")
        shutil.rmtree(disk, ignore_errors=True)
        remove_site(aid)                        # 发布产物同灭（软删漏清的历史条目靠这里兜底）
    db.delete(item)


# ---------------- 4 个接口 ----------------

@router.get("")
def list_trash(
    type: str | None = None,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    if type is not None and type not in _TYPES:
        raise ApiError(E_PARAM_INVALID, "type 只能是 account/class/asset", 400)
    stmt = select(Trash)
    if account.role != "admin":
        stmt = stmt.where(Trash.owner_id == account.id)     # 其余只见自己名下
    if type:
        stmt = stmt.where(Trash.type == type)
    rows = db.scalars(stmt.order_by(Trash.deleted_at.desc(), Trash.id)).all()
    items = [{"id": r.id, "type": r.type, "name": r.name, "owner_id": r.owner_id,
              "disk_path": r.disk_path, "deleted_at": r.deleted_at} for r in rows]
    return ok({"items": items, "total": len(items)})


@router.post("/{trashId}/restore")
def restore(
    trashId: str,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    item = _load(db, trashId)
    _restore(db, item, account)
    return ok(None, "已恢复")


@router.delete("/{trashId}")
def purge(
    trashId: str,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    if account.role != "admin":
        raise ApiError(E_FORBIDDEN, "彻底删除仅管理员", 403)
    item = _load(db, trashId)
    if item.type not in _CONTENT_TYPES:
        raise ApiError(E_FORBIDDEN, "账号条目只可恢复，不提供彻底删除", 403)
    _purge(db, item)
    db.commit()
    return ok(None, "已彻底删除（不可恢复）")


@router.delete("")
def clear(
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    if account.role != "admin":
        raise ApiError(E_FORBIDDEN, "清空仅管理员", 403)
    rows = db.scalars(select(Trash)).all()
    purged = 0
    kept = 0
    for item in rows:
        if item.type in _CONTENT_TYPES:
            _purge(db, item)
            purged += 1
        else:
            kept += 1                        # 账号条目保留（只可恢复）
    db.commit()
    return ok({"purged": purged, "kept": kept},
              f"已清空 {purged} 条，保留账号条目 {kept} 条")
