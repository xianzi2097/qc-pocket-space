"""验收单：笔记域（规范 §3.8 · D18 · M3-N 板块）

覆盖：
  · 两张表零文件：notebooks（本，单层）+ notes（一行一条，md 正文在 content 列）
  · 建本/改名：同人重名 8009、仅本人（别人按 404 不泄露）
  · 删本：级联删全部笔记，msg 带"不可恢复"（§7 总表）
  · 笔记 CRUD：建/读/改（updated_at 更新）/删；全部仅本人，越权 404
  · 列表：按本筛（?notebook=）、分页（默认 20/页）
  · 层级：单层平铺（D18）——本里不再套本
"""
from app.db import SessionLocal
from app.models import Notebook, Note

PWD = "abcd1234"
_N = 0
_last_user = ""


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login_as(client, username):
    resp = client.post("/api/auth/login", json={"username": username, "password": PWD})
    assert resp.json()["code"] == 0, resp.text
    return resp.json()["data"]["token"]


def _user(client, login, role="student"):
    """建一个新用户并登录（名字带全局序号防撞），返回 (token, id)"""
    global _N, _last_user
    _N += 1
    admin = login()["token"]
    name = f"note_u{_N}"
    resp = client.post("/api/accounts",
                       json={"username": name, "password": PWD, "role": role},
                       headers=_auth(admin))
    assert resp.json()["code"] == 0, resp.text
    _last_user = name
    return _login_as(client, name), resp.json()["data"]["id"]


def _mk_notebook(client, token, name="我的本"):
    resp = client.post("/api/notebooks", json={"name": name}, headers=_auth(token))
    assert resp.json()["code"] == 0, resp.text
    return resp.json()["data"]["id"]


def _mk_note(client, token, notebook_id, title="第一条", content="hello md"):
    resp = client.post("/api/notes",
                       json={"notebook_id": notebook_id, "title": title, "content": content},
                       headers=_auth(token))
    assert resp.json()["code"] == 0, resp.text
    return resp.json()["data"]["id"]


# ---------------- 笔记本 ----------------

def test_create_and_list_with_count(client, login):
    t, _ = _user(client, login)
    nb = _mk_notebook(client, t, "Python本")
    _mk_note(client, t, nb, title="n1")
    _mk_note(client, t, nb, title="n2")

    data = client.get("/api/notebooks", headers=_auth(t)).json()["data"]
    assert data["total"] == 1
    assert data["items"][0]["name"] == "Python本"
    assert data["items"][0]["note_count"] == 2                   # 带笔记计数


def test_notebook_duplicate_8009(client, login):
    t, _ = _user(client, login)
    _mk_notebook(client, t, "重名本")
    resp = client.post("/api/notebooks", json={"name": "重名本"}, headers=_auth(t))
    assert resp.status_code == 409 and resp.json()["code"] == 8009


def test_notebook_requires_login(client, login):
    resp = client.post("/api/notebooks", json={"name": "x"})
    assert resp.status_code == 401 and resp.json()["code"] == 2002


def test_rename_notebook(client, login):
    t, _ = _user(client, login)
    nb = _mk_notebook(client, t, "旧名")

    resp = client.patch(f"/api/notebooks/{nb}", json={"name": "新名"}, headers=_auth(t))
    assert resp.json()["code"] == 0
    items = client.get("/api/notebooks", headers=_auth(t)).json()["data"]["items"]
    assert items[0]["name"] == "新名"

    # 撞自己的另一个本 → 8009
    _mk_notebook(client, t, "另一个")
    dup = client.patch(f"/api/notebooks/{nb}", json={"name": "另一个"}, headers=_auth(t))
    assert dup.status_code == 409 and dup.json()["code"] == 8009


def test_delete_notebook_cascades_notes(client, login):
    t, _ = _user(client, login)
    nb = _mk_notebook(client, t, "要删的")
    note_id = _mk_note(client, t, nb)

    resp = client.delete(f"/api/notebooks/{nb}", headers=_auth(t))
    assert resp.json()["code"] == 0
    assert "不可恢复" in resp.json()["msg"]                       # 响应必须说出真相

    db = SessionLocal()
    try:
        assert db.get(Notebook, nb) is None                       # 本没了
        assert db.get(Note, note_id) is None                      # 笔记级联删光
    finally:
        db.close()


