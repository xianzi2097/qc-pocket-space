"""验收单：账号域（规范 §3.2 + §4 权限矩阵）

覆盖：
  · 公开列表字段裁剪（未登录只见昵称/头像/角色，不见描述符与状态）
  · 创建：admin 建老师/学生、teacher 只能建学生、非法 role、重名、弱密码
  · 详情：需登录；本人/管理员看全字段，他人只看基本字段
  · 改资料：本人改昵称；描述符必须是 JSON；status 仅 admin；不能改别人
  · 停用（软删）：仅 admin、不能删自己、进回收站表、会话全部失效
  · 头像：仅学生本人、≤2MB、必须是图片、磁盘落盘 + 库字段同步、公开可取
"""
import json

from app import config
from app.db import SessionLocal
from app.models import Account, AuthSession, Trash


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create(client, token, username, role, password="abcd1234", display=""):
    body = {"username": username, "password": password, "role": role}
    if display:
        body["display_name"] = display
    return client.post("/api/accounts", json=body, headers=_auth(token))


def _make_student(client, admin_token, username="stu_x"):
    resp = _create(client, admin_token, username, "student")
    assert resp.status_code == 200 and resp.json()["code"] == 0, resp.text
    return resp.json()["data"]["id"]


# ---------------- 列表（公开） ----------------

def test_public_list_is_trimmed(client):
    resp = client.get("/api/accounts")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert isinstance(body["data"]["items"], list) and body["data"]["total"] >= 1

    allowed = {"id", "username", "role", "display_name", "avatar_path"}
    for item in body["data"]["items"]:
        assert set(item.keys()) <= allowed, f"公开列表泄漏字段：{set(item.keys()) - allowed}"
    # 默认 admin 在列且是 active
    admin = next(i for i in body["data"]["items"] if i["username"] == "admin")
    assert admin["role"] == "admin"


def test_public_list_role_filter(client, login):
    token = login()["token"]
    _create(client, token, "t_filter", "teacher")

    resp = client.get("/api/accounts", params={"role": "teacher"})
    roles = {i["role"] for i in resp.json()["data"]["items"]}
    assert roles == {"teacher"}


def test_admin_list_includes_full_fields(client, login):
    token = login()["token"]
    resp = client.get("/api/accounts", headers=_auth(token))
    items = resp.json()["data"]["items"]
    admin = next(i for i in items if i["username"] == "admin")
    assert "descriptors" in admin and "status" in admin and "created_at" in admin


# ---------------- 创建 ----------------

def test_create_requires_login(client):
    resp = client.post("/api/accounts", json={"username": "x", "password": "abcd1234", "role": "student"})
    assert resp.status_code == 401 and resp.json()["code"] == 2002


def test_admin_creates_teacher_and_can_login(client, login):
    admin_token = login()["token"]
    resp = _create(client, admin_token, "t_alpha", "teacher", password="abcd1234", display="张老师")
    assert resp.status_code == 200 and resp.json()["code"] == 0, resp.text
    account = resp.json()["data"]
    assert account["role"] == "teacher" and account["display_name"] == "张老师"
    assert "password" not in account and "password_hash" not in account

    # 落地断言：库里真有这行
    db = SessionLocal()
    try:
        row = db.query(Account).filter(Account.username == "t_alpha").first()
        assert row is not None and row.role == "teacher" and row.status == "active"
        assert row.password_hash.startswith("pbkdf2_sha256$")
    finally:
        db.close()

    # 新账号能登录
    login_resp = client.post("/api/auth/login", json={"username": "t_alpha", "password": "abcd1234"})
    assert login_resp.status_code == 200 and login_resp.json()["code"] == 0


def test_duplicate_username_409(client, login):
    token = login()["token"]
    assert _create(client, token, "t_dup", "teacher").json()["code"] == 0
    again = _create(client, token, "t_dup", "teacher")
    assert again.status_code == 409 and again.json()["code"] == 3001


