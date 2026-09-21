"""用户设置路由 — 存在用户目录的 settings.json"""
import os
import json
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from utils import get_current_user, _resolve_user_dir, _atomic_save

router = APIRouter()

# 默认设置
DEFAULTS = {
    "theme": "",
    "customHue": "",
    "fontSize": 16,
    "sidebarExpanded": True,
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
    settings = _load_settings(caller["username"])

    if form.theme is not None:
        settings["theme"] = form.theme
    if form.customHue is not None:
        settings["customHue"] = form.customHue
    if form.fontSize is not None:
        settings["fontSize"] = max(12, min(28, form.fontSize))
    if form.sidebarExpanded is not None:
        settings["sidebarExpanded"] = bool(form.sidebarExpanded)

    fp = _settings_path(caller["username"])
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    _atomic_save(fp, settings)

    return {"status": "success", "settings": settings}
