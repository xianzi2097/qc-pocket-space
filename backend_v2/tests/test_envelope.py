"""验收单：错误返回格式对不对（信封规范 §1）

覆盖：
  · 成功信封形状 code/msg/data
  · 未知路由 404 → 信封 code 1004（不是裸 {"detail":...}）
  · 无 token / 格式错误的 token → 401 + code 2002
  · 参数缺失 → 400 + code 1001，且 data 为 null
"""


def test_health_envelope(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["code"] == 0
    assert body["msg"] == "ok"
    assert body["data"]["status"] == "up"


def test_unknown_route_returns_envelope_404(client):
    resp = client.get("/api/nope")
    assert resp.status_code == 404
    body = resp.json()
    assert body["code"] == 1004
    assert body["data"] is None


def test_protected_route_without_token(client):
    resp = client.get("/api/auth/me")
    assert resp.status_code == 401
    body = resp.json()
    assert body["code"] == 2002
    assert body["msg"]


def test_malformed_bearer_rejected(client):
    resp = client.get("/api/auth/me", headers={"Authorization": "Token abc"})
    assert resp.status_code == 401
    assert resp.json()["code"] == 2002


def test_validation_missing_field_is_400_with_code(client):
    resp = client.post("/api/auth/login", json={"username": "admin"})
    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == 1001
    assert "password" in body["msg"]
    assert body["data"] is None