def test_cannot_create_admin_role(client, login):
    token = login()["token"]
    resp = _create(client, token, "root_x", "admin")
    assert resp.status_code == 400 and resp.json()["code"] == 3003


def test_teacher_cannot_create_teacher(client, login):
    admin_token = login()["token"]
    _create(client, admin_token, "t_boss", "teacher")
    teacher_login = client.post("/api/auth/login", json={"username": "t_boss", "password": "abcd1234"})
    teacher_token = teacher_login.json()["data"]["token"]

    resp = _create(client, teacher_token, "t_rival", "teacher")
    assert resp.status_code == 403 and resp.json()["code"] == 3003


def test_teacher_creates_student(client, login):
    admin_token = login()["token"]
    _create(client, admin_token, "t_ok", "teacher")
    teacher_token = client.post("/api/auth/login", json={"username": "t_ok", "password": "abcd1234"}).json()["data"]["token"]

    resp = _create(client, teacher_token, "stu_ok", "student")
    assert resp.status_code == 200 and resp.json()["code"] == 0


def test_weak_password_rejected(client, login):
    token = login()["token"]
    resp = _create(client, token, "t_weak", "teacher", password="123")
    assert resp.status_code == 400 and resp.json()["code"] == 1002


# ---------------- 详情 ----------------

def test_detail_requires_login(client, login):
    admin_id = login()["account"]["id"]
    resp = client.get(f"/api/accounts/{admin_id}")
    assert resp.status_code == 401 and resp.json()["code"] == 2002


def test_get_own_detail_full(client, login):
    data = login()
    resp = client.get(f"/api/accounts/{data['account']['id']}", headers=_auth(data["token"]))
    assert resp.json()["code"] == 0
    assert "descriptors" in resp.json()["data"] and "status" in resp.json()["data"]


def test_get_others_detail_is_trimmed(client, login):
    admin = login()
    admin_id = admin["account"]["id"]
    other_id = _make_student(client, admin["token"], "stu_view")
    # 被查看者本人登录后看别人 → 只有基本字段
    stu_token = client.post("/api/auth/login", json={"username": "stu_view", "password": "abcd1234"}).json()["data"]["token"]
    resp = client.get(f"/api/accounts/{admin_id}", headers=_auth(stu_token))
    assert resp.json()["code"] == 0
    data = resp.json()["data"]
    assert "descriptors" not in data and "status" not in data
    assert data["id"] == admin_id
    assert other_id


def test_detail_unknown_id_404(client, login):
    token = login()["token"]
    resp = client.get("/api/accounts/zzzzzzzzzzzz", headers=_auth(token))
    assert resp.status_code == 404 and resp.json()["code"] == 3004


# ---------------- 改资料 ----------------

def test_patch_own_display_name(client, login):
    data = login()
    resp = client.patch(
        f"/api/accounts/{data['account']['id']}",
        json={"display_name": "改过的名字"},
        headers=_auth(data["token"]),
    )
    assert resp.json()["code"] == 0 and resp.json()["data"]["display_name"] == "改过的名字"

    db = SessionLocal()
    try:
        row = db.get(Account, data["account"]["id"])
        assert row.display_name == "改过的名字"
    finally:
        db.close()


def test_patch_others_forbidden(client, login):
    admin_token = login()["token"]
    victim_id = _make_student(client, admin_token, "stu_victim")
    # 另一个学生去改他
    _make_student(client, admin_token, "stu_attacker")
    attacker_token = client.post("/api/auth/login", json={"username": "stu_attacker", "password": "abcd1234"}).json()["data"]["token"]

    resp = client.patch(f"/api/accounts/{victim_id}", json={"display_name": "hack"}, headers=_auth(attacker_token))
    assert resp.status_code == 403 and resp.json()["code"] == 2005


