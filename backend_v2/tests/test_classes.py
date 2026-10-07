"""验收单：班级与学生域（规范 §3.3 + §4 权限矩阵）

覆盖：
  · 班级可见范围：teacher 只见自己的、admin 见全部、学生见自己所在的
  · 建班：需 teacher/admin、重名 409（按老师维度重名，不同老师可同名）
  · 改名/改排课：仅本人（或 admin），改名重名校验
  · 删班：仅本人/admin → 进回收站表、成员关系保留（恢复即可复原）
  · 加学生：挂接已有账号 / 现场新建账号、重复入班 409、只能加学生角色
  · 移出班级：删成员关系但**不删账号**
  · 学生列表：仅 teacher/admin，支持 ?q= 跨班搜索并带所属班级
"""
import json

from app.db import SessionLocal
from app.models import Account, ClassMember, ClassRoom, Trash


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _teacher(client, admin_token, username="cls_t_cls", password="abcd1234"):
    resp = client.post(
        "/api/accounts",
        json={"username": username, "password": password, "role": "teacher"},
        headers=_auth(admin_token),
    )
    assert resp.status_code == 200 and resp.json()["code"] == 0, resp.text
    token = client.post("/api/auth/login", json={"username": username, "password": password})
    return token.json()["data"]["token"]


def _student_token(client, admin_token, username="cls_stu_cls"):
    client.post("/api/accounts",
                json={"username": username, "password": "abcd1234", "role": "student"},
                headers=_auth(admin_token))
    token = client.post("/api/auth/login", json={"username": username, "password": "abcd1234"})
    return token.json()["data"]["token"]


def _create_class(client, token, name="三年一班", schedule=None):
    body = {"name": name}
    if schedule is not None:
        body["schedule"] = schedule
    return client.post("/api/classes", json=body, headers=_auth(token))


# ---------------- 列表 ----------------

def test_list_requires_login(client):
    resp = client.get("/api/classes")
    assert resp.status_code == 401 and resp.json()["code"] == 2002


def test_list_visibility_scoped(client, login):
    admin = login()
    t_a = _teacher(client, admin["token"], "cls_t_vis_a")
    t_b = _teacher(client, admin["token"], "cls_t_vis_b")
    _create_class(client, t_a, "A班")
    _create_class(client, t_b, "B班")

    a_names = {c["name"] for c in client.get("/api/classes", headers=_auth(t_a)).json()["data"]["items"]}
    b_names = {c["name"] for c in client.get("/api/classes", headers=_auth(t_b)).json()["data"]["items"]}
    admin_names = {c["name"] for c in client.get("/api/classes", headers=_auth(admin["token"])).json()["data"]["items"]}

    assert a_names == {"A班"} and b_names == {"B班"}
    assert {"A班", "B班"} <= admin_names      # admin 看全部


def test_list_has_student_count_and_owner(client, login):
    admin = login()
    t = _teacher(client, admin["token"], "cls_t_cnt")
    created = _create_class(client, t, "计数班").json()["data"]
    client.post(f"/api/classes/{created['id']}/students",
                json={"username": "cls_stu_cnt1", "password": "abcd1234"},
                headers=_auth(t))

    items = client.get("/api/classes", headers=_auth(t)).json()["data"]["items"]
    row = next(c for c in items if c["id"] == created["id"])
    assert row["student_count"] == 1
    assert row["owner_name"]  # 有归属老师显示名


def test_student_sees_member_classes_only(client, login):
    admin = login()
    t = _teacher(client, admin["token"], "cls_t_who")
    in_cls = _create_class(client, t, "在的班").json()["data"]
    _create_class(client, t, "没我的班")
    stu_token = _student_token(client, admin["token"], "cls_stu_who")
    client.post(f"/api/classes/{in_cls['id']}/students",
                json={"account_id": client.get("/api/auth/me", headers=_auth(stu_token)).json()["data"]["id"]},
                headers=_auth(t))

    names = {c["name"] for c in client.get("/api/classes", headers=_auth(stu_token)).json()["data"]["items"]}
    assert names == {"在的班"}


# ---------------- 建班 ----------------

def test_create_requires_teacher_or_admin(client, login):
    admin = login()
    stu_token = _student_token(client, admin["token"], "cls_stu_mk")
    resp = _create_class(client, stu_token, "学生班")
    assert resp.status_code == 403 and resp.json()["code"] == 2005


def test_admin_can_create(client, login):
    resp = _create_class(client, login()["token"], "管理员班")
    assert resp.status_code == 200 and resp.json()["code"] == 0


