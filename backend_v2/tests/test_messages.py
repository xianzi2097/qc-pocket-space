"""验收单：站内信域（规范 §3.7 · D18 · M3-L 板块）

覆盖：
  · 发送：仅 teacher（admin 也不发）、收件人必须在世学生、默认进对方收件箱
  · 收件箱：只看自己的、分页（默认 20/页）、已发送 = 同表按 sender 查（方案 A）
  · 读 = 自动置已读（收件人读才置；发件人读自己的已发送不改状态）
  · 改：仅收件人（移夹/改已读）；移到别人的夹 → 404
  · 删：仅收件人、真删不进回收站、响应带"不可恢复"（单份语义）
  · 文件夹：内置收件箱懒创建、每夹计数/未读、同名拦、
            改名信件零动（folder_id 不变）、删夹信退回收件箱（一条 UPDATE 一封不丢）
  · 未读角标：unread-count 与实际一致
  · 层级：单层平铺（D18）——本文件只测平铺语义
"""
from app.db import SessionLocal
from app.models import Message, MessageFolder

PWD = "abcd1234"
_N = 0                                  # 跨用例账号名序号（防 3001 撞名）
_last_s2 = ""                           # 最近一次 _setup 的学生2用户名


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login_as(client, username):
    resp = client.post("/api/auth/login", json={"username": username, "password": PWD})
    assert resp.json()["code"] == 0, resp.text
    return resp.json()["data"]["token"]


def _mk_account(client, admin_token, username, role):
    resp = client.post("/api/accounts",
                       json={"username": username, "password": PWD, "role": role},
                       headers=_auth(admin_token))
    assert resp.json()["code"] == 0, resp.text
    return resp.json()["data"]["id"]


def _send(client, token, receiver_id, title="你好", content="```python\nprint(1)\n```"):
    return client.post("/api/messages",
                       json={"receiver_id": receiver_id, "title": title, "content": content},
                       headers=_auth(token))


def _setup(client, login):
    """一个老师 + 两个学生（名字带全局序号防跨用例撞名），返回 (t, s1, s1t, s2)"""
    global _N, _last_s2
    _N += 1
    n = _N
    admin = login()["token"]
    _mk_account(client, admin, f"msg_t{n}", "teacher")
    s1 = _mk_account(client, admin, f"msg_s1{n}", "student")
    s2 = _mk_account(client, admin, f"msg_s2{n}", "student")
    t = _login_as(client, f"msg_t{n}")
    s1t = _login_as(client, f"msg_s1{n}")
    _last_s2 = f"msg_s2{n}"
    return t, s1, s1t, s2


def _s2_token(client):
    """学生2 登录（用户名由最近一次 _setup 记下）"""
    return _login_as(client, _last_s2)


# ---------------- 发送 ----------------

def test_send_lands_in_receiver_inbox(client, login):
    t, s1, s1t, _ = _setup(client, login)
    resp = _send(client, t, s1, title="第一封")
    assert resp.json()["code"] == 0, resp.text

    db = SessionLocal()
    try:
        row = db.query(Message).filter(Message.title == "第一封").first()
        assert row is not None and row.receiver_id == s1 and row.is_read is False
        folder = db.get(MessageFolder, row.folder_id)
        assert folder.owner_id == s1 and folder.builtin is True      # 落在对方内置收件箱
    finally:
        db.close()


def test_send_forbidden_for_admin_and_student(client, login):
    t, s1, s1t, s2 = _setup(client, login)
    admin = login()["token"]

    admin_send = _send(client, admin, s1)
    assert admin_send.status_code == 403 and admin_send.json()["code"] == 2005

    stu_send = _send(client, s1t, s2)
    assert stu_send.status_code == 403 and stu_send.json()["code"] == 2005


def test_send_to_invalid_receiver(client, login):
    t, _, _, _ = _setup(client, login)
    missing = _send(client, t, "zzzzzzzzzzzz")
    assert missing.status_code == 404 and missing.json()["code"] == 3004


def test_send_requires_login(client, login):
    resp = client.post("/api/messages", json={"receiver_id": "x", "title": "t", "content": "c"})
    assert resp.status_code == 401 and resp.json()["code"] == 2002


# ---------------- 收件箱 / 已发送 ----------------

def test_inbox_only_sees_own(client, login):
    t, s1, s1t, s2 = _setup(client, login)
    _send(client, t, s1, title="给s1")

    inbox1 = client.get("/api/messages", headers=_auth(s1t)).json()["data"]
    assert inbox1["total"] == 1 and inbox1["items"][0]["title"] == "给s1"

    s2t = _s2_token(client)
    inbox2 = client.get("/api/messages", headers=_auth(s2t)).json()["data"]
    assert inbox2["total"] == 0                                 # 别人的信看不见


