"""笔记 —— 规范 §3.8（M3 · N 板块 · 9 接口）

两张表零文件：notebooks（本，单层 D18）+ notes（一行一条，md 正文在 content 列）
笔记的归属【经由笔记本判定】：note → notebook_id → 本的 owner_id 才是主人

权限（逐条对应 tests/test_notes.py）：
  全部仅本人——别人的本/笔记一律 404（不泄露存在性）
  删本 = 级联删全部笔记（msg 带"不可恢复"，§7 删除语义总表）
  列表 ?notebook= 按本筛 + 分页（默认 20/页），按 updated_at 倒序（最近编辑置顶）
  重名 8009（同人维度）

路径参数 camelCase（§1.6），前缀 /api 由本 router 统一给。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from ..db import get_db
from ..envelope import (
    E_NOT_FOUND,
    E_NOTE_NOT_FOUND,
    E_NOTEBOOK_TAKEN,
    E_PARAM_INVALID,
    ApiError,
    ok,
)
from ..models import Note, Notebook, new_id, now_str
from ..security import get_current_account

router = APIRouter(prefix="/api", tags=["notes"])

_PAGE_SIZE_DEFAULT = 20
_PAGE_SIZE_MAX = 100


# ---------------- 模型 ----------------

class NotebookCreate(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class NotebookPatch(BaseModel):
    name: str = Field(min_length=1, max_length=64)


class NoteCreate(BaseModel):
    notebook_id: str = Field(min_length=1, max_length=12)
    title: str = Field(min_length=1, max_length=128)
    content: str = Field(default="", max_length=1_000_000)


class NotePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=128)
    content: str | None = Field(default=None, max_length=1_000_000)


# ---------------- 公共辅助 ----------------

def _load_notebook(db: DbSession, notebook_id: str, owner_id: str) -> Notebook:
    row = db.get(Notebook, notebook_id)
    if row is None or row.owner_id != owner_id:      # 别人的本按"不存在"报
        raise ApiError(E_NOT_FOUND, "笔记本不存在", 404)
    return row


def _load_note(db: DbSession, note_id: str, owner_id: str) -> Note:
    note = db.get(Note, note_id)
    if note is None:
        raise ApiError(E_NOTE_NOT_FOUND, "笔记不存在", 404)
    nb = db.get(Notebook, note.notebook_id)
    if nb is None or nb.owner_id != owner_id:        # 归属经由笔记本判定
        raise ApiError(E_NOTE_NOT_FOUND, "笔记不存在", 404)
    return note


def _check_name_free(db: DbSession, owner_id: str, name: str, exclude_id: str | None = None):
    stmt = select(Notebook).where(Notebook.owner_id == owner_id, Notebook.name == name)
    if exclude_id:
        stmt = stmt.where(Notebook.id != exclude_id)
    if db.scalar(stmt):
        raise ApiError(E_NOTEBOOK_TAKEN, "笔记本已存在", 409)


# ---------------- N1~N4 笔记本 ----------------

@router.get("/notebooks")
def list_notebooks(
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    rows = db.scalars(select(Notebook)
                      .where(Notebook.owner_id == account.id)
                      .order_by(Notebook.created_at.desc(), Notebook.id.desc())).all()
    counts = dict(db.execute(
        select(Note.notebook_id, func.count())
        .join(Notebook, Notebook.id == Note.notebook_id)
        .where(Notebook.owner_id == account.id)
        .group_by(Note.notebook_id)).all())
    items = [{"id": n.id, "name": n.name, "created_at": n.created_at,
              "note_count": counts.get(n.id, 0)} for n in rows]
    return ok({"items": items, "total": len(items)})


@router.post("/notebooks")
def create_notebook(
    body: NotebookCreate,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    name = body.name.strip()
    if not name:
        raise ApiError(E_PARAM_INVALID, "笔记本名不能为空", 400)
    _check_name_free(db, account.id, name)
    row = Notebook(id=new_id(), owner_id=account.id, name=name, created_at=now_str())
    db.add(row)
    db.commit()
    return ok({"id": row.id, "name": row.name, "note_count": 0,
               "created_at": row.created_at}, "笔记本已创建")


@router.patch("/notebooks/{notebookId}")
def rename_notebook(
    notebookId: str,
    body: NotebookPatch,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    row = _load_notebook(db, notebookId, account.id)
    name = body.name.strip()
    if not name:
        raise ApiError(E_PARAM_INVALID, "笔记本名不能为空", 400)
    _check_name_free(db, account.id, name, exclude_id=row.id)
    row.name = name                              # 只改一列，笔记零动（id 指针）
    db.commit()
    return ok({"id": row.id, "name": row.name}, "已重命名")


@router.delete("/notebooks/{notebookId}")
def delete_notebook(
    notebookId: str,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    row = _load_notebook(db, notebookId, account.id)
    db.query(Note).where(Note.notebook_id == row.id).delete(
        synchronize_session=False)               # 级联删光：一条 SQL（§7 语义）
    db.delete(row)
    db.commit()
    return ok(None, "笔记本已删除（不可恢复，含全部笔记）")


# ---------------- N5~N9 笔记 ----------------

@router.get("/notes")
def list_notes(
    notebook: str | None = None,
    page: int = 1,
    page_size: int = _PAGE_SIZE_DEFAULT,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    page = max(1, page)
    page_size = max(1, min(page_size, _PAGE_SIZE_MAX))

    stmt = select(Note).join(Notebook, Notebook.id == Note.notebook_id).where(
        Notebook.owner_id == account.id)          # 归属经由笔记本
    if notebook:
        _load_notebook(db, notebook, account.id)  # 筛选不存在/别人的本 → 404
        stmt = stmt.where(Note.notebook_id == notebook)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Note.updated_at.desc(), Note.id.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    items = [{"id": r.id, "notebook_id": r.notebook_id, "title": r.title,
              "updated_at": r.updated_at, "created_at": r.created_at} for r in rows]
    return ok({"items": items, "total": total, "page": page, "page_size": page_size})


@router.post("/notes")
def create_note(
    body: NoteCreate,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    _load_notebook(db, body.notebook_id, account.id)
    row = Note(
        id=new_id(),
        notebook_id=body.notebook_id,
        title=body.title,
        content=body.content,
        updated_at=now_str(),
        created_at=now_str(),
    )
    db.add(row)
    db.commit()
    return ok({"id": row.id, "notebook_id": row.notebook_id, "title": row.title,
               "updated_at": row.updated_at, "created_at": row.created_at}, "笔记已创建")


@router.get("/notes/{noteId}")
def read_note(
    noteId: str,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    row = _load_note(db, noteId, account.id)
    return ok({"id": row.id, "notebook_id": row.notebook_id, "title": row.title,
               "content": row.content, "updated_at": row.updated_at,
               "created_at": row.created_at})


@router.patch("/notes/{noteId}")
def patch_note(
    noteId: str,
    body: NotePatch,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    row = _load_note(db, noteId, account.id)
    if body.title is not None:
        row.title = body.title
    if body.content is not None:
        row.content = body.content               # md 正文直接写进列（零文件）
    row.updated_at = now_str()
    db.commit()
    return ok({"id": row.id, "notebook_id": row.notebook_id, "title": row.title,
               "content": row.content, "updated_at": row.updated_at,
               "created_at": row.created_at}, "笔记已保存")


@router.delete("/notes/{noteId}")
def delete_note(
    noteId: str,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    row = _load_note(db, noteId, account.id)
    db.delete(row)
    db.commit()
    return ok(None, "笔记已删除（不可恢复）")