def test_create_lands_in_db(client, login):
    admin = login()
    t = _teacher(client, admin["token"], "cls_t_land")
    schedule = {"mon": "18:00"}
    resp = _create_class(client, t, "落库班", schedule=schedule)
    assert resp.json()["code"] == 0
    class_id = resp.json()["data"]["id"]

    db = SessionLocal()
    try:
        row = db.get(ClassRoom, class_id)
        assert row is not None and row.name == "落库班"
        assert row.schedule == schedule
        me = client.get("/api/auth/me", headers=_auth(t)).json()["data"]
        assert row.owner_id == me["id"]
    finally:
        db.close()


def test_duplicate_name_same_owner_409(client, login):
    admin = login()
    t = _teacher(client, admin["token"], "cls_t_dup")
    assert _create_class(client, t, "重名班").json()["code"] == 0
    again = _create_class(client, t, "重名班")
    assert again.status_code == 409 and again.json()["code"] == 4001


def test_same_name_different_owners_ok(client, login):
    admin = login()
    t1 = _teacher(client, admin["token"], "cls_t_s1")
    t2 = _teacher(client, admin["token"], "cls_t_s2")
    assert _create_class(client, t1, "同名班").json()["code"] == 0
    assert _create_class(client, t2, "同名班").json()["code"] == 0   # 重名按老师隔离


def test_create_empty_name_400(client, login):
    resp = _create_class(client, login()["token"], "  ")
    assert resp.status_code == 400 and resp.json()["code"] == 1002


# ---------------- 详情 ----------------

def test_detail_requires_login(client, login):
    admin = login()
    cls = _create_class(client, admin["token"], "详情班").json()["data"]
    resp = client.get(f"/api/classes/{cls['id']}")
    assert resp.status_code == 401 and resp.json()["code"] == 2002


def test_detail_shows_students_for_member(client, login):
    admin = login()
    t = _teacher(client, admin["token"], "cls_t_det")
    cls = _create_class(client, t, "成员班").json()["data"]
    stu_id = client.post("/api/accounts",
                         json={"username": "cls_stu_det", "password": "abcd1234", "role": "student"},
                         headers=_auth(admin["token"])).json()["data"]["id"]
    client.post(f"/api/classes/{cls['id']}/students", json={"account_id": stu_id}, headers=_auth(t))

    detail = client.get(f"/api/classes/{cls['id']}", headers=_auth(t)).json()["data"]
    assert detail["name"] == "成员班"
    assert [s["username"] for s in detail["students"]] == ["cls_stu_det"]


def test_detail_outsider_403(client, login):
    admin = login()
    t1 = _teacher(client, admin["token"], "cls_t_o1")
    t2 = _teacher(client, admin["token"], "cls_t_o2")
    cls = _create_class(client, t1, "别人的班").json()["data"]
    resp = client.get(f"/api/classes/{cls['id']}", headers=_auth(t2))
    assert resp.status_code == 403 and resp.json()["code"] == 2005


def test_detail_missing_404(client, login):
    resp = client.get("/api/classes/zzzzzzzzzzzz", headers=_auth(login()["token"]))
    assert resp.status_code == 404 and resp.json()["code"] == 4004


# ---------------- 改名 / 排课 ----------------

def test_patch_rename_by_owner(client, login):
    admin = login()
    t = _teacher(client, admin["token"], "cls_t_ren")
    cls = _create_class(client, t, "旧名字").json()["data"]
    resp = client.patch(f"/api/classes/{cls['id']}", json={"name": "新名字"}, headers=_auth(t))
    assert resp.json()["code"] == 0 and resp.json()["data"]["name"] == "新名字"

    db = SessionLocal()
    try:
        assert db.get(ClassRoom, cls["id"]).name == "新名字"
    finally:
        db.close()


def test_patch_rename_duplicate_409(client, login):
    admin = login()
    t = _teacher(client, admin["token"], "cls_t_rd")
    _create_class(client, t, "班甲")
    cls_b = _create_class(client, t, "班乙").json()["data"]
    resp = client.patch(f"/api/classes/{cls_b['id']}", json={"name": "班甲"}, headers=_auth(t))
    assert resp.status_code == 409 and resp.json()["code"] == 4001


def test_patch_by_non_owner_403(client, login):
    admin = login()
    t1 = _teacher(client, admin["token"], "cls_t_p1")
    t2 = _teacher(client, admin["token"], "cls_t_p2")
    cls = _create_class(client, t1, "受保护班").json()["data"]
    resp = client.patch(f"/api/classes/{cls['id']}", json={"name": "被改了"}, headers=_auth(t2))
    assert resp.status_code == 403 and resp.json()["code"] == 2005


