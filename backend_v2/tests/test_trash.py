"""验收单：统一回收站（规范 §3.11 · M4 · D10 两套合一）

覆盖：
  · 列表：admin 全部、其余只见自己名下（owner_id=主人）；?type= 筛；未登录 401
  · 恢复三分支：
      account → 原表状态翻回 active（行一直都在），仅 admin，登录立刻复活
      class   → 照快照插回行（成员关系没动过 → 复原即复原）；重名 4001
      asset   → 插回行 + 磁盘从 trash/{id} 搬回 assets/{id} + 排序账活着；重名 5001
  · 彻底删：仅 admin、仅内容类（class/asset）；账号条目永不提供 → 403
      class → 级联清成员/课堂记录（铁律：删对象清引用）
      asset → 清 file_order + 磁盘目录物理删除（彻底删才清账）
  · 清空：admin，内容类全清、账号条目保留
  · 越权/未知一律 404 不泄露
"""
from app import config
from app.db import SessionLocal
from app.models import Asset, ClassMember, ClassRecord, ClassRoom, FileOrder, Trash

PWD = "abcd1234"
_N = 0


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _login_as(client, username):
    resp = client.post("/api/auth/login", json={"username": username, "password": PWD})
    assert resp.json()["code"] == 0, resp.text
    return resp.json()["data"]["token"]


def _mk(client, admin_token, username, role):
    resp = client.post("/api/accounts",
                       json={"username": username, "password": PWD, "role": role},
                       headers=_auth(admin_token))
    assert resp.json()["code"] == 0, resp.text
    return resp.json()["data"]["id"]


def _setup(client, login):
    """admin + 老师t1/t2；返回 (admin, t1, t2)"""
    global _N
    _N += 1
    n = _N
    admin = login()["token"]
    _mk(client, admin, f"tr_t1{n}", "teacher")
    _mk(client, admin, f"tr_t2{n}", "teacher")
    return admin, _login_as(client, f"tr_t1{n}"), _login_as(client, f"tr_t2{n}")


def _trash_items(client, token, **params):
    return client.get("/api/trash", params=params, headers=_auth(token)).json()["data"]["items"]


# ---------------- 列表 ----------------

def test_list_scoped_and_type_filter(client, login):
    admin, t1, t2 = _setup(client, login)
    cls1 = client.post("/api/classes", json={"name": "删我"},
                       headers=_auth(t1)).json()["data"]["id"]
    client.delete(f"/api/classes/{cls1}", headers=_auth(t1))

    # t1 看得到自己删的班；t2 看不到
    assert any(i["type"] == "class" for i in _trash_items(client, t1))
    assert not any(i["type"] == "class" for i in _trash_items(client, t2))
    # admin 看得到全部（用 type 筛验证）
    assert any(i["type"] == "class" for i in _trash_items(client, admin))
    # 类型筛
    only_class = _trash_items(client, admin, type="class")
    assert only_class and all(i["type"] == "class" for i in only_class)
    # 未登录
    assert client.get("/api/trash").status_code == 401


# ---------------- 恢复：账号（D25：不进回收站，用户列表直接启用） ----------------

def test_disabled_account_absent_from_trash_and_patch_revives(client, login):
    admin, t1, _ = _setup(client, login)
    sid = _mk(client, admin, "tr_stu", "student")
    client.delete(f"/api/accounts/{sid}", headers=_auth(admin))
    # 停用后登录被拒
    assert client.post("/api/auth/login",
                       json={"username": "tr_stu", "password": PWD}
                       ).status_code in (401, 403)

    # D25（2026-10-10）：停用不写回收站——桶里找不到账号条目
    assert not any(i["type"] == "account" and i["name"] == "tr_stu"
                   for i in _trash_items(client, admin))

    # 启用：PATCH status=active → 复活可登录
    resp = client.patch(f"/api/accounts/{sid}", json={"status": "active"},
                        headers=_auth(admin))
    assert resp.json()["code"] == 0
    assert client.post("/api/auth/login",
                       json={"username": "tr_stu", "password": PWD}).json()["code"] == 0


def test_patch_status_requires_admin(client, login):
    admin, t1, _ = _setup(client, login)
    sid = _mk(client, admin, "tr_stu2", "student")
    resp = client.patch(f"/api/accounts/{sid}", json={"status": "active"},
                        headers=_auth(t1))
    assert resp.status_code == 403 and resp.json()["code"] == 2005


