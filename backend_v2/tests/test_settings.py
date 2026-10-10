"""验收单：个人设置域（规范 §3.10 · M3-S 板块）

覆盖：
  · 读：没存过也返回全套默认值（缺省字段补默认）
  · 写：部分更新（PUT 只动传了的字段），字段值校验（主题/色相/字号 12-28）
  · 最近打开：去重置顶、上限 6（config.RECENT_OPENS_LIMIT）
  · 隔离：每人一套，互不可见；未登录 401
"""
from app import config
from app.db import SessionLocal
from app.models import Setting

PWD = "abcd1234"
_N = 0


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login_as(client, username):
    resp = client.post("/api/auth/login", json={"username": username, "password": PWD})
    assert resp.json()["code"] == 0, resp.text
    return resp.json()["data"]["token"]


def _user(client, login):
    global _N
    _N += 1
    admin = login()["token"]
    name = f"set_u{_N}"
    resp = client.post("/api/accounts",
                       json={"username": name, "password": PWD, "role": "student"},
                       headers=_auth(admin))
    assert resp.json()["code"] == 0, resp.text
    return _login_as(client, name)


def test_get_defaults_without_saving(client, login):
    t = _user(client, login)
    data = client.get("/api/settings", headers=_auth(t)).json()["data"]
    assert data["theme"] == "" and data["font_size"] == 16          # D20：默认橙
    assert data["sidebar"] is True and data["recent_opens"] == []


def test_put_partial_update(client, login):
    t = _user(client, login)
    resp = client.put("/api/settings", json={"theme": "custom", "hue": 300},
                      headers=_auth(t))
    assert resp.json()["code"] == 0

    data = client.get("/api/settings", headers=_auth(t)).json()["data"]
    assert data["theme"] == "custom" and data["hue"] == 300
    assert data["font_size"] == 16                                 # 没传的字段保持默认


def test_put_validation(client, login):
    t = _user(client, login)
    bad_size = client.put("/api/settings", json={"font_size": 30}, headers=_auth(t))
    assert bad_size.status_code == 400 and bad_size.json()["code"] == 1002

    bad_theme = client.put("/api/settings", json={"theme": "purple"}, headers=_auth(t))
    assert bad_theme.status_code == 400 and bad_theme.json()["code"] == 1002

    bad_hue = client.put("/api/settings", json={"hue": 999}, headers=_auth(t))
    assert bad_hue.status_code == 400 and bad_hue.json()["code"] == 1002


def test_recent_opens_dedup_front_and_cap(client, login):
    t = _user(client, login)
    for item in ["a", "b", "c"]:
        client.post("/api/settings/recent-opens", json={"item": item}, headers=_auth(t))
    data = client.get("/api/settings", headers=_auth(t)).json()["data"]
    assert data["recent_opens"] == ["c", "b", "a"]                 # 最新的在最前

    # 重复打开 a → 去重置顶
    client.post("/api/settings/recent-opens", json={"item": "a"}, headers=_auth(t))
    data = client.get("/api/settings", headers=_auth(t)).json()["data"]
    assert data["recent_opens"][0] == "a" and data["recent_opens"].count("a") == 1

    # 灌满超过上限 → 封顶且挤掉最旧
    limit = config.RECENT_OPENS_LIMIT
    for i in range(limit + 3):
        client.post("/api/settings/recent-opens", json={"item": f"x{i}"}, headers=_auth(t))
    data = client.get("/api/settings", headers=_auth(t)).json()["data"]
    assert len(data["recent_opens"]) == limit
    assert data["recent_opens"][0] == f"x{limit + 2}"              # 最新的在最前


def test_settings_isolated_per_user(client, login):
    t1 = _user(client, login)
    t2 = _user(client, login)
    client.put("/api/settings", json={"theme": "green"}, headers=_auth(t1))

    assert client.get("/api/settings", headers=_auth(t1)
                      ).json()["data"]["theme"] == "green"
    assert client.get("/api/settings", headers=_auth(t2)
                      ).json()["data"]["theme"] == ""               # 各管各的（默认橙）


def test_requires_login(client, login):
    assert client.get("/api/settings").status_code == 401
    assert client.post("/api/settings/recent-opens",
                       json={"item": "x"}).status_code == 401