def test_patch_schedule_stored(client, login):
    admin = login()
    t = _teacher(client, admin["token"], "cls_t_sch")
    cls = _create_class(client, t, "排课班").json()["data"]
    schedule = {"wed": "19:00", "sat": "10:00"}
    resp = client.patch(f"/api/classes/{cls['id']}", json={"schedule": schedule}, headers=_auth(t))
    assert resp.json()["code"] == 0

    db = SessionLocal()
    try:
        assert db.get(ClassRoom, cls["id"]).schedule == schedule
    finally:
        db.close()


# ---------------- 删班 ----------------

def test_delete_by_owner_goes_to_trash(client, login):
    admin = login()
    t = _teacher(client, admin["token"], "cls_t_del")
    cls = _create_class(client, t, "待删班").json()["data"]
    stu_id = client.post("/api/accounts",
                         json={"username": "cls_stu_delcls", "password": "abcd1234", "role": "student"},
                         headers=_auth(admin["token"])).json()["data"]["id"]
    client.post(f"/api/classes/{cls['id']}/students", json={"account_id": stu_id}, headers=_auth(t))

    resp = client.delete(f"/api/classes/{cls['id']}", headers=_auth(t))
    assert resp.status_code == 200 and resp.json()["code"] == 0

    db = SessionLocal()
    try:
        assert db.get(ClassRoom, cls["id"]) is None                    # 班级行已删
        trash = db.query(Trash).filter(Trash.type == "class", Trash.owner_id == cls["id"]).first()
        assert trash is not None and trash.payload["name"] == "待删班"   # 快照可恢复
        assert db.query(ClassMember).filter(ClassMember.class_id == cls["id"]).count() == 1  # 成员关系保留
    finally:
        db.close()


def test_delete_permissions(client, login):
    admin = login()
    t1 = _teacher(client, admin["token"], "cls_t_d1")
    t2 = _teacher(client, admin["token"], "cls_t_d2")
    stu_token = _student_token(client, admin["token"], "cls_stu_del")
    cls = _create_class(client, t1, "权限班").json()["data"]

    other = client.delete(f"/api/classes/{cls['id']}", headers=_auth(t2))
    assert other.status_code == 403 and other.json()["code"] == 2005

    by_student = client.delete(f"/api/classes/{cls['id']}", headers=_auth(stu_token))
    assert by_student.status_code == 403 and by_student.json()["code"] == 2005

    by_admin = client.delete(f"/api/classes/{cls['id']}", headers=_auth(admin["token"]))  # admin 有权删任何班
    assert by_admin.status_code == 200 and by_admin.json()["code"] == 0


# ---------------- 加学生 ----------------

def test_add_existing_student(client, login):
    admin = login()
    t = _teacher(client, admin["token"], "cls_t_add")
    cls = _create_class(client, t, "挂接班").json()["data"]
    stu_id = client.post("/api/accounts",
                         json={"username": "cls_stu_exist", "password": "abcd1234", "role": "student"},
                         headers=_auth(admin["token"])).json()["data"]["id"]

    resp = client.post(f"/api/classes/{cls['id']}/students", json={"account_id": stu_id}, headers=_auth(t))
    assert resp.status_code == 200 and resp.json()["code"] == 0, resp.text

    db = SessionLocal()
    try:
        row = db.query(ClassMember).filter(ClassMember.class_id == cls["id"],
                                            ClassMember.account_id == stu_id).first()
        assert row is not None
        assert db.get(Account, stu_id).status == "active"
    finally:
        db.close()


def test_add_creates_new_student_account(client, login):
    admin = login()
    t = _teacher(client, admin["token"], "cls_t_new")
    cls = _create_class(client, t, "新建班").json()["data"]

    resp = client.post(f"/api/classes/{cls['id']}/students",
                       json={"username": "cls_stu_brandnew", "password": "abcd1234"},
                       headers=_auth(t))
    assert resp.json()["code"] == 0, resp.text

    db = SessionLocal()
    try:
        acc = db.query(Account).filter(Account.username == "cls_stu_brandnew").first()
        assert acc is not None and acc.role == "student" and acc.status == "active"
        assert db.query(ClassMember).filter(ClassMember.class_id == cls["id"],
                                            ClassMember.account_id == acc.id).count() == 1
    finally:
        db.close()

    # 新账号能登录
    assert client.post("/api/auth/login",
                       json={"username": "cls_stu_brandnew", "password": "abcd1234"}).status_code == 200


def test_add_duplicate_409(client, login):
    admin = login()
    t = _teacher(client, admin["token"], "cls_t_dd")
    cls = _create_class(client, t, "重复班").json()["data"]
    stu_id = client.post("/api/accounts",
                         json={"username": "cls_stu_dd", "password": "abcd1234", "role": "student"},
                         headers=_auth(admin["token"])).json()["data"]["id"]

    assert client.post(f"/api/classes/{cls['id']}/students",
                       json={"account_id": stu_id}, headers=_auth(t)).json()["code"] == 0
    again = client.post(f"/api/classes/{cls['id']}/students",
                        json={"account_id": stu_id}, headers=_auth(t))
    assert again.status_code == 409 and again.json()["code"] == 4009