def test_patch_invalid_descriptors_json(client, login):
    data = login()
    resp = client.patch(
        f"/api/accounts/{data['account']['id']}",
        json={"descriptors": "这不是 JSON"},
        headers=_auth(data["token"]),
    )
    assert resp.status_code == 400 and resp.json()["code"] == 1002


def test_patch_valid_descriptors(client, login):
    data = login()
    payload = json.dumps({"teacher": {"严": 3}}, ensure_ascii=False)
    resp = client.patch(
        f"/api/accounts/{data['account']['id']}",
        json={"descriptors": payload},
        headers=_auth(data["token"]),
    )
    assert resp.json()["code"] == 0
    db = SessionLocal()
    try:
        row = db.get(Account, data["account"]["id"])
        assert json.loads(row.descriptors)["teacher"]["严"] == 3
    finally:
        db.close()


def test_patch_status_requires_admin(client, login):
    data = login()  # admin 本人改自己 status 也要走 admin 判定，这里验证学生越权
    admin_token = data["token"]
    _make_student(client, admin_token, "stu_status")
    stu_token = client.post("/api/auth/login", json={"username": "stu_status", "password": "abcd1234"}).json()["data"]["token"]
    resp = client.patch(
        f"/api/accounts/{login()['account']['id']}",
        json={"status": "deleted"},
        headers=_auth(stu_token),
    )
    assert resp.status_code == 403 and resp.json()["code"] == 2005


# ---------------- 登录名修改（A4 · 2026-10-07 拍板：可改；学生一律不可） ----------------

def test_teacher_renames_own_username(client, login):
    admin = login()
    t_token = _teacher_token(client, admin["token"], "t_oldname")
    t_id = client.get("/api/auth/me", headers=_auth(t_token)).json()["data"]["id"]

    resp = client.patch(f"/api/accounts/{t_id}", json={"username": "t_newname"}, headers=_auth(t_token))
    assert resp.status_code == 200 and resp.json()["data"]["username"] == "t_newname"

    # 库里真的改了 + 旧名作废、新名可登录
    db = SessionLocal()
    try:
        assert db.get(Account, t_id).username == "t_newname"
    finally:
        db.close()
    assert client.post("/api/auth/login", json={"username": "t_oldname", "password": "abcd1234"}).status_code == 401
    assert client.post("/api/auth/login", json={"username": "t_newname", "password": "abcd1234"}).status_code == 200


def test_rename_to_taken_username_409(client, login):
    admin = login()
    _teacher_token(client, admin["token"], "t_owner_of_name")
    t2_token = _teacher_token(client, admin["token"], "t_wants_rename")
    t2_id = client.get("/api/auth/me", headers=_auth(t2_token)).json()["data"]["id"]

    resp = client.patch(f"/api/accounts/{t2_id}", json={"username": "t_owner_of_name"}, headers=_auth(t2_token))
    assert resp.status_code == 409 and resp.json()["code"] == 3001


def test_student_cannot_change_username(client, login):
    admin = login()
    _make_student(client, admin["token"], "stu_rename")
    stu_token = client.post("/api/auth/login",
                            json={"username": "stu_rename", "password": "abcd1234"}).json()["data"]["token"]
    stu_id = client.get("/api/auth/me", headers=_auth(stu_token)).json()["data"]["id"]

    resp = client.patch(f"/api/accounts/{stu_id}", json={"username": "stu_hacked"}, headers=_auth(stu_token))
    assert resp.status_code == 403 and resp.json()["code"] == 2005


def test_admin_renames_student(client, login):
    admin = login()
    stu_id = _make_student(client, admin["token"], "stu_old")
    resp = client.patch(f"/api/accounts/{stu_id}", json={"username": "stu_renamed"},
                        headers=_auth(admin["token"]))
    assert resp.json()["code"] == 0
    assert client.post("/api/auth/login",
                       json={"username": "stu_renamed", "password": "abcd1234"}).status_code == 200


# ---------------- 停用（软删 → 回收站） ----------------