# ---------------- 恢复：班级 ----------------

def test_restore_class_keeps_members(client, login):
    admin, t1, _ = _setup(client, login)
    sid = _mk(client, admin, "tr_member", "student")
    cls1 = client.post("/api/classes", json={"name": "恢复班"},
                       headers=_auth(t1)).json()["data"]["id"]
    client.post(f"/api/classes/{cls1}/students", json={"account_id": sid},
                headers=_auth(t1))
    client.delete(f"/api/classes/{cls1}", headers=_auth(t1))
    assert not any(i["name"] == "恢复班" for i in
                   client.get("/api/classes", headers=_auth(t1)).json()["data"]["items"])

    item = next(i for i in _trash_items(client, t1) if i["name"] == "恢复班")
    assert client.post(f"/api/trash/{item['id']}/restore",
                       headers=_auth(t1)).json()["code"] == 0

    # 行回来了，成员关系【从来没动过】→ 学生还在班里
    got = client.get(f"/api/classes/{cls1}", headers=_auth(t1))
    assert got.json()["code"] == 0
    assert [s["username"] for s in got.json()["data"]["students"]] == ["tr_member"]
    assert not any(i["name"] == "恢复班" for i in _trash_items(client, t1))


def test_restore_class_name_conflict(client, login):
    admin, t1, _ = _setup(client, login)
    cls1 = client.post("/api/classes", json={"name": "重名班"},
                       headers=_auth(t1)).json()["data"]["id"]
    client.delete(f"/api/classes/{cls1}", headers=_auth(t1))
    # 删后又建了同名新班
    client.post("/api/classes", json={"name": "重名班"}, headers=_auth(t1))

    item = next(i for i in _trash_items(client, t1) if i["name"] == "重名班")
    resp = client.post(f"/api/trash/{item['id']}/restore", headers=_auth(t1))
    assert resp.status_code == 409 and resp.json()["code"] == 4001


# ---------------- 恢复：资产 ----------------

def test_restore_asset_moves_disk_back(client, login):
    admin, t1, _ = _setup(client, login)
    asset = client.post("/api/assets",
                        json={"bucket": "courses", "name": "恢复课", "type": "scratch"},
                        headers=_auth(t1)).json()["data"]
    client.post(f"/api/assets/{asset['id']}/files/upload", data={"path": "a.txt"},
                files={"file": ("a.txt", b"payload", "application/octet-stream")},
                headers=_auth(t1))
    client.post(f"/api/assets/{asset['id']}/files/order",
                json={"path": "", "names": ["a.txt"], "position": 0}, headers=_auth(t1))

    client.delete(f"/api/assets/{asset['id']}", headers=_auth(t1))
    root = config.DATA_ROOT / "assets" / asset["id"]
    moved = config.DATA_ROOT / "trash" / asset["id"]
    assert not root.exists() and moved.is_dir()                  # 磁盘搬进了回收站目录

    item = next(i for i in _trash_items(client, t1) if i["name"] == "恢复课")
    assert client.post(f"/api/trash/{item['id']}/restore",
                       headers=_auth(t1)).json()["code"] == 0

    assert root.is_dir() and (root / "a.txt").read_bytes() == b"payload"   # 目录搬回+内容在
    assert not moved.exists()
    db = SessionLocal()
    try:
        assert db.get(Asset, asset["id"]) is not None             # 行回来了
        assert db.query(FileOrder).filter_by(asset_id=asset["id"]).count() == 1  # 排序账活着
    finally:
        db.close()


def test_restore_asset_name_conflict(client, login):
    admin, t1, _ = _setup(client, login)
    asset = client.post("/api/assets",
                        json={"bucket": "courses", "name": "撞名课", "type": "scratch"},
                        headers=_auth(t1)).json()["data"]
    client.delete(f"/api/assets/{asset['id']}", headers=_auth(t1))
    client.post("/api/assets", json={"bucket": "courses", "name": "撞名课",
                                     "type": "scratch"}, headers=_auth(t1))

    item = next(i for i in _trash_items(client, t1) if i["name"] == "撞名课")
    resp = client.post(f"/api/trash/{item['id']}/restore", headers=_auth(t1))
    assert resp.status_code == 409 and resp.json()["code"] == 5001


# ---------------- 彻底删 ----------------