def test_add_teacher_account_rejected(client, login):
    admin = login()
    t = _teacher(client, admin["token"], "cls_t_role")
    cls = _create_class(client, t, "角色班").json()["data"]
    t2_id = client.post("/api/accounts",
                        json={"username": "cls_t_victim", "password": "abcd1234", "role": "teacher"},
                        headers=_auth(admin["token"])).json()["data"]["id"]

    resp = client.post(f"/api/classes/{cls['id']}/students", json={"account_id": t2_id}, headers=_auth(t))
    assert resp.status_code == 400 and resp.json()["code"] == 1002


def test_add_by_non_owner_403(client, login):
    admin = login()
    t1 = _teacher(client, admin["token"], "cls_t_a1")
    t2 = _teacher(client, admin["token"], "cls_t_a2")
    cls = _create_class(client, t1, "私有班").json()["data"]
    resp = client.post(f"/api/classes/{cls['id']}/students",
                       json={"username": "anyone_new", "password": "abcd1234"}, headers=_auth(t2))
    assert resp.status_code == 403 and resp.json()["code"] == 2005


# ---------------- 移出 ----------------


def test_remove_student_keeps_account(client, login):
    admin = login()
    t = _teacher(client, admin["token"], "cls_t_rm")
    cls = _create_class(client, t, "移出班").json()["data"]
    stu_id = client.post("/api/accounts",
                         json={"username": "cls_stu_rm", "password": "abcd1234", "role": "student"},
                         headers=_auth(admin["token"])).json()["data"]["id"]
    client.post(f"/api/classes/{cls['id']}/students", json={"account_id": stu_id}, headers=_auth(t))

    resp = client.delete(f"/api/classes/{cls['id']}/students/{stu_id}", headers=_auth(t))
    assert resp.status_code == 200 and resp.json()["code"] == 0

    db = SessionLocal()
    try:
        assert db.query(ClassMember).filter(ClassMember.class_id == cls["id"],
                                            ClassMember.account_id == stu_id).count() == 0
        assert db.get(Account, stu_id) is not None        # 账号还在（≠删号）
        assert db.get(Account, stu_id).status == "active"
    finally:
        db.close()


def test_remove_non_member_404(client, login):
    admin = login()
    t = _teacher(client, admin["token"], "cls_t_nm")
    cls = _create_class(client, t, "空班").json()["data"]
    stu_id = client.post("/api/accounts",
                         json={"username": "cls_stu_nm", "password": "abcd1234", "role": "student"},
                         headers=_auth(admin["token"])).json()["data"]["id"]

    resp = client.delete(f"/api/classes/{cls['id']}/students/{stu_id}", headers=_auth(t))
    assert resp.status_code == 404 and resp.json()["code"] == 1004


# ---------------- 学生列表 ----------------

def test_students_list_requires_teacher(client, login):
    admin = login()
    stu_token = _student_token(client, admin["token"], "cls_stu_nl")
    resp = client.get("/api/students", headers=_auth(stu_token))
    assert resp.status_code == 403 and resp.json()["code"] == 2005

    resp2 = client.get("/api/students", headers=_auth(admin["token"]))
    assert resp2.status_code == 200 and resp2.json()["code"] == 0


def test_students_list_with_classes(client, login):
    admin = login()
    t = _teacher(client, admin["token"], "cls_t_sl")
    cls = _create_class(client, t, "归属班").json()["data"]
    stu_id = client.post("/api/accounts",
                         json={"username": "cls_stu_belongs", "password": "abcd1234", "role": "student"},
                         headers=_auth(admin["token"])).json()["data"]["id"]
    client.post(f"/api/classes/{cls['id']}/students", json={"account_id": stu_id}, headers=_auth(t))

    items = client.get("/api/students", headers=_auth(t)).json()["data"]["items"]
    row = next(s for s in items if s["username"] == "cls_stu_belongs")
    assert [c["name"] for c in row["classes"]] == ["归属班"]


def test_students_search_q(client, login):
    admin = login()
    t = _teacher(client, admin["token"], "cls_t_q")
    _teacher(client, admin["token"], "cls_t_q2")
    for name in ("张三丰", "李四"):
        client.post("/api/accounts",
                    json={"username": name, "password": "abcd1234", "role": "student"},
                    headers=_auth(admin["token"]))

    hit = client.get("/api/students", params={"q": "张三"}, headers=_auth(t)).json()["data"]["items"]
    assert [s["username"] for s in hit] == ["张三丰"]
    assert all(s["role"] == "student" for s in hit)