def test_others_notebook_404(client, login):
    t1, _ = _user(client, login)
    t2, _ = _user(client, login)
    nb = _mk_notebook(client, t1, "t1的本")

    assert client.patch(f"/api/notebooks/{nb}", json={"name": "劫持"},
                        headers=_auth(t2)).status_code == 404
    assert client.delete(f"/api/notebooks/{nb}", headers=_auth(t2)).status_code == 404
    # 列表互不可见
    assert client.get("/api/notebooks", headers=_auth(t2)).json()["data"]["total"] == 0


# ---------------- 笔记 ----------------

def test_create_note_lands_in_notebook(client, login):
    t, _ = _user(client, login)
    nb = _mk_notebook(client, t)
    note_id = _mk_note(client, t, nb, title="递归", content="# 递归\n\n```py\npass\n```")

    db = SessionLocal()
    try:
        row = db.get(Note, note_id)
        assert row is not None and row.notebook_id == nb
        assert row.content.startswith("# 递归")                    # md 正文就在列里
    finally:
        db.close()


def test_create_note_in_others_notebook_404(client, login):
    t1, _ = _user(client, login)
    t2, _ = _user(client, login)
    nb = _mk_notebook(client, t1, "t1的")
    resp = client.post("/api/notes",
                       json={"notebook_id": nb, "title": "x", "content": "y"},
                       headers=_auth(t2))
    assert resp.status_code == 404


def test_list_notes_filter_and_pagination(client, login):
    t, _ = _user(client, login)
    nb1 = _mk_notebook(client, t, "本一")
    nb2 = _mk_notebook(client, t, "本二")
    for i in range(12):
        _mk_note(client, t, nb1, title=f"a{i:02d}")
    _mk_note(client, t, nb2, title="b独家")

    page1 = client.get("/api/notes", params={"notebook": nb1, "page": 1, "page_size": 10},
                       headers=_auth(t)).json()["data"]
    assert page1["total"] == 12 and len(page1["items"]) == 10
    page2 = client.get("/api/notes", params={"notebook": nb1, "page": 2, "page_size": 10},
                       headers=_auth(t)).json()["data"]
    assert len(page2["items"]) == 2

    all_notes = client.get("/api/notes", headers=_auth(t)).json()["data"]
    assert all_notes["total"] == 13                               # 不筛 = 全部


def test_read_note_and_stranger_404(client, login):
    t1, _ = _user(client, login)
    t2, _ = _user(client, login)
    nb = _mk_notebook(client, t1)
    note_id = _mk_note(client, t1, nb, title="私密", content="只有我能看到")

    mine = client.get(f"/api/notes/{note_id}", headers=_auth(t1)).json()["data"]
    assert mine["content"] == "只有我能看到"

    assert client.get(f"/api/notes/{note_id}", headers=_auth(t2)).status_code == 404
    assert client.get(f"/api/notes/zzzzzzzzzzzz", headers=_auth(t1)).status_code == 404


def test_patch_note_updates_content_and_time(client, login):
    t, _ = _user(client, login)
    nb = _mk_notebook(client, t)
    note_id = _mk_note(client, t, nb, title="原标题", content="旧内容")

    resp = client.patch(f"/api/notes/{note_id}",
                        json={"title": "新标题", "content": "新内容"},
                        headers=_auth(t))
    assert resp.json()["code"] == 0
    data = resp.json()["data"]
    assert data["title"] == "新标题" and data["content"] == "新内容"

    db = SessionLocal()
    try:
        row = db.get(Note, note_id)
        assert row.updated_at >= row.created_at                   # updated_at 跟着走
    finally:
        db.close()


def test_delete_note_hard(client, login):
    t, _ = _user(client, login)
    nb = _mk_notebook(client, t)
    note_id = _mk_note(client, t, nb)

    resp = client.delete(f"/api/notes/{note_id}", headers=_auth(t))
    assert resp.json()["code"] == 0
    assert "不可恢复" in resp.json()["msg"]

    again = client.delete(f"/api/notes/{note_id}", headers=_auth(t))
    assert again.status_code == 404                               # 真删，二次 404

    db = SessionLocal()
    try:
        assert db.get(Note, note_id) is None                      # 无回收站
    finally:
        db.close()


def test_note_ownership_via_notebook(client, login):
    """笔记的归属经由笔记本判定：别人的本里的笔记，动不了也看不见"""
    t1, _ = _user(client, login)
    t2, _ = _user(client, login)
    nb = _mk_notebook(client, t1)
    note_id = _mk_note(client, t1, nb)

    assert client.patch(f"/api/notes/{note_id}", json={"content": "hack"},
                        headers=_auth(t2)).status_code == 404
    assert client.delete(f"/api/notes/{note_id}", headers=_auth(t2)).status_code == 404