def test_delete_requires_admin(client, login):
    admin_token = login()["token"]
    target_id = _make_student(client, admin_token, "stu_del1")
    _create(client, admin_token, "t_del", "teacher")
    teacher_token = client.post("/api/auth/login", json={"username": "t_del", "password": "abcd1234"}).json()["data"]["token"]

    resp = client.delete(f"/api/accounts/{target_id}", headers=_auth(teacher_token))
    assert resp.status_code == 403 and resp.json()["code"] == 2005


def test_delete_self_blocked(client, login):
    data = login()
    resp = client.delete(f"/api/accounts/{data['account']['id']}", headers=_auth(data["token"]))
    assert resp.status_code == 403 and resp.json()["code"] == 3010


def test_delete_soft_kills_sessions_and_lands_in_trash(client, login):
    admin_token = login()["token"]
    target_id = _make_student(client, admin_token, "stu_dead")
    stu_token = client.post("/api/auth/login", json={"username": "stu_dead", "password": "abcd1234"}).json()["data"]["token"]
    # 被删前能用
    assert client.get("/api/auth/me", headers=_auth(stu_token)).status_code == 200

    resp = client.delete(f"/api/accounts/{target_id}", headers=_auth(admin_token))
    assert resp.status_code == 200 and resp.json()["code"] == 0

    db = SessionLocal()
    try:
        row = db.get(Account, target_id)
        assert row.status == "deleted"
        trash = db.query(Trash).filter(Trash.type == "account", Trash.owner_id == target_id).first()
        assert trash is not None and trash.name == "stu_dead"
        # 会话全部清除
        assert db.query(AuthSession).filter(AuthSession.account_id == target_id).count() == 0
    finally:
        db.close()

    # 老 token 立即失效、也不能重新登录
    assert client.get("/api/auth/me", headers=_auth(stu_token)).status_code == 401
    again = client.post("/api/auth/login", json={"username": "stu_dead", "password": "abcd1234"})
    assert again.status_code == 401 and again.json()["code"] == 2001


# ---------------- 头像 ----------------

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


def test_student_uploads_avatar(client, login):
    admin_token = login()["token"]
    _make_student(client, admin_token, "stu_av")
    stu_token = client.post("/api/auth/login", json={"username": "stu_av", "password": "abcd1234"}).json()["data"]["token"]
    stu_id = client.get("/api/auth/me", headers=_auth(stu_token)).json()["data"]["id"]

    resp = client.post(
        f"/api/accounts/{stu_id}/avatar",
        files={"file": ("a.png", PNG, "image/png")},
        headers=_auth(stu_token),
    )
    assert resp.status_code == 200 and resp.json()["code"] == 0, resp.text

    # 磁盘落盘 + 库字段同步（双断言）
    db = SessionLocal()
    try:
        row = db.get(Account, stu_id)
        path = config.DATA_ROOT / row.avatar_path
        assert path.exists() and path.read_bytes() == PNG
        assert row.avatar_path.endswith(".png") and stu_id in row.avatar_path
    finally:
        db.close()


def test_avatar_requires_student_self(client, login):
    admin_token = login()["token"]
    _make_student(client, admin_token, "stu_owner")
    _make_student(client, admin_token, "stu_other")
    owner_token = client.post("/api/auth/login", json={"username": "stu_owner", "password": "abcd1234"}).json()["data"]["token"]
    other_token = client.post("/api/auth/login", json={"username": "stu_other", "password": "abcd1234"}).json()["data"]["token"]
    owner_id = client.get("/api/auth/me", headers=_auth(owner_token)).json()["data"]["id"]

    # 别人的头像 → 403
    stolen = client.post(f"/api/accounts/{owner_id}/avatar",
                         files={"file": ("a.png", PNG, "image/png")}, headers=_auth(other_token))
    assert stolen.status_code == 403 and stolen.json()["code"] == 2005

    # 老师自己的头像也不行（仅学生）
    _create(client, admin_token, "t_av", "teacher")
    t_token = client.post("/api/auth/login", json={"username": "t_av", "password": "abcd1234"}).json()["data"]["token"]
    t_id = client.get("/api/auth/me", headers=_auth(t_token)).json()["data"]["id"]
    teacher_try = client.post(f"/api/accounts/{t_id}/avatar",
                              files={"file": ("a.png", PNG, "image/png")}, headers=_auth(t_token))
    assert teacher_try.status_code == 403 and teacher_try.json()["code"] == 2005


