"""资产 —— 规范 §3.4/§3.5（D16 合表 · D17 接口合一 · M2 全部动作）

一张 assets 表 + 每资产一个磁盘目录 + file_order 排序账 —— 后端的大一统。
bucket 五值（courses|books|reference|download|viz）是全域唯一的分类词（原 shelf/category 废除；
viz=可视化，D35：一行 = 一个演示页 = 根目录一个 .html，其 files/download 走 inline + CSP sandbox）。

权限（逐条对应 tests/test_assets.py）：
  读（列表/详情/文件树/下载）   公开；详情的 publish 字段仅 owner/admin 可见
  写（建/改/删/文件树写/课件/发布） owner 或 admin；建资产仅 teacher/admin
  族守卫（违反 → 404）          lessons/publish/publish-status 仅课程族；
                                download（zip）全族开放（D33/D34：课程族须 shareable=true）
  软删四联动                    行删 + trash 快照(type=asset) + 磁盘移 trash/{id}
                                + file_order 保留（彻底删 M4 清账）
  重名                          按 owner 全局查（跨族也不许重名）→ 5001 课程族 / 6001 资料族

路径参数一律 camelCase（规范 §1.6），前缀 /api/assets 由本 router 统一给。
"""
import shutil

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession
from starlette.background import BackgroundTask

from .. import filetree
from ..db import get_db
from ..envelope import (
    E_COURSE_NAME_TAKEN,
    E_FORBIDDEN,
    E_NOT_FOUND,
    E_PARAM_INVALID,
    E_RESOURCE_NAME_TAKEN,
    ApiError,
    ok,
)
from ..models import Account, Asset, Trash, new_id, now_str
from ..publish import enqueue, is_active, remove_site
from ..security import get_current_account, get_optional_account

router = APIRouter(prefix="/api/assets", tags=["assets"])

_BUCKETS = {"courses", "books", "reference", "download", "viz"}
_COURSE_FAMILY = {"courses", "books"}      # 族守卫：lessons/publish 只认这两值（zip 下载已全族开放，D33/D34）
_VIZ = "viz"                                 # D35 可视化：非课程族（守卫自动拦），.html 走 inline 内嵌


# ---------------- 模型 ----------------

class AssetCreate(BaseModel):
    bucket: str = Field(min_length=1, max_length=16)
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=5000)
    tags: list[str] = Field(default_factory=list)
    type: str = Field(default="", max_length=32)     # 课程族必填（前端表单选）
    format: str = Field(default="", max_length=32)   # 缺省按 type 兜底


class AssetPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    description: str | None = Field(default=None, max_length=5000)
    tags: list[str] | None = None
    type: str | None = Field(default=None, max_length=32)
    format: str | None = Field(default=None, max_length=32)
    bucket: str | None = Field(default=None, min_length=1, max_length=16)  # 挪书架/挪分类
    shareable: bool | None = None                             # 课程族下载开关（D34）


class MkdirBody(BaseModel):
    path: str = Field(default="", max_length=500)


