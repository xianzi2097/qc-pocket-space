"""班级与学生 —— 规范 §3.3（M1b · 8 个接口 + 1 个详情）

可见范围（第一道门）
  teacher → 只见/只管自己的班；admin → 全部；student → 自己所在的班

业务规则（第二道门，逐条对应 tests/test_classes.py）
  · 建班重名按老师隔离：A 老师的"一班"≠ B 老师的"一班"
  · 删班 = 删班级行 + 进回收站（trash 表留快照）；成员关系保留（恢复即复原）
  · 加学生二选一：挂接已有账号 / 现场新建学生号；重复入班 409；只能加学生角色
  · 移出班级只删关系，不删账号
  · 跨班搜学生并入 GET /api/students?q=（V1 的两个 search 端点合并）
  · 学生个人上课日已砍（2026-10-07：V1 有此功能但暂不用，等真需要再写回）

路径参数一律 camelCase（规范 §1.6），前缀 /api 由本 router 统一给。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from .. import config
from ..db import get_db
from ..envelope import (
    E_ACCOUNT_NOT_FOUND,
    E_CLASS_NAME_TAKEN,
    E_CLASS_NOT_FOUND,
    E_FORBIDDEN,
    E_NOT_FOUND,
    E_PARAM_INVALID,
    E_STUDENT_ALREADY_IN_CLASS,
    ApiError,
    ok,
)
from ..models import Account, ClassMember, ClassRoom, Trash, new_id
from ..security import account_member, account_public, get_current_account, hash_password

router = APIRouter(prefix="/api", tags=["classes"])


class ClassCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    schedule: dict | None = None


class ClassPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=64)
    schedule: dict | None = None          # 传 null 视为"不改"


class StudentAdd(BaseModel):
    account_id: str | None = None         # 方式一：挂接已有账号
    username: str | None = Field(default=None, max_length=config.MAX_USERNAME_LEN)
    password: str | None = None           # 方式二：现场建号时必填# ---------------- 公共辅助 ----------------

def _load_class(db: DbSession, class_id: str) -> ClassRoom:
    cls = db.get(ClassRoom, class_id)
    if cls is None:
        raise ApiError(E_CLASS_NOT_FOUND, "班级不存在", 404)
    return cls


def _require_owner(db: DbSession, cls: ClassRoom, account: Account) -> None:
    """第一道门：只有本班老师（或 admin）能动这个班"""
    if account.role != "admin" and cls.owner_id != account.id:
        raise ApiError(E_FORBIDDEN, "没有权限", 403)


def _owner_names(db: DbSession, owner_ids: set[str]) -> dict[str, str]:
    if not owner_ids:
        return {}
    rows = db.scalars(select(Account).where(Account.id.in_(owner_ids))).all()
    return {row.id: row.username for row in rows}


def _class_dict(db: DbSession, cls: ClassRoom, owner_names: dict[str, str] | None = None,
                counts: dict[str, int] | None = None) -> dict:
    data = {
        "id": cls.id,
        "name": cls.name,
        "owner_id": cls.owner_id,
        "owner_name": (owner_names or _owner_names(db, {cls.owner_id})).get(cls.owner_id, ""),
        "schedule": cls.schedule or {},
        "created_at": cls.created_at,
        "student_count": (counts or {}).get(cls.id, 0),
    }
    return data


def _member_ids(db: DbSession, account_id: str) -> list[str]:
    return list(db.scalars(select(ClassMember.class_id).where(ClassMember.account_id == account_id)).all())


# ---------------- 班级 ----------------

@router.get("/classes")
def list_classes(
    db: DbSession = Depends(get_db),
    account: Account = Depends(get_current_account),
):
    if account.role == "admin":
        classes = db.scalars(select(ClassRoom).order_by(ClassRoom.created_at.desc())).all()
    elif account.role == "student":
        member_of = _member_ids(db, account.id)
        classes = []
        if member_of:
            classes = db.scalars(
                select(ClassRoom).where(ClassRoom.id.in_(member_of)).order_by(ClassRoom.created_at.desc())
            ).all()
    else:  # teacher
        classes = db.scalars(
            select(ClassRoom).where(ClassRoom.owner_id == account.id).order_by(ClassRoom.created_at.desc())
        ).all()

    counts = dict(
        db.execute(
            select(ClassMember.class_id, func.count()).group_by(ClassMember.class_id)
        ).all()
    )
    names = _owner_names(db, {c.owner_id for c in classes})
    items = [_class_dict(db, c, names, counts) for c in classes]
    return ok({"items": items, "total": len(items)})


@router.post("/classes")
def create_class(
    body: ClassCreate,
    db: DbSession = Depends(get_db),
    account: Account = Depends(get_current_account),
):
    if account.role not in ("teacher", "admin"):
        raise ApiError(E_FORBIDDEN, "只有老师可以建班", 403)

    name = body.name.strip()
    if not name:
        raise ApiError(E_PARAM_INVALID, "班级名不能为空", 400)
    exists = db.scalar(
        select(ClassRoom).where(ClassRoom.owner_id == account.id, ClassRoom.name == name)
    )
    if exists:
        raise ApiError(E_CLASS_NAME_TAKEN, "班级名称已存在", 409)

    created = ClassRoom(id=new_id(), owner_id=account.id, name=name, schedule=body.schedule or {})
    db.add(created)
    try:
        db.commit()
    except IntegrityError:               # 并发同名（唯一约束兜底，见 C-24）
        db.rollback()
        raise ApiError(E_CLASS_NAME_TAKEN, "班级名称已存在", 409)
    db.refresh(created)
    return ok(_class_dict(db, created), "班级已创建")


@router.get("/classes/{classId}")
def get_class(
    classId: str,
    db: DbSession = Depends(get_db),
    account: Account = Depends(get_current_account),
):
    cls = _load_class(db, classId)
    is_member = bool(
        db.scalar(
            select(ClassMember.id).where(ClassMember.class_id == cls.id,
                                         ClassMember.account_id == account.id)
        )
    )
    if not (account.role == "admin" or cls.owner_id == account.id or is_member):
        raise ApiError(E_FORBIDDEN, "没有权限", 403)

    members = db.execute(
        select(ClassMember, Account)
        .join(Account, Account.id == ClassMember.account_id)
        .where(ClassMember.class_id == cls.id)
        .order_by(Account.username)
    ).all()
    students = [
        {
            **account_member(acc),          # 含档案三字段（teacher/admin 视图）
            "joined_at": member.joined_at,
        }
        for member, acc in members
    ]
    data = _class_dict(db, cls)
    data["students"] = students
    return ok(data)


@router.patch("/classes/{classId}")
def patch_class(
    classId: str,
    body: ClassPatch,
    db: DbSession = Depends(get_db),
    account: Account = Depends(get_current_account),
):
    cls = _load_class(db, classId)
    _require_owner(db, cls, account)

    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise ApiError(E_PARAM_INVALID, "班级名不能为空", 400)
        clash = db.scalar(
            select(ClassRoom).where(ClassRoom.owner_id == cls.owner_id,
                                    ClassRoom.name == name,
                                    ClassRoom.id != cls.id)
        )
        if clash:
            raise ApiError(E_CLASS_NAME_TAKEN, "班级名称已存在", 409)
        cls.name = name

    if body.schedule is not None:
        cls.schedule = body.schedule

    db.commit()
    return ok(_class_dict(db, cls), "班级已更新")


@router.delete("/classes/{classId}")
def delete_class(
    classId: str,
    db: DbSession = Depends(get_db),
    account: Account = Depends(get_current_account),
):
    if account.role not in ("teacher", "admin"):
        raise ApiError(E_FORBIDDEN, "只有老师可以删班", 403)
    cls = _load_class(db, classId)
    _require_owner(db, cls, account)

    db.add(
        Trash(
            id=new_id(),
            type="class",
            owner_id=cls.owner_id,            # 归属 = 班主任（列表按主人筛；班 id 在快照里）
            name=cls.name,
            payload={
                "id": cls.id,
                "owner_id": cls.owner_id,
                "name": cls.name,
                "schedule": cls.schedule,
                "created_at": cls.created_at,
            },
        )
    )
    db.delete(cls)                            # 班级行删除；class_members 保留（恢复即复原）
    db.commit()
    return ok(None, "班级已移入回收站")


# ---------------- 学生 ----------------

def _require_class_owner(db: DbSession, classId: str, account: Account) -> ClassRoom:
    cls = _load_class(db, classId)
    _require_owner(db, cls, account)
    return cls


def _resolve_student(db: DbSession, body: StudentAdd) -> Account:
    """挂接已有账号 或 现场新建，返回学生账号"""
    if body.account_id:
        acc = db.get(Account, body.account_id)
        if acc is None or acc.status != "active":
            raise ApiError(E_ACCOUNT_NOT_FOUND, "账号不存在", 404)
        if acc.role != "student":
            raise ApiError(E_PARAM_INVALID, "只能添加学生账号", 400)
        return acc

    username = (body.username or "").strip()
    if not username:
        raise ApiError(E_PARAM_INVALID, "缺少 account_id 或 username", 400)

    acc = db.scalar(select(Account).where(Account.username == username))
    if acc is not None:
        if acc.status != "active":
            raise ApiError(E_PARAM_INVALID, "账号不存在", 404)
        if acc.role != "student":
            raise ApiError(E_PARAM_INVALID, "只能添加学生账号", 400)
        return acc

    if not body.password or len(body.password) < config.MIN_PASSWORD_LEN:
        raise ApiError(E_PARAM_INVALID, f"新账号需要至少 {config.MIN_PASSWORD_LEN} 位密码", 400)

    acc = Account(
        id=new_id(),
        username=username,
        password_hash=hash_password(body.password),
        role="student",
    )
    db.add(acc)
    db.flush()                                # 拿到 id，供下面写成员关系
    return acc


@router.post("/classes/{classId}/students")
def add_student(
    classId: str,
    body: StudentAdd,
    db: DbSession = Depends(get_db),
    account: Account = Depends(get_current_account),
):
    cls = _require_class_owner(db, classId, account)     # 先判归属，再动任何数据
    student = _resolve_student(db, body)

    already = db.scalar(
        select(ClassMember).where(ClassMember.class_id == cls.id,
                                  ClassMember.account_id == student.id)
    )
    if already:
        raise ApiError(E_STUDENT_ALREADY_IN_CLASS, "学生已在班内", 409)

    member = ClassMember(class_id=cls.id, account_id=student.id)
    db.add(member)
    try:
        db.commit()
    except IntegrityError:                              # 并发重复入班兜底
        db.rollback()
        raise ApiError(E_STUDENT_ALREADY_IN_CLASS, "学生已在班内", 409)

    return ok(
        {
            "class_id": cls.id,
            "student": {"id": student.id, "username": student.username,
                        "avatar_path": student.avatar_path},
            "joined_at": member.joined_at,
        },
        "学生已加入班级",
    )


@router.delete("/classes/{classId}/students/{accountId}")
def remove_student(
    classId: str,
    accountId: str,
    db: DbSession = Depends(get_db),
    account: Account = Depends(get_current_account),
):
    cls = _require_class_owner(db, classId, account)
    member = db.scalar(
        select(ClassMember).where(ClassMember.class_id == cls.id,
                                  ClassMember.account_id == accountId)
    )
    if member is None:
        raise ApiError(E_NOT_FOUND, "学生不在该班级", 404)     # 班在、人不在 → 通用 1004

    db.delete(member)                                  # 只删关系，账号原样保留
    db.commit()
    return ok(None, "学生已移出班级")


# ---------------- 全体学生（V1 的 students/list 与 find-student 合并于此） ----------------

@router.get("/students")
def list_students(
    q: str | None = None,
    db: DbSession = Depends(get_db),
    account: Account = Depends(get_current_account),
):
    if account.role not in ("teacher", "admin"):
        raise ApiError(E_FORBIDDEN, "只有老师可以查看学生列表", 403)

    stmt = select(Account).where(Account.role == "student", Account.status == "active")
    if q and q.strip():
        like = q.strip()
        stmt = stmt.where(Account.username.contains(like))
    students = db.scalars(stmt.order_by(Account.username)).all()

    # 批量拿成员关系 → 映射到班级名（含"跨班"这个核心诉求）
    members = db.execute(
        select(ClassMember, ClassRoom)
        .join(ClassRoom, ClassRoom.id == ClassMember.class_id)
        .where(ClassMember.account_id.in_([s.id for s in students] or [""]))
    ).all()
    classes_by_student: dict[str, list[dict]] = {}
    for member, cls in members:
        classes_by_student.setdefault(member.account_id, []).append({"id": cls.id, "name": cls.name})

    items = [
        {
            **account_member(student),      # 含档案三字段（teacher/admin 视图）
            "classes": sorted(classes_by_student.get(student.id, []), key=lambda c: c["name"]),
        }
        for student in students
    ]
    return ok({"items": items, "total": len(items)})
