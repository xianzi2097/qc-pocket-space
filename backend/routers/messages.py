"""站内信路由"""
import os
import json
import shutil
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session
from models import UserModel
from utils import (
    get_db, get_current_user, require_role,
    _safe_path, _get_msgs_dir, _write_msg, _find_msg_file,
    SendMessageForm, _atomic_save, _file_lock
)

router = APIRouter()


@router.post("/api/messages/send")
def send_message(request: Request, form: SendMessageForm, db: Session = Depends(get_db)):
    caller = require_role(request, ["teacher"])
    recipient = db.query(UserModel).filter(
        UserModel.username == form.recipient, UserModel.role == "student"
    ).first()
    if not recipient:
        raise HTTPException(status_code=404, detail=f"学生 [{form.recipient}] 不存在，请检查账号名")
    folder = form.folder or "收件箱"
    msg_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{caller['username']}"
    _write_msg(form.recipient, folder, msg_id, caller["username"], form.subject, form.body)
    return {"status": "success", "message": "消息发送成功！", "id": msg_id}


@router.get("/api/messages/folders")
def list_folders(request: Request):
    caller = get_current_user(request)
    base = _get_msgs_dir(caller["username"])
    if not os.path.exists(base):
        return {"status": "success", "folders": [{"name": "收件箱", "count": 0, "unread": 0}], "total_unread": 0}
    folders = []
    total_unread = 0
    for folder_name in sorted(os.listdir(base)):
        fp = os.path.join(base, folder_name)
        if not os.path.isdir(fp) or folder_name.startswith("."):
            continue
        files = [f for f in os.listdir(fp) if f.endswith(".json")]
        unread = 0
        for f in files:
            try:
                with open(os.path.join(fp, f), "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    if not data.get("is_read", True):
                        unread += 1
            except (json.JSONDecodeError, OSError):
                pass
        folders.append({"name": folder_name, "count": len(files), "unread": unread})
        total_unread += unread
    if not any(f["name"] == "收件箱" for f in folders):
        folders.insert(0, {"name": "收件箱", "count": 0, "unread": 0})
    return {"status": "success", "folders": folders, "total_unread": total_unread}


@router.get("/api/messages/inbox")
def inbox(request: Request, folder: str = ""):
    caller = get_current_user(request)
    # folder 来自查询参数 → 过 _safe_path，防 ../ 越出消息根目录
    dir_path = _safe_path(_get_msgs_dir(caller["username"]), folder)
    if not os.path.exists(dir_path):
        return {"status": "success", "messages": []}
    msgs = []
    for fname in sorted(os.listdir(dir_path), reverse=True):
        if not fname.endswith(".json"):
            continue
        try:
            with open(os.path.join(dir_path, fname), "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("subject"):
                    msgs.append(data)
        except (json.JSONDecodeError, OSError):
            pass
    return {"status": "success", "messages": msgs}


@router.post("/api/messages/{msg_id}/read")
def mark_read(request: Request, msg_id: str, folder: str = ""):
    caller = get_current_user(request)
    fp = _find_msg_file(caller["username"], folder, msg_id)
    if not fp:
        raise HTTPException(status_code=404, detail="消息不存在")
    # 读-改-写整体持锁：防并发 mark_read / 改消息字段时互相覆盖
    with _file_lock(fp):
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["is_read"] = True
        _atomic_save(fp, data)
    return {"status": "success"}


@router.post("/api/messages/folders")
def create_folder(request: Request, folder: str = ""):
    caller = get_current_user(request)
    if not folder or folder == "收件箱":
        raise HTTPException(status_code=400, detail="文件夹名无效")
    # folder 来自查询参数 → 过 _safe_path，防 ../ 在消息根目录之外建目录
    dir_path = _safe_path(_get_msgs_dir(caller["username"]), folder)
    if os.path.exists(dir_path):
        raise HTTPException(status_code=400, detail="文件夹已存在")
    os.makedirs(dir_path, exist_ok=True)
    return {"status": "success", "message": f"文件夹 [{folder}] 创建成功！"}


@router.delete("/api/messages/folders/{folder_name:path}")
def delete_folder(request: Request, folder_name: str):
    caller = get_current_user(request)
    if folder_name == "收件箱":
        raise HTTPException(status_code=400, detail="不能删除收件箱")
    path = _safe_path(_get_msgs_dir(caller["username"]), folder_name)
    if os.path.exists(path):
        shutil.rmtree(path)
    return {"status": "success", "message": f"文件夹 [{folder_name}] 已删除"}


@router.put("/api/messages/folders/{folder_name:path}")
def rename_folder(request: Request, folder_name: str, new_name: str = ""):
    caller = get_current_user(request)
    if folder_name == "收件箱":
        raise HTTPException(status_code=400, detail="不能重命名收件箱")
    if not new_name or new_name == folder_name:
        raise HTTPException(status_code=400, detail="新名称无效")
    base = _get_msgs_dir(caller["username"])
    old_path = _safe_path(base, folder_name)
    new_path = _safe_path(base, new_name)
    if not os.path.exists(old_path):
        raise HTTPException(status_code=404, detail="文件夹不存在")
    if os.path.exists(new_path):
        raise HTTPException(status_code=400, detail="同名文件夹已存在")
    os.rename(old_path, new_path)
    return {"status": "success", "message": f"文件夹 [{folder_name}] → [{new_name}]"}


@router.delete("/api/messages/{msg_id}")
def delete_message(request: Request, msg_id: str, folder: str = ""):
    caller = get_current_user(request)
    if not folder:
        raise HTTPException(status_code=400, detail="请指定文件夹")
    base = _get_msgs_dir(caller["username"])
    fp = _safe_path(base, folder, f"{msg_id}.json")
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="消息不存在")
    os.remove(fp)
    return {"status": "success", "message": "消息已删除"}


@router.post("/api/messages/{msg_id}/move")
def move_message(request: Request, msg_id: str, folder: str = ""):
    caller = get_current_user(request)
    src_fp = _find_msg_file(caller["username"], None, msg_id)
    if not src_fp:
        raise HTTPException(status_code=404, detail="消息不存在")
    # folder 来自查询参数 → 过 _safe_path，防 ../ 把消息移出消息根目录
    dst_dir = _safe_path(_get_msgs_dir(caller["username"]), folder)
    os.makedirs(dst_dir, exist_ok=True)
    os.rename(src_fp, os.path.join(dst_dir, f"{msg_id}.json"))
    return {"status": "success", "message": f"已移至 [{folder}]"}


@router.get("/api/messages/unread-count")
def unread_count(request: Request):
    result = list_folders(request)
    return {"status": "success", "count": result.get("total_unread", 0)}
