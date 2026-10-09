"""课堂记录 —— 规范 §3.9（M3 · R 板块 · 4 接口）

一张表 class_records：复合主键 (class_id, date)，同班同日保存即覆盖（幂等）
可见范围：班主任 / 班成员 / admin；其他人按"班级不存在"404 不泄露
写：仅班主任或 admin（别的老师 403）

路径参数 camelCase（§1.6），前缀 /api 由本 router 统一给。
"""
import re

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from ..db import get_db
from ..envelope import (
    E_CLASS_NOT_FOUND,
    E_FORBIDDEN,
    E_NOT_FOUND,
    E_PARAM_INVALID,
    ApiError,
    ok,
)
from ..models import Account, ClassMember, ClassRecord, ClassRoom
from ..security import get_current_account

router = APIRouter(prefix="/api", tags=["class-records"])

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MONTH_RE = re.compile(r"^\d{4}-\d{2}$")


class RecordPut(BaseModel):
    content: str = Field(default="", max_length=200_000)


# ---------------- 公共辅助 ----------------

def _check_date(date: str) -> str:
    if not _DATE_RE.match(date or ""):
        raise ApiError(E_PARAM_INVALID, "日期格式应为 YYYY-MM-DD", 400)
    return date


def _load_class(db: DbSession, class_id: str) -> ClassRoom:
    cls = db.get(ClassRoom, class_id)
    if cls is None:
        raise ApiError(E_CLASS_NOT_FOUND, "班级不存在", 404)
    return cls


def _require_read(db: DbSession, cls: ClassRoom, account) -> None:
    """读：班主任 / 成员 / admin；其他人按不存在报（不泄露班级存在性）"""
    if account.role == "admin" or cls.owner_id == account.id:
        return
    is_member = db.scalar(select(ClassMember.id).where(
        ClassMember.class_id == cls.id, ClassMember.account_id == account.id))
    if not is_member:
        raise ApiError(E_CLASS_NOT_FOUND, "班级不存在", 404)


def _require_write(cls: ClassRoom, account) -> None:
    if account.role != "admin" and cls.owner_id != account.id:
        raise ApiError(E_FORBIDDEN, "只有班主任可以记录课堂", 403)


def _visible_class_ids(db: DbSession, account) -> list[str] | None:
    """当前账号可见的班级 id；admin → None 表示全部"""
    if account.role == "admin":
        return None
    if account.role == "teacher":
        return list(db.scalars(select(ClassRoom.id).where(ClassRoom.owner_id == account.id)))
    return list(db.scalars(select(ClassMember.class_id)
                           .where(ClassMember.account_id == account.id)))


# ---------------- 4 个接口 ----------------

@router.get("/class-records")
def list_records(
    class_id: str | None = None,
    month: str | None = None,
    student: str | None = None,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    if month and not _MONTH_RE.match(month):
        raise ApiError(E_PARAM_INVALID, "month 格式应为 YYYY-MM", 400)

    scope: list[str] | None
    if class_id:
        cls = _load_class(db, class_id)
        _require_read(db, cls, account)
        scope = [cls.id]
    elif student:
        if account.role not in ("teacher", "admin"):
            raise ApiError(E_FORBIDDEN, "只有老师可以按学生检索", 403)
        stu = db.get(Account, student)
        if stu is None or stu.role != "student":
            raise ApiError(E_NOT_FOUND, "学生不存在", 404)
        stu_classes = list(db.scalars(select(ClassMember.class_id)
                                      .where(ClassMember.account_id == stu.id)))
        visible = _visible_class_ids(db, account)
        scope = stu_classes if visible is None else [c for c in stu_classes if c in set(visible)]
    else:
        scope = _visible_class_ids(db, account)      # None = admin 全部

    stmt = select(ClassRecord)
    if scope is not None:
        stmt = stmt.where(ClassRecord.class_id.in_(scope or [""]))
    if month:
        stmt = stmt.where(ClassRecord.date.startswith(month))
    rows = db.scalars(stmt.order_by(ClassRecord.date.desc(),
                                    ClassRecord.class_id.asc())).all()
    items = [{"class_id": r.class_id, "date": r.date, "content": r.content,
              "updated_at": r.updated_at} for r in rows]
    return ok({"items": items, "total": len(items)})


@router.get("/class-records/{classId}/{date}")
def get_record(
    classId: str,
    date: str,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    _check_date(date)
    cls = _load_class(db, classId)
    _require_read(db, cls, account)
    row = db.get(ClassRecord, (classId, date))
    if row is None:
        raise ApiError(E_NOT_FOUND, "记录不存在", 404)
    return ok({"class_id": row.class_id, "date": row.date,
               "content": row.content, "updated_at": row.updated_at})


@router.put("/class-records/{classId}/{date}")
def save_record(
    classId: str,
    date: str,
    body: RecordPut,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    _check_date(date)
    cls = _load_class(db, classId)
    _require_write(cls, account)

    row = db.get(ClassRecord, (classId, date))       # 复合主键：同日已存在
    if row is None:
        row = ClassRecord(class_id=classId, date=date,
                          content=body.content, updated_at=_now())
        db.add(row)
    else:
        row.content = body.content                   # 保存即覆盖（幂等）
        row.updated_at = _now()
    db.commit()
    return ok({"class_id": row.class_id, "date": row.date,
               "content": row.content, "updated_at": row.updated_at}, "已保存")


@router.delete("/class-records/{classId}/{date}")
def delete_record(
    classId: str,
    date: str,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    _check_date(date)
    cls = _load_class(db, classId)
    _require_write(cls, account)
    row = db.get(ClassRecord, (classId, date))
    if row is None:
        raise ApiError(E_NOT_FOUND, "记录不存在", 404)
    db.delete(row)
    db.commit()
    return ok(None, "已删除（不可恢复）")


def _now() -> str:
    from ..models import now_str
    return now_str()
