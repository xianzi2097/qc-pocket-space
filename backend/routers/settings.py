"""用户设置路由 — 存在用户目录的 settings.json"""
import os
import json
import time
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from utils import get_current_user, _resolve_user_dir, _atomic_save, _file_lock

router = APIRouter()

# 默认设置（sidebarExpanded 默认收起：首次进入是图标栏，用户手动展开后记住）
DEFAULTS = {
    "theme": "",
    "customHue": "",
    "fontSize": 16,
    "sidebarExpanded": False,
    "recentOpens": [],
}


def _settings_path(username: str) -> str:
    user_dir = _resolve_user_dir(username)
    if not user_dir:
        raise HTTPException(status_code=404, detail="用户不存在")
    return os.path.join(user_dir, "settings.json")


def _load_settings(username: str) -> dict:
    fp = _settings_path(username)
    if not os.path.exists(fp):
        return dict(DEFAULTS)
    try:
        with open(fp, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                # 合并默认值（新字段自动补齐）
                merged = dict(DEFAULTS)
                merged.update(data)
                return merged
    except Exception:
        pass
    return dict(DEFAULTS)


@router.get("/api/user/settings")
def get_settings(request: Request):
    caller = get_current_user(request)
    return {"status": "success", "settings": _load_settings(caller["username"])}


class SettingsForm(BaseModel):
    theme: str | None = None
    customHue: str | None = None
    fontSize: int | None = None
    sidebarExpanded: bool | None = None


@router.put("/api/user/settings")
def update_settings(request: Request, form: SettingsForm):
    caller = get_current_user(request)
    fp = _settings_path(caller["username"])
    # 读-改-写整体持锁：防两个 PUT 并发时互相覆盖
    with _file_lock(fp):
        settings = _load_settings(caller["username"])

        if form.theme is not None:
            settings["theme"] = form.theme
        if form.customHue is not None:
            settings["customHue"] = form.customHue
        if form.fontSize is not None:
            settings["fontSize"] = max(12, min(28, form.fontSize))
        if form.sidebarExpanded is not None:
            settings["sidebarExpanded"] = bool(form.sidebarExpanded)

        os.makedirs(os.path.dirname(fp), exist_ok=True)
        _atomic_save(fp, settings)

    return {"status": "success", "settings": settings}


class RecentOpenForm(BaseModel):
    kind: Literal["section", "course"]
    id: str
    label: str
    teacher: str | None = None
    type: str | None = None


@router.post("/api/user/recent-opens")
def add_recent_open(request: Request, form: RecentOpenForm):
    """记录一次「最近打开」—— 读-改-写整体持锁，同 kind+id 去重置顶，上限 6 条。游客无账号 → 401"""
    caller = get_current_user(request)
    fp = _settings_path(caller["username"])
    entry = {
        "kind": form.kind,
        "id": form.id[:100],
        "label": form.label[:200],
        "teacher": form.teacher,
        "type": form.type,
        "ts": int(time.time() * 1000),
    }
    with _file_lock(fp):
        settings = _load_settings(caller["username"])
        rest = [
            x for x in settings.get("recentOpens", [])
            if not (isinstance(x, dict) and x.get("kind") == form.kind and x.get("id") == form.id)
        ]
        settings["recentOpens"] = ([entry] + rest)[:6]
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        _atomic_save(fp, settings)

    return {"status": "success"}
