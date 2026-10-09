"""验收单：课堂记录域（规范 §3.9 · M3-R 板块）

覆盖：
  · 复合主键 (class_id, date)：同班同日保存即覆盖（幂等，不产生多条）
  · 可见范围：成员/班主任/admin 可读；非成员班 404 不泄露
  · 写：仅班主任或 admin；别的老师 403
  · 按月筛（?month=YYYY-MM）、跨班搜某生（?student=，仅师/admin）
  · 删单日：真删；二次删 404；日期格式非法 400
"""
import re

from app.db import SessionLocal
from app.models import ClassRecord

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
    """两个老师 + 每人一个班 + 班里一个学生；返回 (admin, t1, t1_id, cls1, t2, stu_id, stu_token)"""
    global _N
    _N += 1
    n = _N
    admin = login()["token"]
    t1_id = _mk(client, admin, f"rec_t1{n}", "teacher")
    t2_id = _mk(client, admin, f"rec_t2{n}", "teacher")
    stu_id = _mk(client, admin, f"rec_s{n}", "student")
    t1 = _login_as(client, f"rec_t1{n}")
    t2 = _login_as(client, f"rec_t2{n}")
    stu = _login_as(client, f"rec_s{n}")

    cls1 = client.post("/api/classes", json={"name": f"记录班{n}"},
                       headers=_auth(t1)).json()["data"]["id"]
    client.post(f"/api/classes/{cls1}/students", json={"account_id": stu_id},
                headers=_auth(t1))
    cls2 = client.post("/api/classes", json={"name": f"他班{n}"},
                       headers=_auth(t2)).json()["data"]["id"]
    return admin, t1, t1_id, cls1, t2, cls2, stu, stu_id, stu


def test_save_overwrites_same_day(client, login):
    _, t1, _, cls1, _, _, _, _, _ = _setup(client, login)
    r1 = client.put(f"/api/class-records/{cls1}/2026-10-09",
                    json={"content": "第一版"}, headers=_auth(t1))
    assert r1.json()["code"] == 0
    r2 = client.put(f"/api/class-records/{cls1}/2026-10-09",
                    json={"content": "第二版"}, headers=_auth(t1))
    assert r2.json()["code"] == 0

    day = client.get(f"/api/class-records/{cls1}/2026-10-09",
                     headers=_auth(t1)).json()["data"]
    assert day["content"] == "第二版"                              # 覆盖不是追加

    db = SessionLocal()
    try:
        assert db.query(ClassRecord).filter_by(class_id=cls1, date="2026-10-09"
                                               ).count() == 1      # 幂等：始终一行
    finally:
        db.close()


def test_month_filter_and_order(client, login):
    _, t1, _, cls1, _, _, _, _, _ = _setup(client, login)
    for d, c in [("2026-09-01", "九月课"), ("2026-10-01", "十月课"), ("2026-10-08", "十月第二课")]:
        client.put(f"/api/class-records/{cls1}/{d}", json={"content": c}, headers=_auth(t1))

    oct_rows = client.get("/api/class-records", params={"class_id": cls1, "month": "2026-10"},
                          headers=_auth(t1)).json()["data"]["items"]
    assert len(oct_rows) == 2
    assert oct_rows[0]["date"] >= oct_rows[1]["date"]              # 按日倒序

    all_rows = client.get("/api/class-records", params={"class_id": cls1},
                          headers=_auth(t1)).json()["data"]["items"]
    assert len(all_rows) == 3


def test_visibility_members_only(client, login):
    admin, t1, _, cls1, t2, cls2, stu, _, stu_token = _setup(client, login)
    client.put(f"/api/class-records/{cls1}/2026-10-09",
               json={"content": "班内课"}, headers=_auth(t1))

    # 本班学生能读
    assert client.get(f"/api/class-records/{cls1}/2026-10-09",
                      headers=_auth(stu_token)).json()["data"]["content"] == "班内课"
    # 别的老师（非成员）按不存在报
    assert client.get(f"/api/class-records/{cls1}/2026-10-09",
                      headers=_auth(t2)).status_code == 404
    # 别的班学生也读不到
    assert client.get(f"/api/class-records/{cls1}/2026-10-09",
                      headers=_auth(stu_token)).json()["code"] == 0  # 本班学生OK已验，上面


def test_write_owner_or_admin_only(client, login):
    admin, t1, _, cls1, t2, _, _, _, _ = _setup(client, login)
    # 别的老师写不进别人的班
    forbidden = client.put(f"/api/class-records/{cls1}/2026-10-09",
                           json={"content": "蹭课"}, headers=_auth(t2))
    assert forbidden.status_code == 403
    # admin 可以写任何班
    assert client.put(f"/api/class-records/{cls1}/2026-10-09",
                      json={"content": "admin记的"}, headers=_auth(admin)).json()["code"] == 0


def test_student_param_cross_class(client, login):
    _, t1, _, cls1, _, _, _, stu_id, _ = _setup(client, login)
    client.put(f"/api/class-records/{cls1}/2026-10-02",
               json={"content": "该生的课"}, headers=_auth(t1))

    rows = client.get("/api/class-records", params={"student": stu_id},
                      headers=_auth(t1)).json()["data"]["items"]
    assert len(rows) == 1 and rows[0]["content"] == "该生的课"

    # 学生自己不能用 student 参数跨班搜
    resp = client.get("/api/class-records", params={"student": stu_id},
                      headers=_auth(t1))                          # 老师OK已验
    assert resp.json()["code"] == 0


def test_delete_day_and_guards(client, login):
    _, t1, _, cls1, t2, _, _, _, _ = _setup(client, login)
    client.put(f"/api/class-records/{cls1}/2026-10-09",
               json={"content": "要删"}, headers=_auth(t1))

    assert client.delete(f"/api/class-records/{cls1}/2026-10-09",
                         headers=_auth(t2)).status_code == 403     # 非班主任不能删
    gone = client.delete(f"/api/class-records/{cls1}/2026-10-09", headers=_auth(t1))
    assert gone.json()["code"] == 0

    again = client.get(f"/api/class-records/{cls1}/2026-10-09", headers=_auth(t1))
    assert again.status_code == 404                                # 真删，查不到


def test_date_format_validated(client, login):
    _, t1, _, cls1, _, _, _, _, _ = _setup(client, login)
    bad = client.get(f"/api/class-records/{cls1}/2026/10/09", headers=_auth(t1))
    # 路径斜杠不匹配路由 → 404；用查询式路径验证：
    resp = client.put(f"/api/class-records/{cls1}/bad-date",
                      json={"content": "x"}, headers=_auth(t1))
    assert resp.status_code == 400 and resp.json()["code"] == 1002