def test_sent_box_via_sender_query(client, login):
    t, s1, s1t, _ = _setup(client, login)
    _send(client, t, s1, title="发出的")

    sent = client.get("/api/messages", params={"box": "sent"}, headers=_auth(t)).json()["data"]
    assert sent["total"] == 1 and sent["items"][0]["title"] == "发出的"

    # 老师的收件箱（没人发他）是空的——同一张表两个视角
    inbox = client.get("/api/messages", headers=_auth(t)).json()["data"]
    assert inbox["total"] == 0


def test_pagination(client, login):
    t, s1, s1t, _ = _setup(client, login)
    for i in range(12):
        _send(client, t, s1, title=f"信{i:02d}")

    page1 = client.get("/api/messages", params={"page": 1, "page_size": 10},
                       headers=_auth(s1t)).json()["data"]
    assert page1["total"] == 12 and len(page1["items"]) == 10
    page2 = client.get("/api/messages", params={"page": 2, "page_size": 10},
                       headers=_auth(s1t)).json()["data"]
    assert len(page2["items"]) == 2
    # 默认分页 20/页
    default = client.get("/api/messages", headers=_auth(s1t)).json()["data"]
    assert default["page_size"] == 20


# ---------------- 读 = 自动已读 ----------------

def test_read_marks_read_and_count_drops(client, login):
    t, s1, s1t, _ = _setup(client, login)
    mid = _send(client, t, s1).json()["data"]["id"]

    count1 = client.get("/api/messages/unread-count", headers=_auth(s1t)).json()["data"]["count"]
    assert count1 == 1

    detail = client.get(f"/api/messages/{mid}", headers=_auth(s1t)).json()["data"]
    assert detail["content"].startswith("```python")              # 正文 md 拿得到
    assert detail["is_read"] is True                              # 读了就已读

    count2 = client.get("/api/messages/unread-count", headers=_auth(s1t)).json()["data"]["count"]
    assert count2 == 0


def test_sender_reading_own_does_not_mark_read(client, login):
    t, s1, _, _ = _setup(client, login)
    mid = _send(client, t, s1).json()["data"]["id"]

    client.get(f"/api/messages/{mid}", headers=_auth(t))          # 发件人看自己的已发送

    db = SessionLocal()
    try:
        assert db.get(Message, mid).is_read is False              # 收件人的未读不受影响
    finally:
        db.close()


def test_detail_stranger_forbidden(client, login):
    t, s1, _, s2 = _setup(client, login)
    mid = _send(client, t, s1).json()["data"]["id"]
    s2t = _s2_token(client)
    resp = client.get(f"/api/messages/{mid}", headers=_auth(s2t))
    assert resp.status_code == 404 and resp.json()["code"] == 7004


# ---------------- 改：仅收件人 ----------------

def test_receiver_moves_and_unread_toggle(client, login):
    t, s1, s1t, _ = _setup(client, login)
    mid = _send(client, t, s1).json()["data"]["id"]
    fid = client.post("/api/message-folders", json={"name": "课堂代码"},
                      headers=_auth(s1t)).json()["data"]["id"]

    moved = client.patch(f"/api/messages/{mid}", json={"folder_id": fid}, headers=_auth(s1t))
    assert moved.json()["code"] == 0

    client.patch(f"/api/messages/{mid}", json={"is_read": False}, headers=_auth(s1t))
    db = SessionLocal()
    try:
        row = db.get(Message, mid)
        assert row.folder_id == fid and row.is_read is False
    finally:
        db.close()


def test_sender_cannot_modify(client, login):
    t, s1, _, _ = _setup(client, login)
    mid = _send(client, t, s1).json()["data"]["id"]
    resp = client.patch(f"/api/messages/{mid}", json={"is_read": True}, headers=_auth(t))
    assert resp.status_code == 403 and resp.json()["code"] == 2005


def test_move_to_others_folder_404(client, login):
    t, s1, s1t, s2 = _setup(client, login)
    s2t = _s2_token(client)
    fid = client.post("/api/message-folders", json={"name": "s2的夹"},
                      headers=_auth(s2t)).json()["data"]["id"]
    mid = _send(client, t, s1).json()["data"]["id"]

    resp = client.patch(f"/api/messages/{mid}", json={"folder_id": fid}, headers=_auth(s1t))
    assert resp.status_code == 404


# ---------------- 删：仅收件人、真删 ----------------

def test_delete_by_receiver_hard(client, login):
    t, s1, s1t, _ = _setup(client, login)
    mid = _send(client, t, s1).json()["data"]["id"]

    resp = client.delete(f"/api/messages/{mid}", headers=_auth(s1t))
    assert resp.json()["code"] == 0
    assert "不可恢复" in resp.json()["msg"]                       # msg 必须说出真相

    db = SessionLocal()
    try:
        assert db.get(Message, mid) is None                       # 真删，无回收站
    finally:
        db.close()


