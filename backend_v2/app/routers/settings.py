"""个人设置 —— 规范 §3.10（M3 · S 板块 · 3 接口）

一张表 settings：一人一行（account_id 主键），没存过也返回全套默认值
最近打开：去重置顶、上限 config.RECENT_OPENS_LIMIT（默认 6）

全部仅本人；字段校验手写 ApiError（保证 400/1002，不依赖 pydantic 报错形状）。

路径参数 camelCase（§1.6），前缀 /api 由本 router 统一给。
"""
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session as DbSession

from .. import config
from ..db import get_db
from ..envelope import E_PARAM_INVALID, ApiError, ok
from ..models import Setting
from ..security import get_current_account

router = APIRouter(prefix="/api", tags=["settings"])

_THEMES = ("", "green", "blue", "custom")   # D20：前端真实四值（''=橙默认）；light/dark 系 spec 想象的暗黑，已勘误


class SettingsPut(BaseModel):
    theme: str | None = Field(default=None, max_length=16)
    hue: int | None = None
    font_size: int | None = None
    sidebar: bool | None = None


class RecentOpen(BaseModel):
    item: str = Field(min_length=1, max_length=200)


def _shape(row: Setting | None) -> dict:
    if row is None:                              # 没存过 → 全套默认（D20：橙）
        return {"theme": "", "hue": 95, "font_size": 16,
                "sidebar": True, "recent_opens": []}
    return {"theme": row.theme, "hue": row.hue, "font_size": row.font_size,
            "sidebar": row.sidebar, "recent_opens": row.recent_opens or []}


def _get_row(db: DbSession, account_id: str) -> Setting | None:
    return db.get(Setting, account_id)


@router.get("/settings")
def get_settings(
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    return ok(_shape(_get_row(db, account.id)))


@router.put("/settings")
def put_settings(
    body: SettingsPut,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    if body.theme is not None and body.theme not in _THEMES:
        raise ApiError(E_PARAM_INVALID, "theme 只能是 ''/green/blue/custom（D20）", 400)
    if body.hue is not None and not (0 <= body.hue <= 360):
        raise ApiError(E_PARAM_INVALID, "hue 取值 0-360", 400)
    if body.font_size is not None and not (12 <= body.font_size <= 28):
        raise ApiError(E_PARAM_INVALID, "font_size 取值 12-28", 400)

    row = _get_row(db, account.id)
    if row is None:
        row = Setting(account_id=account.id)      # 首次写入按默认建行，再叠改动
        db.add(row)
    if body.theme is not None:
        row.theme = body.theme
    if body.hue is not None:
        row.hue = body.hue
    if body.font_size is not None:
        row.font_size = body.font_size
    if body.sidebar is not None:
        row.sidebar = body.sidebar
    db.commit()
    return ok(_shape(row), "设置已保存")


@router.post("/settings/recent-opens")
def push_recent_open(
    body: RecentOpen,
    db: DbSession = Depends(get_db),
    account = Depends(get_current_account),
):
    row = _get_row(db, account.id)
    if row is None:
        row = Setting(account_id=account.id)
        db.add(row)

    items: list = list(row.recent_opens or [])
    if body.item in items:                       # 去重
        items.remove(body.item)
    items.insert(0, body.item)                   # 置顶
    row.recent_opens = items[:config.RECENT_OPENS_LIMIT]   # 封顶，挤掉最旧
    db.commit()
    return ok({"recent_opens": row.recent_opens})
