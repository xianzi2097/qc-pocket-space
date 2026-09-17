"""最近访问记录路由 — 存在用户目录的 recent.json"""
import os
import time
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from utils import get_current_user, _resolve_user_dir, _atomic_save

router = APIRouter()

MAX_RECENT = 15


class RecentPostForm(BaseModel):
    type: str   # course | section | resource
    id: str     # 课程 id / section key
    title: str


def _recent_path(username: str) -> str:
    user_dir = _resolve_user_dir(username)
    if not user_dir:
        raise HTTPException(status_code=404, detail="用户不存在")
    return os.path.join(user_dir, "recent.json")


def _load_recent(username: str) -> list[dict]:
    fp = _recent_path(username)
    if not os.path.exists(fp):
        return []
    try:
        with open(fp, "r", encoding="utf-8") as f:
            data = f.read().strip()
            if not data:
                return []
            import json
            items = json.loads(data)
            return items if isinstance(items, list) else []
    except Exception:
        return []


@router.get("/api/recent")
def get_recent(request: Request):
    caller = get_current_user(request)
    items = _load_recent(caller["username"])
    return {"status": "success", "items": items}


@router.post("/api/recent")
def post_recent(request: Request, form: RecentPostForm):
    caller = get_current_user(request)
    items = _load_recent(caller["username"])

    now = int(time.time() * 1000)

    # 去重：同 type + id 只保留最新，移到最前
    items = [i for i in items if not (i.get("type") == form.type and i.get("id") == form.id)]

    items.insert(0, {
        "type": form.type,
        "id": form.id,
        "title": form.title,
        "ts": now,
    })

    # LRU 淘汰
    items = items[:MAX_RECENT]

    fp = _recent_path(caller["username"])
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    _atomic_save(fp, items)

    return {"status": "success", "items": items}