def test_delete_by_sender_forbidden(client, login):
    t, s1, _, _ = _setup(client, login)
    mid = _send(client, t, s1).json()["data"]["id"]
    resp = client.delete(f"/api/messages/{mid}", headers=_auth(t))
    assert resp.status_code == 403 and resp.json()["code"] == 2005


# ---------------- 文件夹 ----------------

def test_folders_auto_inbox_with_counts(client, login):
    t, s1, s1t, _ = _setup(client, login)
    _send(client, t, s1, title="m1")

    data = client.get("/api/message-folders", headers=_auth(s1t)).json()["data"]
    inbox = next(f for f in data["items"] if f["builtin"])
    assert inbox["name"] == "收件箱" and inbox["count"] == 1 and inbox["unread"] == 1
    assert data["total_unread"] == 1


def test_create_folder_conflict(client, login):
    _, _, s1t, _ = _setup(client, login)
    ok1 = client.post("/api/message-folders", json={"name": "Python课堂"}, headers=_auth(s1t))
    assert ok1.json()["code"] == 0

    dup = client.post("/api/message-folders", json={"name": "Python课堂"}, headers=_auth(s1t))
    assert dup.status_code == 409 and dup.json()["code"] == 1009

    bad = client.post("/api/message-folders", json={"name": "  "}, headers=_auth(s1t))
    assert bad.status_code == 400


def test_rename_folder_keeps_messages_attached(client, login):
    t, s1, s1t, _ = _setup(client, login)
    fid = client.post("/api/message-folders", json={"name": "旧名"}, headers=_auth(s1t)
                      ).json()["data"]["id"]
    mid = _send(client, t, s1).json()["data"]["id"]
    client.patch(f"/api/messages/{mid}", json={"folder_id": fid}, headers=_auth(s1t))

    resp = client.patch(f"/api/message-folders/{fid}", json={"name": "新名"},
                        headers=_auth(s1t))
    assert resp.json()["code"] == 0

    db = SessionLocal()
    try:
        assert db.get(Message, mid).folder_id == fid                # 改名信件零动（指针红利）
    finally:
        db.close()


def test_builtin_folder_protected(client, login):
    _, _, s1t, _ = _setup(client, login)
    items = client.get("/api/message-folders", headers=_auth(s1t)).json()["data"]["items"]
    inbox = items[0]                                                # 排序：内置在最前
    assert inbox["builtin"] is True and inbox["name"] == "收件箱"

    rn = client.patch(f"/api/message-folders/{inbox['id']}", json={"name": "改名"},
                      headers=_auth(s1t))
    assert rn.status_code == 403 and rn.json()["code"] == 7010

    dl = client.delete(f"/api/message-folders/{inbox['id']}", headers=_auth(s1t))
    assert dl.status_code == 403 and dl.json()["code"] == 7010


def test_delete_folder_messages_retreat_to_inbox(client, login):
    t, s1, s1t, _ = _setup(client, login)
    fid = client.post("/api/message-folders", json={"name": "临时"},
                      headers=_auth(s1t)).json()["data"]["id"]
    mid = _send(client, t, s1).json()["data"]["id"]
    client.patch(f"/api/messages/{mid}", json={"folder_id": fid}, headers=_auth(s1t))

    resp = client.delete(f"/api/message-folders/{fid}", headers=_auth(s1t))
    assert resp.json()["code"] == 0

    db = SessionLocal()
    try:
        assert db.get(MessageFolder, fid) is None                 # 夹没了
        row = db.get(Message, mid)                                # 信还在
        assert row is not None
        inbox = db.query(MessageFolder).filter_by(owner_id=s1, builtin=True).first()
        assert row.folder_id == inbox.id                          # 而且退回了收件箱
    finally:
        db.close()


def test_folder_others_404(client, login):
    _, _, s1t, s2 = _setup(client, login)
    s2t = _s2_token(client)
    fid = client.post("/api/message-folders", json={"name": "s2的"},
                      headers=_auth(s2t)).json()["data"]["id"]
    # 学生1 改/删学生2的夹都不行（按不存在报）
    assert client.patch(f"/api/message-folders/{fid}", json={"name": "x"},
                        headers=_auth(s1t)).status_code == 404
    assert client.delete(f"/api/message-folders/{fid}",
                         headers=_auth(s1t)).status_code == 404


def test_unread_count_matches_actual(client, login):
    t, s1, s1t, _ = _setup(client, login)
    for i in range(3):
        _send(client, t, s1, title=f"n{i}")
    mids = [m["id"] for m in client.get("/api/messages",
                                        headers=_auth(s1t)).json()["data"]["items"]]
    assert client.get("/api/messages/unread-count",
                      headers=_auth(s1t)).json()["data"]["count"] == 3

    client.get(f"/api/messages/{mids[0]}", headers=_auth(s1t))    # 读一封
    assert client.get("/api/messages/unread-count",
                      headers=_auth(s1t)).json()["data"]["count"] == 2