def test_avatar_size_limit(client, login):
    admin_token = login()["token"]
    _make_student(client, admin_token, "stu_big")
    stu_token = client.post("/api/auth/login", json={"username": "stu_big", "password": "abcd1234"}).json()["data"]["token"]
    stu_id = client.get("/api/auth/me", headers=_auth(stu_token)).json()["data"]["id"]

    big = b"a" * (config.AVATAR_MAX_BYTES + 1)
    resp = client.post(f"/api/accounts/{stu_id}/avatar",
                       files={"file": ("a.png", big, "image/png")}, headers=_auth(stu_token))
    assert resp.status_code == 413 and resp.json()["code"] == 1113


def test_avatar_must_be_image(client, login):
    admin_token = login()["token"]
    _make_student(client, admin_token, "stu_txt")
    stu_token = client.post("/api/auth/login", json={"username": "stu_txt", "password": "abcd1234"}).json()["data"]["token"]
    stu_id = client.get("/api/auth/me", headers=_auth(stu_token)).json()["data"]["id"]

    resp = client.post(f"/api/accounts/{stu_id}/avatar",
                       files={"file": ("a.txt", b"hello", "text/plain")}, headers=_auth(stu_token))
    assert resp.status_code == 400 and resp.json()["code"] == 1002


def test_avatar_public_fetch(client, login):
    admin_token = login()["token"]
    _make_student(client, admin_token, "stu_get")
    stu_token = client.post("/api/auth/login", json={"username": "stu_get", "password": "abcd1234"}).json()["data"]["token"]
    stu_id = client.get("/api/auth/me", headers=_auth(stu_token)).json()["data"]["id"]

    # 没传过 → 库里是预设标记 avatar_00 → 接口 404，前端据此画 emoji（方案一约定）
    marker = client.get(f"/api/accounts/{stu_id}/avatar")
    assert marker.status_code == 404 and marker.json()["code"] == 1004

    client.post(f"/api/accounts/{stu_id}/avatar",
                files={"file": ("a.png", PNG, "image/png")}, headers=_auth(stu_token))
    got = client.get(f"/api/accounts/{stu_id}/avatar")   # 公开，不带 token
    assert got.status_code == 200 and got.headers["content-type"].startswith("image/")
    assert got.content == PNG


def test_avatar_missing_file_returns_404(client, login):
    """库里指向自定义图片但文件丢了 → 404/1004，前端降级画 emoji（不再有兜底图片）"""
    admin = login()
    _make_student(client, admin["token"], "stu_lost")
    stu_id = next(i["id"] for i in client.get("/api/accounts", headers=_auth(admin["token"])).json()["data"]["items"]
                  if i["username"] == "stu_lost")

    db = SessionLocal()
    try:
        row = db.get(Account, stu_id)
        row.avatar_path = "users/nonexistent/avatar.jpg"   # 模拟文件被清掉
        db.commit()
    finally:
        db.close()

    resp = client.get(f"/api/accounts/{stu_id}/avatar")
    assert resp.status_code == 404 and resp.json()["code"] == 1004


