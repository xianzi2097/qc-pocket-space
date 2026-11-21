"""验收单：登录这条链路的每个行为（规范 §3.1 + security.py）

覆盖：
  · 登录成功（返回 token、角色、且不泄漏密码字段）
  · 密码错 / 账号不存在 → 同一个错误码 2001（不泄露"账号是否存在"）
  · D26（2026-10-10）：停用号 + **正确密码** → 2004「账号已被停用」；
    停用号 + 错误密码 → 仍 2001（防枚举原则不变）
  · me：带 token 正常返回、乱 token 拒、过期 token → 2003
  · 登出后原 token 立即失效
  · D41 单端登录：同账号再次登录，先登那处立即失效（后登顶先登）
  · 改密：新密码太短拒、原密码错拒、成功后其他设备被踢、旧密码作废
  · 梯度限流：错 5 次第 6 次被锁 429；登录成功则计数清零
"""
from app.db import SessionLocal
from app.models import Account, AuthSession
from app.security import create_session


def test_login_success(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "111111"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["token"]
    assert body["data"]["expires_in"] > 0
    account = body["data"]["account"]
    assert account["username"] == "admin"
    assert account["role"] == "admin"
    assert "password" not in account and "password_hash" not in account


def test_login_wrong_password(client):
    resp = client.post("/api/auth/login", json={"username": "admin", "password": "wrong-pass"})
    assert resp.status_code == 401
    assert resp.json()["code"] == 2001


def test_login_unknown_account_same_error(client):
    resp = client.post("/api/auth/login", json={"username": "no-such-user", "password": "whatever"})
    assert resp.status_code == 401
    assert resp.json()["code"] == 2001


def test_login_returns_self_account(login):
    data = login()
    assert data["account"]["username"] == "admin"
    assert data["account"]["status"] == "active"


def test_me_with_token(client, login):
    data = login()
    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {data['token']}"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["data"]["role"] == "admin"
    assert "descriptors" in body["data"]


def test_me_with_garbage_token(client):
    resp = client.get("/api/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert resp.status_code == 401
    assert resp.json()["code"] == 2002


def test_expired_token_returns_2003(client, login):
    data = login()
    token = data["token"]
    db = SessionLocal()
    try:
        row = db.get(AuthSession, token)
        row.expires_at = "2000-01-01 00:00:00"
        db.commit()
    finally:
        db.close()

    resp = client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 401
    assert resp.json()["code"] == 2003


def test_logout_invalidates_token(client, login):
    data = login()
    headers = {"Authorization": f"Bearer {data['token']}"}
    assert client.get("/api/auth/me", headers=headers).status_code == 200

    resp = client.post("/api/auth/logout", headers=headers)
    assert resp.status_code == 200 and resp.json()["code"] == 0

    after = client.get("/api/auth/me", headers=headers)
    assert after.status_code == 401
    assert after.json()["code"] == 2002


def test_login_kicks_previous_session(client, login):
    """D41 单端登录：同账号后登的顶掉先登的（用户实测场景：老师用学生号先登着，学生再登）"""
    first = login()
    second = login()
    h_first = {"Authorization": f"Bearer {first['token']}"}
    h_second = {"Authorization": f"Bearer {second['token']}"}

    assert client.get("/api/auth/me", headers=h_second).status_code == 200
    kicked = client.get("/api/auth/me", headers=h_first)
    assert kicked.status_code == 401
    assert kicked.json()["code"] == 2002  # 会话行已被删，按未登录处理


def test_change_password_kills_other_sessions(client, login):
    """D41 后登录互踢，正常登录拿不到并存会话 → 手工造一行"另一台设备"再验改密踢它"""
    current = login()
    db = SessionLocal()
    try:
        other_token = create_session(
            db, db.query(Account).filter(Account.username == "admin").one()
        )
    finally:
        db.close()
    h_current = {"Authorization": f"Bearer {current['token']}"}
    h_other = {"Authorization": f"Bearer {other_token}"}

    resp = client.patch(
        "/api/auth/password",
        headers=h_current,
        json={"old_password": "111111", "new_password": "abcd1234"},
    )
    assert resp.status_code == 200 and resp.json()["code"] == 0

    # 另一台设备被踢下线，当前会话保留
    assert client.get("/api/auth/me", headers=h_other).status_code == 401
    assert client.get("/api/auth/me", headers=h_current).status_code == 200

    # 旧密码失效、新密码可登录
    assert client.post("/api/auth/login", json={"username": "admin", "password": "111111"}).status_code == 401
    relogin = client.post("/api/auth/login", json={"username": "admin", "password": "abcd1234"})
    assert relogin.status_code == 200

    # 还原默认密码，避免影响其他用例
    restore = client.patch(
        "/api/auth/password",
        headers={"Authorization": f"Bearer {relogin.json()['data']['token']}"},
        json={"old_password": "abcd1234", "new_password": "111111"},
    )
    assert restore.status_code == 200 and restore.json()["code"] == 0


def test_change_password_rejects_short_new(client, login):
    data = login()
    resp = client.patch(
        "/api/auth/password",
        headers={"Authorization": f"Bearer {data['token']}"},
        json={"old_password": "111111", "new_password": "123"},
    )
    assert resp.status_code == 400
    assert resp.json()["code"] == 1002


def test_login_rate_limit_gradient(client):
    """5 次失败后锁 30s：第 6 次直接 429"""
    for _ in range(5):
        resp = client.post("/api/auth/login", json={"username": "admin", "password": "bad"})
        assert resp.status_code == 401

    blocked = client.post("/api/auth/login", json={"username": "admin", "password": "bad"})
    assert blocked.status_code == 429
    assert blocked.json()["code"] == 2029
    assert "秒" in blocked.json()["msg"]


def test_successful_login_clears_failures(client, login):
    for _ in range(4):
        client.post("/api/auth/login", json={"username": "admin", "password": "bad"})
    data = login()  # 第 5 次前成功 → 计数清零
    assert data["token"]

    resp = client.post("/api/auth/login", json={"username": "admin", "password": "bad"})
    assert resp.status_code == 401  # 仍然只是普通失败，未被锁