def test_purge_asset_destroys_everything(client, login):
    admin, t1, _ = _setup(client, login)
    asset = client.post("/api/assets",
                        json={"bucket": "reference", "name": "火化资料"},
                        headers=_auth(t1)).json()["data"]
    client.post(f"/api/assets/{asset['id']}/files/upload", data={"path": "x.txt"},
                files={"file": ("x.txt", b"x", "application/octet-stream")},
                headers=_auth(t1))

    client.delete(f"/api/assets/{asset['id']}", headers=_auth(t1))
    item = next(i for i in _trash_items(client, admin) if i["name"] == "火化资料")
    resp = client.delete(f"/api/trash/{item['id']}", headers=_auth(admin))
    assert resp.json()["code"] == 0

    db = SessionLocal()
    try:
        assert db.query(FileOrder).filter_by(asset_id=asset["id"]).count() == 0  # 彻底删才清账
        assert db.get(Trash, item["id"]) is None
    finally:
        db.close()
    assert not (config.DATA_ROOT / "trash" / asset["id"]).exists()  # 磁盘真没了
    assert not any(i["id"] == item["id"] for i in _trash_items(client, admin))


def test_purge_requires_admin_and_content_only(client, login):
    admin, t1, _ = _setup(client, login)
    # 老师不能彻底删
    cls1 = client.post("/api/classes", json={"name": "待火化"},
                       headers=_auth(t1)).json()["data"]["id"]
    client.delete(f"/api/classes/{cls1}", headers=_auth(t1))
    item = next(i for i in _trash_items(client, t1) if i["name"] == "待火化")
    assert client.delete(f"/api/trash/{item['id']}",
                         headers=_auth(t1)).status_code == 403

    # 账号条目永不提供彻底删（哪怕 admin）——D25 后正常流程不产生账号行，
    # 这里直接造一条存量行验证防御分支仍在
    db = SessionLocal()
    try:
        db.add(Trash(id="trpurglegacy1", type="account", owner_id="legacyacc01",
                     name="legacy_acc", payload={}, disk_path="",
                     deleted_at="2026-10-10 00:00:00"))
        db.commit()
    finally:
        db.close()
    resp = client.delete("/api/trash/trpurglegacy1", headers=_auth(admin))
    assert resp.status_code == 403 and resp.json()["code"] == 2005


def test_purge_class_clears_references(client, login):
    admin, t1, _ = _setup(client, login)
    sid = _mk(client, admin, "tr_ref_stu", "student")
    cls1 = client.post("/api/classes", json={"name": "清引用"},
                       headers=_auth(t1)).json()["data"]["id"]
    client.post(f"/api/classes/{cls1}/students", json={"account_id": sid},
                headers=_auth(t1))
    client.delete(f"/api/classes/{cls1}", headers=_auth(t1))
    item = next(i for i in _trash_items(client, admin) if i["name"] == "清引用")
    assert client.delete(f"/api/trash/{item['id']}",
                         headers=_auth(admin)).json()["code"] == 0

    db = SessionLocal()
    try:
        assert db.query(ClassMember).filter_by(class_id=cls1).count() == 0   # 铁律：清引用
        assert db.query(ClassRecord).filter_by(class_id=cls1).count() == 0
        assert db.get(ClassRoom, cls1) is None
        assert db.get(Trash, item["id"]) is None
    finally:
        db.close()


# ---------------- 清空 ----------------

def test_clear_purges_content_keeps_accounts(client, login):
    admin, t1, _ = _setup(client, login)
    cls1 = client.post("/api/classes", json={"name": "批量烧"},
                       headers=_auth(t1)).json()["data"]["id"]
    client.delete(f"/api/classes/{cls1}", headers=_auth(t1))
    sid = _mk(client, admin, "tr_keep_stu", "student")
    client.delete(f"/api/accounts/{sid}", headers=_auth(admin))

    resp = client.delete("/api/trash", headers=_auth(admin))
    assert resp.json()["code"] == 0

    remaining = _trash_items(client, admin)
    assert all(i["type"] == "account" for i in remaining)          # 账号条目保留
    assert not any(i["type"] == "class" for i in remaining)
    # 非 admin 不可用
    assert client.delete("/api/trash", headers=_auth(t1)).status_code == 403


def test_restore_unknown_404(client, login):
    admin, _, _ = _setup(client, login)
    assert client.post("/api/trash/zzzzzzzzzzzz/restore",
                       headers=_auth(admin)).status_code == 404