def test_avatar_storage_full_returns_507(client, login, monkeypatch):
    """磁盘写入失败（空间不足等）→ 507/9001，而不是泛化的 500 服务繁忙"""
    from pathlib import Path

    admin = login()
    stu_id = _make_student(client, admin["token"], "stu_full")
    stu_token = client.post("/api/auth/login",
                            json={"username": "stu_full", "password": "abcd1234"}).json()["data"]["token"]

    def boom(self, data):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(Path, "write_bytes", boom)
    resp = client.post(f"/api/accounts/{stu_id}/avatar",
                       files={"file": ("a.png", PNG, "image/png")}, headers=_auth(stu_token))
    assert resp.status_code == 507
    assert resp.json()["code"] == 9001


# ---------------- 批量注册（规范 §3.2 · 启动器导入用） ----------------

def _teacher_token(client, admin_token, username):
    _create(client, admin_token, username, "teacher")
    return client.post("/api/auth/login", json={"username": username, "password": "abcd1234"}).json()["data"]["token"]


def test_batch_register_creates_students(client, login):
    token = login()["token"]
    resp = client.post(
        "/api/accounts/batch",
        json={"role": "student", "usernames": ["批量甲", "批量乙", "批量丙"]},
        headers=_auth(token),
    )
    assert resp.status_code == 200 and resp.json()["code"] == 0, resp.text
    data = resp.json()["data"]
    assert data["created"] == 3 and data["skipped"] == []

    db = SessionLocal()
    try:
        for name in ("批量甲", "批量乙", "批量丙"):
            row = db.query(Account).filter(Account.username == name).first()
            assert row is not None and row.role == "student" and row.status == "active"
    finally:
        db.close()

    # 默认密码可登录
    assert client.post("/api/auth/login",
                       json={"username": "批量甲", "password": "123456"}).status_code == 200


def test_batch_register_requires_admin(client, login):
    admin = login()
    t_token = _teacher_token(client, admin["token"], "t_batch")
    resp = client.post("/api/accounts/batch",
                       json={"role": "student", "usernames": ["x1"]}, headers=_auth(t_token))
    assert resp.status_code == 403 and resp.json()["code"] == 2005


def test_batch_register_skips_existing(client, login):
    token = login()["token"]
    _create(client, token, "老账号", "student")
    resp = client.post("/api/accounts/batch",
                       json={"role": "student", "password": "abcd1234",
                             "usernames": ["老账号", "新账号"]},
                       headers=_auth(token))
    data = resp.json()["data"]
    assert data["created"] == 1 and data["usernames"] == ["新账号"]
    assert data["skipped"][0]["username"] == "老账号" and "已存在" in data["skipped"][0]["reason"]


def test_batch_register_weak_password(client, login):
    resp = client.post("/api/accounts/batch",
                       json={"role": "student", "password": "123", "usernames": ["weak1"]},
                       headers=_auth(login()["token"]))
    assert resp.status_code == 400 and resp.json()["code"] == 1002


def test_batch_register_empty_list(client, login):
    resp = client.post("/api/accounts/batch",
                       json={"role": "student", "usernames": []},
                       headers=_auth(login()["token"]))
    assert resp.status_code == 400 and resp.json()["code"] in (1001, 1002)


# ---------------- 管理员重置密码 ----------------

def test_admin_resets_password_and_kills_sessions(client, login):
    admin = login()
    stu_id = _make_student(client, admin["token"], "stu_reset")
    stu_token = client.post("/api/auth/login",
                            json={"username": "stu_reset", "password": "abcd1234"}).json()["data"]["token"]
    assert client.get("/api/auth/me", headers=_auth(stu_token)).status_code == 200

    resp = client.post(f"/api/accounts/{stu_id}/password",
                       json={"new_password": "zhong1234"}, headers=_auth(admin["token"]))
    assert resp.status_code == 200 and resp.json()["code"] == 0

    # 旧密码失效、新密码生效、旧会话全失效
    assert client.post("/api/auth/login",
                       json={"username": "stu_reset", "password": "abcd1234"}).status_code == 401
    assert client.post("/api/auth/login",
                       json={"username": "stu_reset", "password": "zhong1234"}).status_code == 200
    assert client.get("/api/auth/me", headers=_auth(stu_token)).status_code == 401


