"""统一回收站路由（班级/课程回收）"""
import os
import json
import shutil
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from models import TrashModel, SessionLocal
from utils import (
    get_db, get_current_user, require_role, restore_from_trash,
    _list_trash_items, _move_to_trash, _load_classes, _save_classes,
    _get_teacher_course_dir, _safe_path, TRASH_DIR,
    _get_classes_path, _file_lock
)

router = APIRouter()


# ── 用户垃圾桶（DB 版，TrashModel）──

@router.get("/api/trash")
def list_trash(request: Request, db: Session = Depends(get_db)):
    require_role(request, ["admin"])
    items = db.query(TrashModel).order_by(TrashModel.id.desc()).all()
    return {
        "status": "success",
        "trash": [{
            "id": t.id, "username": t.username, "role": t.role,
            "trash_folder": t.trash_folder, "trashed_at": t.trashed_at
        } for t in items]
    }


@router.post("/api/trash/restore/{trash_id}")
def restore_user(request: Request, trash_id: int, db: Session = Depends(get_db)):
    require_role(request, ["admin"])
    return restore_from_trash(trash_id, db)


@router.delete("/api/trash")
def empty_trash(request: Request, db: Session = Depends(get_db)):
    require_role(request, ["admin"])
    items = db.query(TrashModel).all()
    for t in items:
        tp = os.path.join(TRASH_DIR, t.trash_folder)
        if os.path.exists(tp):
            shutil.rmtree(tp)
        db.delete(t)
    db.commit()
    return {"status": "success", "message": f"已清空 {len(items)} 条垃圾桶记录"}


@router.delete("/api/trash/{trash_id}")
def permanent_delete_trash(request: Request, trash_id: int, db: Session = Depends(get_db)):
    require_role(request, ["admin"])
    trash = db.query(TrashModel).filter(TrashModel.id == trash_id).first()
    if not trash:
        raise HTTPException(status_code=404, detail="垃圾桶中未找到该记录")
    trash_path = os.path.join(TRASH_DIR, trash.trash_folder)
    if os.path.exists(trash_path):
        shutil.rmtree(trash_path)
    db.delete(trash)
    db.commit()
    return {"status": "success", "message": f"已彻底删除 [{trash.username}]"}


# ── 统一回收站（文件版，班级/课程）──

@router.get("/api/trash/items")
def list_trash_items(request: Request):
    caller = get_current_user(request)
    owner = caller["username"] if caller["role"] != "admin" else ""
    items = _list_trash_items(owner=owner)
    return {"status": "success", "items": items}


@router.post("/api/trash/items/{folder_name}/restore")
def restore_trash_item(request: Request, folder_name: str):
    caller = require_role(request, ["admin", "teacher"])
    folder_path = os.path.join(TRASH_DIR, folder_name)
    meta_path = os.path.join(folder_path, ".trash.json")
    if not os.path.exists(meta_path):
        raise HTTPException(status_code=404, detail="回收站中未找到该项目")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    if meta["type"] == "class":
        json_files = [f for f in os.listdir(folder_path) if f.endswith(".json") and f != ".trash.json"]
        if not json_files:
            raise HTTPException(status_code=404, detail="回收站数据损坏")
        with open(os.path.join(folder_path, json_files[0]), "r", encoding="utf-8") as f:
            data = json.load(f)
        restored = data.get("class_data")
        if not restored:
            raise HTTPException(status_code=404, detail="回收站数据损坏")
        # 读→改→写整体上锁：fp 与 _load/_save_classes 内部同一路径算法
        fp = _get_classes_path(caller["username"])
        with _file_lock(fp):
            classes = _load_classes(caller["username"])
            classes.append(restored)
            _save_classes(caller["username"], classes)
    elif meta["type"] == "course":
        base = _get_teacher_course_dir(meta["owner"])
        target = os.path.join(base, os.path.basename(meta["original_path"]))
        shutil.move(folder_path, target)
        trash_meta = os.path.join(target, ".trash.json")
        if os.path.exists(trash_meta):
            os.remove(trash_meta)
        return {"status": "success", "message": "课程已恢复"}
    else:
        raise HTTPException(status_code=400, detail="不支持的回收站类型")
    shutil.rmtree(folder_path)
    return {"status": "success", "message": "已恢复"}


@router.delete("/api/trash/items/{folder_name}")
def permanent_delete_item(request: Request, folder_name: str):
    # 永久删除仅 admin（与 DB 版 DELETE /api/trash/{id} 一致；老师可看列表/恢复，不可彻底删）
    require_role(request, ["admin"])
    folder_path = os.path.join(TRASH_DIR, folder_name)
    if not os.path.exists(folder_path):
        raise HTTPException(status_code=404, detail="回收站中未找到该项目")
    shutil.rmtree(folder_path)
    return {"status": "success", "message": "已永久删除"}