class RenameBody(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    new_name: str = Field(min_length=1, max_length=200)


class OrderBody(BaseModel):
    path: str = Field(default="", max_length=500)
    names: list[str] = Field(min_length=1, max_length=500)
    position: int = 0


# ---------------- 公共辅助 ----------------

def _load(db: DbSession, asset_id: str) -> Asset:
    asset = db.get(Asset, asset_id)
    if asset is None:
        raise ApiError(E_NOT_FOUND, "资产不存在", 404)
    return asset


def _require_owner(asset: Asset, account) -> None:
    if account.role != "admin" and asset.owner_id != account.id:
        raise ApiError(E_FORBIDDEN, "没有权限", 403)


def _require_family(asset: Asset, family: set[str]) -> None:
    """族守卫：桶不符按 404 报（不暴露"存在但你没资格"）"""
    if asset.bucket not in family:
        raise ApiError(E_NOT_FOUND, "资产不存在", 404)


def _name_taken_code(bucket: str) -> int:
    return E_COURSE_NAME_TAKEN if bucket in _COURSE_FAMILY else E_RESOURCE_NAME_TAKEN


def _check_name(db: DbSession, owner_id: str, name: str, bucket: str,
                exclude_id: str | None = None) -> None:
    stmt = select(Asset).where(Asset.owner_id == owner_id, Asset.name == name)
    if exclude_id:
        stmt = stmt.where(Asset.id != exclude_id)
    if db.scalar(stmt):
        raise ApiError(_name_taken_code(bucket), "名称已存在", 409)


def _owner_names(db: DbSession, owner_ids: set[str]) -> dict[str, str]:
    if not owner_ids:
        return {}
    rows = db.scalars(select(Account).where(Account.id.in_(owner_ids))).all()
    return {row.id: row.username for row in rows}


def _public(asset: Asset, owner_names: dict[str, str],
            viewer=None) -> dict:
    data = {
        "id": asset.id,
        "owner_id": asset.owner_id,
        "owner_name": owner_names.get(asset.owner_id, ""),
        "bucket": asset.bucket,
        "name": asset.name,
        "description": asset.description,
        "tags": asset.tags,
        "type": asset.type,
        "format": asset.format,
        "shareable": bool(asset.shareable),   # 课程下载开关公开（前端显隐下载按钮，D34）
        "created_at": asset.created_at,
        # publish.state 公开（前端据此开电子书 iframe）；message 可能带服务器路径，仅 owner/admin
        "publish": asset.publish or {"state": "none"},
    }
    if not (viewer is not None
            and (viewer.role == "admin" or viewer.id == asset.owner_id)):
        state = data["publish"].get("state", "none")
        data["publish"] = {"state": state}
    return data


def _view(db: DbSession, asset: Asset, viewer) -> dict:
    return _public(asset, _owner_names(db, {asset.owner_id}), viewer)


# ---------------- 资产 CRUD ----------------

@router.get("")
def list_assets(
    bucket: str | None = None,
    owner: str | None = None,
    db: DbSession = Depends(get_db),
    viewer=Depends(get_optional_account),
):
    if bucket and bucket not in _BUCKETS:
        raise ApiError(E_PARAM_INVALID, "bucket 只能是 courses/books/reference/download/viz", 400)
    stmt = select(Asset)
    if bucket:
        stmt = stmt.where(Asset.bucket == bucket)
    if owner:
        stmt = stmt.where(Asset.owner_id == owner)
    items_all = db.scalars(stmt.order_by(Asset.created_at.desc())).all()
    names = _owner_names(db, {a.owner_id for a in items_all})
    items = [_public(a, names, viewer) for a in items_all]
    return ok({"items": items, "total": len(items)})


@router.post("")
def create_asset(
    body: AssetCreate,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    if account.role not in ("teacher", "admin"):
        raise ApiError(E_FORBIDDEN, "只有老师可以创建资产", 403)
    if body.bucket not in _BUCKETS:
        raise ApiError(E_PARAM_INVALID, "bucket 只能是 courses/books/reference/download/viz", 400)

    name = body.name.strip()
    if not name:
        raise ApiError(E_PARAM_INVALID, "名称不能为空", 400)
    atype = body.type.strip()
    if body.bucket in _COURSE_FAMILY and not atype:
        raise ApiError(E_PARAM_INVALID, "课程族必须指定 type（前端表单选择）", 400)
    _check_name(db, account.id, name, body.bucket)

    # format 兜底：显式优先；课程族按 type 推断（scratch→scratch，其余→folder）
    fmt = body.format.strip()
    if not fmt and body.bucket in _COURSE_FAMILY:
        fmt = "scratch" if atype == "scratch" else "folder"

    asset = Asset(
        id=new_id(),
        owner_id=account.id,
        bucket=body.bucket,
        name=name,
        description=body.description,
        tags=body.tags or [],
        type=atype if body.bucket in _COURSE_FAMILY else "",
        format=fmt if body.bucket in _COURSE_FAMILY else "",
        created_at=now_str(),
    )
    try:
        filetree.create_asset_dir(asset.id)     # 库+盘双写：先备货
        db.add(asset)
        db.commit()
    except Exception:
        db.rollback()
        shutil.rmtree(filetree.asset_root(asset.id), ignore_errors=True)
        raise
    return ok(_view(db, asset, account), "创建成功")


@router.get("/{assetId}")
def get_asset(assetId: str, db: DbSession = Depends(get_db),
              viewer=Depends(get_optional_account)):
    asset = _load(db, assetId)
    return ok(_view(db, asset, viewer))


@router.patch("/{assetId}")
def patch_asset(
    assetId: str,
    body: AssetPatch,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    asset = _load(db, assetId)
    _require_owner(asset, account)

    if body.bucket is not None:
        if body.bucket not in _BUCKETS:
            raise ApiError(E_PARAM_INVALID, "bucket 只能是 courses/books/reference/download/viz", 400)
        asset.bucket = body.bucket              # 跨族挪动允许（D17：课→资料归档）
    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise ApiError(E_PARAM_INVALID, "名称不能为空", 400)
        _check_name(db, asset.owner_id, name, asset.bucket, exclude_id=asset.id)
        asset.name = name
    if body.description is not None:
        asset.description = body.description
    if body.tags is not None:
        asset.tags = body.tags
    if body.type is not None:
        asset.type = body.type
    if body.format is not None:
        asset.format = body.format
    if body.shareable is not None:
        asset.shareable = body.shareable      # 课程下载开关（D34）

    db.commit()
    return ok(_view(db, asset, account), "已更新")


@router.delete("/{assetId}")
def delete_asset(
    assetId: str,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    asset = _load(db, assetId)
    _require_owner(asset, account)
    asset_id = asset.id                          # commit 后对象过期，先捕获

    # ③ 磁盘整棵移进回收站（软删；恢复 M4 做，移回即复原）
    src = filetree.asset_root(asset_id)
    dst = filetree.asset_trash_dir(asset_id)
    if src.exists():
        shutil.rmtree(dst, ignore_errors=True)
        shutil.move(str(src), str(dst))

    try:
        # ① 行删（id 保留在快照里 → file_order ④ 与恢复都认它）
        db.add(Trash(
            id=new_id(),
            type="asset",
            owner_id=asset.owner_id,
            name=asset.name,
            payload={
                "id": asset.id, "owner_id": asset.owner_id, "bucket": asset.bucket,
                "name": asset.name, "description": asset.description,
                "tags": asset.tags, "type": asset.type, "format": asset.format,
                "publish": asset.publish, "created_at": asset.created_at,
            },
            disk_path=f"trash/{asset.id}",
        ))
        db.delete(asset)
        db.commit()
    except Exception:
        db.rollback()
        if dst.exists() and not src.exists():     # 磁盘搬回来，保持一致
            shutil.move(str(dst), str(src))
        raise
    # ⑤ 发布产物清理（§3.4 四联动）：已删内容不得在 /course/{id}/ 公网残留。
    #    放 commit 成功后——回滚路径资产还活着，产物必须保留（状态诚实）
    remove_site(asset_id)
    return ok(None, "已移入回收站")


# ---------------- 文件树（F1~F7 挂载点，D17 二合一） ----------------

@router.get("/{assetId}/files")
def list_files(assetId: str, path: str = "", db: DbSession = Depends(get_db)):
    _load(db, assetId)
    return ok(filetree.list_entries(db, assetId, path))


@router.get("/{assetId}/files/download")
def download_file(assetId: str, path: str = "", db: DbSession = Depends(get_db)):
    asset = _load(db, assetId)
    # D35：viz 族的 .html 走 inline + CSP sandbox（前端 iframe 内嵌演示页）
    return filetree.file_response(
        filetree.open_target(assetId, path),
        inline_html=asset.bucket == _VIZ,
    )


@router.post("/{assetId}/files/upload")
async def upload_file(
    assetId: str,
    file: UploadFile = File(...),
    path: str = Form(...),
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    asset = _load(db, assetId)
    _require_owner(asset, account)
    rel = filetree.upload_file(assetId, path, file)
    return ok({"path": rel}, "上传成功")


@router.post("/{assetId}/files/mkdir")
def mkdir(assetId: str, body: MkdirBody, db: DbSession = Depends(get_db),
          account = Depends(get_current_account)):
    asset = _load(db, assetId)
    _require_owner(asset, account)
    return ok({"path": filetree.mkdir(assetId, body.path)}, "文件夹已创建")


@router.patch("/{assetId}/files/rename")
def rename_path(assetId: str, body: RenameBody, db: DbSession = Depends(get_db),
                account = Depends(get_current_account)):
    asset = _load(db, assetId)
    _require_owner(asset, account)
    return ok({"path": filetree.rename(db, assetId, body.path, body.new_name)}, "已重命名")


@router.delete("/{assetId}/files")
def remove_path(assetId: str, path: str = "", db: DbSession = Depends(get_db),
                account = Depends(get_current_account)):
    asset = _load(db, assetId)
    _require_owner(asset, account)
    filetree.remove(db, assetId, path)
    return ok(None, "已删除（不可恢复）")


@router.post("/{assetId}/files/order")
def order_path(assetId: str, body: OrderBody, db: DbSession = Depends(get_db),
               account = Depends(get_current_account)):
    asset = _load(db, assetId)
    _require_owner(asset, account)
    filetree.apply_order(db, assetId, body.path, body.names, body.position)
    return ok(None, "排序已更新")


# ---------------- 课件（课程族守卫） ----------------

@router.get("/{assetId}/lessons")
def list_lessons(assetId: str, db: DbSession = Depends(get_db)):
    asset = _load(db, assetId)
    _require_family(asset, _COURSE_FAMILY)
    items = []
    for child in sorted(filetree.asset_root(asset.id).iterdir(), key=lambda p: p.name):
        if not child.is_dir() or child.name.startswith("."):
            continue
        sb3 = [f.name for f in child.iterdir()
               if f.is_file() and f.suffix.lower() == ".sb3"]
        items.append({
            "name": child.name,
            "has_main": "main.sb3" in sb3,
            "has_student": "student.sb3" in sb3,
            "sb3_count": len(sb3),
        })
    return ok({"items": items, "total": len(items)})


@router.delete("/{assetId}/lessons/{lesson}")
def delete_lesson(
    assetId: str,
    lesson: str,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    asset = _load(db, assetId)
    _require_family(asset, _COURSE_FAMILY)
    _require_owner(asset, account)
    filetree.remove(db, assetId, lesson)         # 树内物理删（前端必须二次确认）
    return ok(None, "课件已删除（不可恢复）")


# ---------------- 发布队列（C9/C10 · 课程族守卫） ----------------

@router.post("/{assetId}/publish")
def publish_start(
    assetId: str,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    asset = _load(db, assetId)
    _require_family(asset, _COURSE_FAMILY)
    _require_owner(asset, account)

    if is_active((asset.publish or {}).get("state")):
        return ok(asset.publish, "正在渲染中")     # 幂等：已在队列不重复入

    asset.publish = {"state": "queued", "queued_at": now_str()}
    db.commit()
    enqueue(assetId)                              # 入队即返回，worker 后台串行渲染
    return ok(asset.publish, "已入队")


@router.get("/{assetId}/publish-status")
def publish_status(
    assetId: str,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    asset = _load(db, assetId)
    _require_family(asset, _COURSE_FAMILY)
    _require_owner(asset, account)
    return ok(asset.publish or {"state": "none"})


# ---------------- 打包下载（全族开放，D33/D34） ----------------

@router.get("/{assetId}/download")
def download_asset(assetId: str, path: str = "", db: DbSession = Depends(get_db)):
    asset = _load(db, assetId)
    if asset.bucket in _COURSE_FAMILY and not asset.shareable:
        # 课程族下载闸门（D34）：老师后台开关没开就不许打包；资料族不限（原行为）
        raise ApiError(E_FORBIDDEN, "该课程未开放下载", 403)
    target, is_temp = filetree.pack(assetId, path)
    if is_temp:
        return FileResponse(
            target,
            media_type="application/zip",
            filename=f"{asset.name}.zip",
            background=BackgroundTask(target.unlink, missing_ok=True),
        )
    return filetree.file_response(target)