def test_reset_requires_admin(client, login):
    admin = login()
    stu_id = _make_student(client, admin["token"], "stu_r2")
    t_token = _teacher_token(client, admin["token"], "t_reset")
    resp = client.post(f"/api/accounts/{stu_id}/password",
                       json={"new_password": "abcdefgh"}, headers=_auth(t_token))
    assert resp.status_code == 403 and resp.json()["code"] == 2005


def test_reset_short_password(client, login):
    admin = login()
    stu_id = _make_student(client, admin["token"], "stu_r3")
    resp = client.post(f"/api/accounts/{stu_id}/password",
                       json={"new_password": "123"}, headers=_auth(admin["token"]))
    assert resp.status_code == 400 and resp.json()["code"] == 1002


def test_reset_unknown_account(client, login):
    resp = client.post("/api/accounts/zzzzzzzzzzzz/password",
                       json={"new_password": "abcdefgh"}, headers=_auth(login()["token"]))
    assert resp.status_code == 404 and resp.json()["code"] == 3004


# ---------------- emoji 预设头像（方案一：清单归前端，后端只存标记） ----------------

def test_choose_preset_avatar(client, login):
    admin = login()
    stu_id = _make_student(client, admin["token"], "stu_pre")
    stu_token = client.post("/api/auth/login",
                            json={"username": "stu_pre", "password": "abcd1234"}).json()["data"]["token"]

    resp = client.put(f"/api/accounts/{stu_id}/avatar/preset",
                      json={"preset": "avatar_03"}, headers=_auth(stu_token))
    assert resp.status_code == 200 and resp.json()["code"] == 0

    # 只存标记串，不落任何图片文件
    db = SessionLocal()
    try:
        assert db.get(Account, stu_id).avatar_path == "avatar_03"
    finally:
        db.close()

    # 预设标记 → 图片接口 404，前端据此本地画 emoji
    got = client.get(f"/api/accounts/{stu_id}/avatar")
    assert got.status_code == 404 and got.json()["code"] == 1004


def test_choose_preset_only_self(client, login):
    admin = login()
    a_id = _make_student(client, admin["token"], "stu_pa")
    _make_student(client, admin["token"], "stu_pb")
    a_token = client.post("/api/auth/login",
                          json={"username": "stu_pa", "password": "abcd1234"}).json()["data"]["token"]
    b_id = next(i["id"] for i in client.get("/api/accounts", headers=_auth(admin["token"])).json()["data"]["items"]
                if i["username"] == "stu_pb")

    # 改别人的 → 403
    steal = client.put(f"/api/accounts/{b_id}/avatar/preset",
                       json={"preset": "avatar_02"}, headers=_auth(a_token))
    assert steal.status_code == 403 and steal.json()["code"] == 2005

    # 老师也能给自己选（任意角色，仅限本人）
    t_token = _teacher_token(client, admin["token"], "t_pre")
    t_id = next(i["id"] for i in client.get("/api/accounts", headers=_auth(admin["token"])).json()["data"]["items"]
                if i["username"] == "t_pre")
    mine = client.put(f"/api/accounts/{t_id}/avatar/preset",
                      json={"preset": "avatar_05"}, headers=_auth(t_token))
    assert mine.status_code == 200 and mine.json()["code"] == 0


def test_choose_unknown_preset(client, login):
    admin = login()
    stu_id = _make_student(client, admin["token"], "stu_bad")
    stu_token = client.post("/api/auth/login",
                            json={"username": "stu_bad", "password": "abcd1234"}).json()["data"]["token"]
    for bad in ("avatar_abc", "emoji01", "avatar_1234"):
        resp = client.put(f"/api/accounts/{stu_id}/avatar/preset",
                          json={"preset": bad}, headers=_auth(stu_token))
        assert resp.status_code == 400 and resp.json()["code"] == 1002, bad
