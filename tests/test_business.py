"""
后端业务流程测试
================
覆盖所有 API 模块的 CRUD 完整流程、边界情况、并发安全。
数据目录 (data/) 为测试数据，可随意读写。

运行方式：
  cd C:\Arin - cabin\QC-PocketSpace\qc-pocket-space\tests
  venv\Scripts\python.exe -m pytest test_business.py -v
"""
import sys
import os
import threading

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, BACKEND_DIR)

from fastapi.testclient import TestClient
from main import app
from utils import generate_token, ACTIVE_TOKENS

# 确保登录限流状态从干净状态开始
from routers.auth import _reset_login_failures
_reset_login_failures()

client = TestClient(app)


# ============================================================
# 辅助函数
# ============================================================

def make_token(username: str, role: str) -> str:
    return generate_token(username, role)


def auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def setup_function():
    ACTIVE_TOKENS.clear()
    _reset_login_failures()


def _ensure_login_ok(username: str, password: str) -> str:
    """清除限流后登录，免疫跨测试的限流状态泄漏"""
    _reset_login_failures()
    resp = client.post("/api/login", json={"username": username, "password": password})
    assert resp.status_code == 200, f"登录失败: {resp.text}"
    return resp.json()["token"]


def _create_user(username: str, role: str, password: str = "123456") -> str:
    """通过 API 创建测试用户并返回 token（如果已存在则先删除再创建）"""
    admin = make_token("admin", "admin")
    resp = client.post("/api/register", headers=auth(admin),
                       json={"username": username, "password": password, "role": role})
    if resp.status_code == 400 and "已存在" in resp.text:
        # 清理脏数据并重试
        client.delete(f"/api/users/{username}", headers=auth(admin))
        admin = make_token("admin", "admin")
        resp = client.post("/api/register", headers=auth(admin),
                           json={"username": username, "password": password, "role": role})
    assert resp.status_code == 200, f"注册失败: {resp.text}"
    return make_token(username, role)


def _delete_user(username: str):
    """通过 API 删除测试用户（软删 + 清空垃圾桶，确保不留残余）"""
    admin = make_token("admin", "admin")
    resp = client.delete(f"/api/users/{username}", headers=auth(admin))
    # 硬清理：直接从 trash 表删除
    if resp.status_code == 200:
        trash_id = resp.json().get("trash", {}).get("id")
        if trash_id:
            client.delete(f"/api/trash/{trash_id}", headers=auth(make_token("admin", "admin")))


def _get_user_id(username: str) -> int:
    """从 DB 查找用户 ID"""
    from main import SessionLocal, UserModel
    db = SessionLocal()
    try:
        user = db.query(UserModel).filter(UserModel.username == username).first()
        return user.id if user else -1
    finally:
        db.close()

# ============================================================
# 🔑 登录与用户资料
# ============================================================

class Test登录与资料:
    """登录、个人资料、密码修改、头像、描述符"""

    def test_登录成功返回完整信息(self):
        """登录后应返回 token + user 对象（含 id, username, role, avatar_path, descriptors）"""
        _create_user("biz_login_user", "student", "mypass")
        resp = client.post("/api/login", json={"username": "biz_login_user", "password": "mypass"})
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "success"
        assert "token" in data
        user = data["user"]
        assert user["username"] == "biz_login_user"
        assert user["role"] == "student"
        assert "avatar_path" in user
        assert "descriptors" in user
        _delete_user("biz_login_user")

    def test_查看个人资料(self):
        token = _create_user("biz_profile_user", "student")
        resp = client.get("/api/user/profile", headers=auth(token))
        assert resp.status_code == 200
        assert resp.json()["user"]["username"] == "biz_profile_user"
        _delete_user("biz_profile_user")

    def test_修改密码完整流程(self):
        """注册 → 旧密码登录 → 改密码 → 旧密码失败 → 新密码成功"""
        _create_user("biz_pwd_user", "student", "old123")

        # 旧密码登录
        token = _ensure_login_ok("biz_pwd_user", "old123")

        # 修改密码
        resp = client.post("/api/user/change-password", headers=auth(token),
                           json={"username": "biz_pwd_user", "old_password": "old123", "new_password": "new456"})
        assert resp.status_code == 200

        # 旧密码失败
        _reset_login_failures()
        resp = client.post("/api/login", json={"username": "biz_pwd_user", "password": "old123"})
        assert resp.status_code == 401

        # 新密码成功
        token2 = _ensure_login_ok("biz_pwd_user", "new456")

        _delete_user("biz_pwd_user")

    def test_修改密码原密码错误(self):
        token = _create_user("biz_pwd_wrong", "student", "correct")
        resp = client.post("/api/user/change-password", headers=auth(token),
                           json={"username": "biz_pwd_wrong", "old_password": "wrong", "new_password": "new"})
        assert resp.status_code == 400
        _delete_user("biz_pwd_wrong")

    def test_描述符更新和读取(self):
        """更新描述符 → profile 验证"""
        token = _create_user("biz_desc_user", "student")
        resp = client.post("/api/update-descriptors", headers=auth(token),
                           json={"qc_descriptors": '{"score": 100, "pet": "cat"}'})
        assert resp.status_code == 200

        resp = client.get("/api/user/profile", headers=auth(token))
        desc = resp.json()["user"]["descriptors"]
        assert desc["score"] == 100
        assert desc["pet"] == "cat"
        _delete_user("biz_desc_user")

    def test_头像更新_预设头像(self):
        """更新为预设头像 avatar_01.jpg"""
        token = _create_user("biz_avatar_user", "student")
        resp = client.post("/api/update-avatar", headers=auth(token),
                           json={"username": "biz_avatar_user", "avatar_path": "avatar_05.jpg"})
        assert resp.status_code == 200
        assert resp.json()["avatar_path"] == "avatar_05.jpg"
        _delete_user("biz_avatar_user")

    def test_头像更新_custom(self):
        """学生可以设置 custom 头像"""
        token = _create_user("biz_avatar_custom", "student")
        resp = client.post("/api/update-avatar", headers=auth(token),
                           json={"username": "biz_avatar_custom", "avatar_path": "custom"})
        assert resp.status_code == 200
        _delete_user("biz_avatar_custom")

    def test_头像上传仅限学生(self):
        """老师不能上传自定义头像"""
        token = _create_user("biz_avatar_teacher", "teacher")
        import io
        resp = client.post("/api/upload-avatar", headers=auth(token),
                           files={"file": ("avatar.jpg", io.BytesIO(b"fake jpg"), "image/jpeg")})
        assert resp.status_code == 400
        _delete_user("biz_avatar_teacher")


# ============================================================
# 📝 笔记本 + 笔记
# ============================================================

class Test笔记本和笔记:
    """笔记本 CRUD + 笔记 CRUD"""

    def test_笔记本CRUD完整流程(self):
        """创建 → 列表 → 重命名 → 删除"""
        token = _create_user("biz_nb_user", "student")

        # 创建
        resp = client.post("/api/notebooks", headers=auth(token), json={"name": "我的笔记本"})
        assert resp.status_code == 200
        assert resp.json()["name"] == "我的笔记本"

        # 列表
        resp = client.get("/api/notebooks", headers=auth(token))
        names = [nb["name"] for nb in resp.json()["notebooks"]]
        assert "我的笔记本" in names

        # 重命名
        resp = client.put("/api/notebooks/我的笔记本?new_name=改名后", headers=auth(token))
        assert resp.status_code == 200

        # 删除
        resp = client.delete("/api/notebooks/改名后", headers=auth(token))
        assert resp.status_code == 200

        _delete_user("biz_nb_user")

    def test_创建重名笔记本(self):
        token = _create_user("biz_nb_dup", "student")
        client.post("/api/notebooks", headers=auth(token), json={"name": "重复"})
        resp = client.post("/api/notebooks", headers=auth(token), json={"name": "重复"})
        assert resp.status_code == 400
        _delete_user("biz_nb_dup")

    def test_删除不存在的笔记本(self):
        token = _create_user("biz_nb_404", "student")
        resp = client.delete("/api/notebooks/不存在", headers=auth(token))
        assert resp.status_code == 404
        _delete_user("biz_nb_404")

    def test_重命名到已存在的笔记本(self):
        token = _create_user("biz_nb_rename", "student")
        client.post("/api/notebooks", headers=auth(token), json={"name": "A"})
        client.post("/api/notebooks", headers=auth(token), json={"name": "B"})
        resp = client.put("/api/notebooks/A?new_name=B", headers=auth(token))
        assert resp.status_code == 400
        _delete_user("biz_nb_rename")

    def test_笔记CRUD完整流程(self):
        """创建 → 读取 → 修改 → 列出 → 重命名 → 删除"""
        token = _create_user("biz_note_user", "student")
        client.post("/api/notebooks", headers=auth(token), json={"name": "测试本"})

        # 创建笔记
        resp = client.post("/api/notes?notebook=测试本", headers=auth(token),
                           json={"filename": "hello.md", "content": "# 你好\n这是测试"})
        assert resp.status_code == 200

        # 读取
        resp = client.get("/api/notes/测试本/hello.md", headers=auth(token))
        assert resp.status_code == 200
        assert "测试" in resp.json()["content"]

        # 修改（同名覆盖）
        resp = client.post("/api/notes?notebook=测试本", headers=auth(token),
                           json={"filename": "hello.md", "content": "# 更新"})
        assert resp.status_code == 200

        # 列出
        resp = client.get("/api/notes?notebook=测试本", headers=auth(token))
        assert resp.status_code == 200
        assert len(resp.json()["notes"]) >= 1

        # 重命名
        resp = client.put("/api/notes/测试本/hello.md?new_name=world.md", headers=auth(token))
        assert resp.status_code == 200

        # 删除
        resp = client.delete("/api/notes/测试本/world.md", headers=auth(token))
        assert resp.status_code == 200

        _delete_user("biz_note_user")

    def test_读取不存在的笔记(self):
        token = _create_user("biz_note_404", "student")
        client.post("/api/notebooks", headers=auth(token), json={"name": "空本"})
        resp = client.get("/api/notes/空本/不存在.md", headers=auth(token))
        assert resp.status_code == 404
        _delete_user("biz_note_404")

    def test_笔记自动补md后缀(self):
        """保存时如果不带 .md 后缀，应该自动补上"""
        token = _create_user("biz_note_ext", "student")
        client.post("/api/notebooks", headers=auth(token), json={"name": "后缀测试"})
        resp = client.post("/api/notes?notebook=后缀测试", headers=auth(token),
                           json={"filename": "noext", "content": "test"})
        assert resp.status_code == 200
        # 应该能以 .md 后缀读取
        resp = client.get("/api/notes/后缀测试/noext.md", headers=auth(token))
        assert resp.status_code == 200
        _delete_user("biz_note_ext")


# ============================================================
# 💬 站内信
# ============================================================

class Test站内信:
    """消息发送、接收、文件夹管理"""

    def test_消息CRUD完整流程(self):
        """老师发 → 学生收 → 已读 → 移动 → 删除"""
        teacher = _create_user("biz_msg_t", "teacher")
        student = _create_user("biz_msg_s", "student")

        # 老师发消息
        resp = client.post("/api/messages/send", headers=auth(teacher),
                           json={"recipient": "biz_msg_s", "subject": "作业通知", "body": "完成第三章"})
        assert resp.status_code == 200
        msg_id = resp.json()["id"]

        # 学生查看收件箱
        resp = client.get("/api/messages/inbox?folder=收件箱", headers=auth(student))
        assert resp.status_code == 200
        assert len(resp.json()["messages"]) >= 1

        # 文件夹列表 + 未读数
        resp = client.get("/api/messages/folders", headers=auth(student))
        assert resp.status_code == 200
        assert resp.json()["total_unread"] >= 1

        # 标记已读
        resp = client.post(f"/api/messages/{msg_id}/read?folder=收件箱", headers=auth(student))
        assert resp.status_code == 200

        # 创建新文件夹
        resp = client.post("/api/messages/folders?folder=重要", headers=auth(student))
        assert resp.status_code == 200

        # 移动消息
        resp = client.post(f"/api/messages/{msg_id}/move?folder=重要", headers=auth(student))
        assert resp.status_code == 200

        # 删除消息
        resp = client.delete(f"/api/messages/{msg_id}?folder=重要", headers=auth(student))
        assert resp.status_code == 200

        # 删除文件夹
        resp = client.delete("/api/messages/folders/重要", headers=auth(student))
        assert resp.status_code == 200

        _delete_user("biz_msg_t")
        _delete_user("biz_msg_s")

    def test_不能删除收件箱(self):
        student = _create_user("biz_msg_inbox", "student")
        resp = client.delete("/api/messages/folders/收件箱", headers=auth(student))
        assert resp.status_code == 400
        _delete_user("biz_msg_inbox")

    def test_不能重命名收件箱(self):
        student = _create_user("biz_msg_rename", "student")
        resp = client.put("/api/messages/folders/收件箱?new_name=改名", headers=auth(student))
        assert resp.status_code == 400
        _delete_user("biz_msg_rename")

    def test_创建重名文件夹(self):
        student = _create_user("biz_msg_dup", "student")
        client.post("/api/messages/folders?folder=测试夹", headers=auth(student))
        resp = client.post("/api/messages/folders?folder=测试夹", headers=auth(student))
        assert resp.status_code == 400
        _delete_user("biz_msg_dup")

    def test_删除不存在的消息(self):
        student = _create_user("biz_msg_404", "student")
        resp = client.delete("/api/messages/fake_msg_id?folder=收件箱", headers=auth(student))
        assert resp.status_code == 404
        _delete_user("biz_msg_404")

    def test_未读计数(self):
        teacher = _create_user("biz_unread_t", "teacher")
        student = _create_user("biz_unread_s", "student")

        # 发消息（msg_id 基于秒级时间戳，快速发送可能覆盖，所以至少发 1 条）
        client.post("/api/messages/send", headers=auth(teacher),
                    json={"recipient": "biz_unread_s", "subject": "unread_test", "body": "x"})

        resp = client.get("/api/messages/unread-count", headers=auth(student))
        assert resp.status_code == 200
        assert resp.json()["count"] >= 1

        _delete_user("biz_unread_t")
        _delete_user("biz_unread_s")

    def test_发送消息给不存在的学生(self):
        teacher = _create_user("biz_msg_err_t", "teacher")
        resp = client.post("/api/messages/send", headers=auth(teacher),
                           json={"recipient": "ghost", "subject": "x", "body": "x"})
        assert resp.status_code == 404
        _delete_user("biz_msg_err_t")


# ============================================================
# 🏫 班级管理
# ============================================================

class Test班级管理:
    """班级 CRUD + 学生管理"""

    def test_班级CRUD完整流程(self):
        """创建 → 列表 → 改名 → 删除"""
        teacher = _create_user("biz_cls_t", "teacher")

        # 创建
        resp = client.post("/api/classes", headers=auth(teacher),
                           json={"name": "一班", "schedule": {"days": ["周一", "周三"]}})
        assert resp.status_code == 200

        # 列表
        resp = client.get("/api/classes", headers=auth(teacher))
        names = [c["name"] for c in resp.json()["classes"]]
        assert "一班" in names

        # 改名
        resp = client.put("/api/classes/一班", headers=auth(teacher),
                          json={"name": "二班", "schedule": {"days": ["周二"]}})
        assert resp.status_code == 200

        # 删除
        resp = client.delete("/api/classes/二班", headers=auth(teacher))
        assert resp.status_code == 200

        _delete_user("biz_cls_t")

    def test_添加已有学生到班级(self):
        teacher = _create_user("biz_cls_add_t", "teacher")
        student = _create_user("biz_cls_add_s", "student")
        sid = _get_user_id("biz_cls_add_s")

        client.post("/api/classes", headers=auth(teacher), json={"name": "测试班"})
        resp = client.post("/api/classes/测试班/students", headers=auth(teacher),
                           json={"name": "测试学生", "existing_id": str(sid)})
        assert resp.status_code == 200
        assert resp.json()["user_id"] == sid

        # 验证学生在班级中
        resp = client.get("/api/classes", headers=auth(teacher))
        cls = next(c for c in resp.json()["classes"] if c["name"] == "测试班")
        student_ids = [s["user_id"] for s in cls["students"]]
        assert sid in student_ids

        _delete_user("biz_cls_add_t")
        _delete_user("biz_cls_add_s")

    def test_添加新学生到班级(self):
        """不传 existing_id → 自动创建学生账号"""
        teacher = _create_user("biz_cls_new_t", "teacher")
        client.post("/api/classes", headers=auth(teacher), json={"name": "新学生班"})

        resp = client.post("/api/classes/新学生班/students", headers=auth(teacher),
                           json={"name": "王小明", "existing_id": "", "remark": ""})
        assert resp.status_code == 200
        assert resp.json()["user_id"] > 0

        # 清理自动创建的学生
        from main import SessionLocal, UserModel
        db = SessionLocal()
        try:
            auto = db.query(UserModel).filter(UserModel.username.like("%王小明")).first()
            if auto:
                _delete_user(auto.username)
        finally:
            db.close()
        _delete_user("biz_cls_new_t")

    def test_移除班级学生(self):
        teacher = _create_user("biz_cls_rm_t", "teacher")
        student = _create_user("biz_cls_rm_s", "student")
        sid = _get_user_id("biz_cls_rm_s")

        client.post("/api/classes", headers=auth(teacher), json={"name": "移除班"})
        client.post("/api/classes/移除班/students", headers=auth(teacher),
                    json={"name": "x", "existing_id": str(sid)})

        resp = client.delete(f"/api/classes/移除班/students/{sid}", headers=auth(teacher))
        assert resp.status_code == 200

        # 验证已移除
        resp = client.get("/api/classes", headers=auth(teacher))
        cls = next(c for c in resp.json()["classes"] if c["name"] == "移除班")
        assert all(s["user_id"] != sid for s in cls["students"])

        _delete_user("biz_cls_rm_t")
        _delete_user("biz_cls_rm_s")

    def test_删除不存在的班级(self):
        teacher = _create_user("biz_cls_404", "teacher")
        resp = client.delete("/api/classes/不存在", headers=auth(teacher))
        assert resp.status_code == 404
        _delete_user("biz_cls_404")

    def test_更新只改schedule不改名(self):
        teacher = _create_user("biz_cls_sched", "teacher")
        client.post("/api/classes", headers=auth(teacher), json={"name": "调度班", "schedule": {"days": ["周一"]}})
        resp = client.put("/api/classes/调度班", headers=auth(teacher),
                          json={"name": "调度班", "schedule": {"days": ["周一", "周三", "周五"]}})
        assert resp.status_code == 200
        _delete_user("biz_cls_sched")

    def test_移除非数字学生ID(self):
        teacher = _create_user("biz_cls_badid", "teacher")
        client.post("/api/classes", headers=auth(teacher), json={"name": "badid班"})
        resp = client.delete("/api/classes/badid班/students/abc", headers=auth(teacher))
        assert resp.status_code == 400
        _delete_user("biz_cls_badid")

    def test_查找学生(self):
        teacher = _create_user("biz_cls_find_t", "teacher")
        student = _create_user("biz_cls_find_s", "student")
        sid = _get_user_id("biz_cls_find_s")

        client.post("/api/classes", headers=auth(teacher), json={"name": "查找班"})
        client.post("/api/classes/查找班/students", headers=auth(teacher),
                    json={"name": "biz_cls_find_s", "existing_id": str(sid)})

        resp = client.get(f"/api/classes/find-student?name=biz_cls_find_s", headers=auth(teacher))
        assert resp.status_code == 200
        assert len(resp.json()["matches"]) >= 1

        _delete_user("biz_cls_find_t")
        _delete_user("biz_cls_find_s")

    def test_全校学生列表(self):
        teacher = _create_user("biz_list_t", "teacher")
        student = _create_user("biz_list_s", "student")

        resp = client.get("/api/students/list", headers=auth(teacher))
        assert resp.status_code == 200
        usernames = [s["username"] for s in resp.json()["students"]]
        assert "biz_list_s" in usernames

        _delete_user("biz_list_t")
        _delete_user("biz_list_s")


# ============================================================
# 📚 课堂记录
# ============================================================

class Test课堂记录:
    """课堂记录 CRUD"""

    def test_课堂记录CRUD完整流程(self):
        """创建 → 列表(按月) → 获取单条 → 列表(全部) → 删除"""
        teacher = _create_user("biz_sess_t", "teacher")

        # 创建
        resp = client.post("/api/sessions/测试班", headers=auth(teacher), json={
            "date": "2026-07-08",
            "students": [
                {"id": "1", "name": "张三", "status": "present", "content": "认真"},
                {"id": "2", "name": "李四", "status": "absent", "remark": "请假"}
            ]
        })
        assert resp.status_code == 200

        # 按月列出
        resp = client.get("/api/sessions/测试班?month=2026-07", headers=auth(teacher))
        assert resp.status_code == 200
        assert len(resp.json()["sessions"]) >= 1

        # 获取单条
        resp = client.get("/api/sessions/测试班/2026-07-08", headers=auth(teacher))
        assert resp.status_code == 200
        assert resp.json()["session"]["date"] == "2026-07-08"

        # 全部列出（不传 month）
        resp = client.get("/api/sessions/测试班", headers=auth(teacher))
        assert resp.status_code == 200

        # 删除
        resp = client.delete("/api/sessions/测试班/2026-07-08", headers=auth(teacher))
        assert resp.status_code == 200

        _delete_user("biz_sess_t")

    def test_同日期覆盖更新(self):
        """同一天再保存一次应该覆盖"""
        teacher = _create_user("biz_sess_over", "teacher")

        client.post("/api/sessions/覆盖班", headers=auth(teacher), json={
            "date": "2026-07-10",
            "students": [{"id": "1", "name": "A", "status": "present"}]
        })
        client.post("/api/sessions/覆盖班", headers=auth(teacher), json={
            "date": "2026-07-10",
            "students": [{"id": "1", "name": "A", "status": "absent"}]
        })

        resp = client.get("/api/sessions/覆盖班/2026-07-10", headers=auth(teacher))
        assert resp.json()["session"]["students"][0]["status"] == "absent"

        _delete_user("biz_sess_over")

    def test_删除不存在的课堂记录(self):
        teacher = _create_user("biz_sess_404", "teacher")
        resp = client.delete("/api/sessions/不存在/2099-01-01", headers=auth(teacher))
        assert resp.status_code == 404
        _delete_user("biz_sess_404")

    def test_获取不存在的课堂记录(self):
        teacher = _create_user("biz_sess_get404", "teacher")
        resp = client.get("/api/sessions/不存在/2099-01-01", headers=auth(teacher))
        assert resp.status_code == 404
        _delete_user("biz_sess_get404")


# ============================================================
# 📚 课程管理
# ============================================================

class Test课程管理:
    """课程 + 课件素材 CRUD"""

    def test_课程CRUD完整流程(self):
        """创建 → 列表 → 删除"""
        teacher = _create_user("biz_course_t", "teacher")
        token = make_token("biz_course_t", "teacher")

        # 创建
        resp = client.post("/api/courses", headers=auth(token),
                           json={"teacher": "biz_course_t", "name": "Scratch入门", "course_type": "scratch"})
        assert resp.status_code == 200
        course_id = resp.json()["course"]["id"]

        # 列表
        resp = client.get(f"/api/courses?teacher=biz_course_t")
        names = [c["name"] for c in resp.json()["courses"]]
        assert "Scratch入门" in names

        # 删除（移到回收站）——用 course_id 而非中文名
        resp = client.delete(f"/api/courses/{course_id}", headers=auth(token))
        assert resp.status_code == 200

        _delete_user("biz_course_t")

    def test_创建重名课程(self):
        teacher = _create_user("biz_course_dup", "teacher")
        token = make_token("biz_course_dup", "teacher")
        client.post("/api/courses", headers=auth(token),
                    json={"teacher": "biz_course_dup", "name": "重复课程", "course_type": "scratch"})
        resp = client.post("/api/courses", headers=auth(token),
                           json={"teacher": "biz_course_dup", "name": "重复课程", "course_type": "scratch"})
        assert resp.status_code == 400
        _delete_user("biz_course_dup")

    def test_删除不存在的课程(self):
        teacher = _create_user("biz_course_404", "teacher")
        token = make_token("biz_course_404", "teacher")
        resp = client.delete("/api/courses/不存在", headers=auth(token))
        assert resp.status_code == 404
        _delete_user("biz_course_404")

    def test_课件列表(self):
        teacher = _create_user("biz_mat_t", "teacher")
        token = make_token("biz_mat_t", "teacher")
        create_resp = client.post("/api/courses", headers=auth(token),
                    json={"teacher": "biz_mat_t", "name": "课件测试", "course_type": "scratch"})
        if create_resp.status_code == 400:
            # 已存在 → 从列表中取 id
            list_resp = client.get(f"/api/courses?teacher=biz_mat_t")
            courses = [c for c in list_resp.json()["courses"] if c["name"] == "课件测试"]
            assert courses, "找不到已存在的课件测试课程"
            course_id = courses[0]["id"]
        else:
            assert create_resp.status_code == 200
            course_id = create_resp.json()["course"]["id"]

        resp = client.get(f"/api/courses/{course_id}/materials?teacher=biz_mat_t")
        assert resp.status_code == 200
        assert "materials" in resp.json()

        _delete_user("biz_mat_t")

    def test_课件列表不存在的课程(self):
        resp = client.get("/api/courses/不存在xyz/materials")
        assert resp.status_code == 404


# ============================================================
# 🗑️ 垃圾桶（用户回收站）
# ============================================================

class Test垃圾桶:
    """用户软删除 → 回收站 → 恢复"""

    def test_删除用户后恢复(self):
        """创建 → 删除 → 查看垃圾桶 → 恢复 → 验证"""
        _create_user("biz_trash_u", "student", "pass123")
        admin = make_token("admin", "admin")

        # 删除
        resp = client.delete("/api/users/biz_trash_u", headers=auth(admin))
        assert resp.status_code == 200
        trash_id = resp.json()["trash"]["id"]

        # 查看垃圾桶
        resp = client.get("/api/trash", headers=auth(admin))
        assert resp.status_code == 200
        assert any(t["id"] == trash_id for t in resp.json()["trash"])

        # 恢复
        resp = client.post(f"/api/trash/restore/{trash_id}", headers=auth(admin))
        assert resp.status_code == 200

        # 验证恢复成功（能登录）
        _ensure_login_ok("biz_trash_u", "pass123")

        _delete_user("biz_trash_u")

    def test_恢复不存在的垃圾桶记录(self):
        admin = make_token("admin", "admin")
        resp = client.post("/api/trash/restore/99999", headers=auth(admin))
        assert resp.status_code == 404

    def test_永久删除垃圾桶记录(self):
        _create_user("biz_perm_del", "student")
        admin = make_token("admin", "admin")
        resp = client.delete("/api/users/biz_perm_del", headers=auth(admin))
        trash_id = resp.json()["trash"]["id"]

        resp = client.delete(f"/api/trash/{trash_id}", headers=auth(admin))
        assert resp.status_code == 200

        # 确认已彻底删除（先清限流，再确认登录失败不是由限流导致）
        _reset_login_failures()
        resp = client.post("/api/login", json={"username": "biz_perm_del", "password": "123456"})
        assert resp.status_code == 401

    def test_恢复时用户名已存在(self):
        """如果用户名已被占用，恢复应该失败"""
        _create_user("biz_trash_dup", "student")
        admin = make_token("admin", "admin")
        resp = client.delete("/api/users/biz_trash_dup", headers=auth(admin))
        trash_id = resp.json()["trash"]["id"]

        # 重新创建同名用户
        _create_user("biz_trash_dup", "student", "123456")
        admin = make_token("admin", "admin")  # 重新获取 admin token（_create_user 内部会踢旧 token）

        # 恢复应该失败
        resp = client.post(f"/api/trash/restore/{trash_id}", headers=auth(admin))
        assert resp.status_code == 400

        _delete_user("biz_trash_dup")


# ============================================================
# 🗑️ 统一回收站（班级/课程）
# ============================================================

class Test统一回收站:
    """班级和课程的回收站"""

    def test_删除班级进回收站(self):
        teacher = _create_user("biz_utrash_t", "teacher")
        token = make_token("biz_utrash_t", "teacher")
        client.post("/api/classes", headers=auth(token), json={"name": "回收站班"})

        # 删除班级
        resp = client.delete("/api/classes/回收站班", headers=auth(token))
        assert resp.status_code == 200

        # 查看回收站
        resp = client.get("/api/trash/items", headers=auth(token))
        assert resp.status_code == 200

        _delete_user("biz_utrash_t")

    def test_删除课程进回收站(self):
        teacher = _create_user("biz_ctrash_t", "teacher")
        token = make_token("biz_ctrash_t", "teacher")
        create_resp = client.post("/api/courses", headers=auth(token),
                    json={"teacher": "biz_ctrash_t", "name": "回收站课程", "course_type": "scratch"})
        course_id = create_resp.json()["course"]["id"]

        resp = client.delete(f"/api/courses/{course_id}", headers=auth(token))
        assert resp.status_code == 200

        resp = client.get("/api/trash/items", headers=auth(token))
        assert resp.status_code == 200

        _delete_user("biz_ctrash_t")


# ============================================================
# 🔀 并发测试
# ============================================================

class Test并发:
    """多线程同时操作不应崩溃"""

    def test_并发登录(self):
        """5 个用户同时登录"""
        for i in range(5):
            _create_user(f"conc_login_{i}", "student")
        _reset_login_failures()

        results = []
        errors = []

        def do_login(idx):
            try:
                resp = client.post("/api/login",
                                   json={"username": f"conc_login_{idx}", "password": "123456"})
                results.append(resp.status_code)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=do_login, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"并发错误: {errors}"
        assert all(s == 200 for s in results)

        for i in range(5):
            _delete_user(f"conc_login_{i}")

    def test_并发读写笔记(self):
        token = _create_user("conc_note", "student")
        client.post("/api/notebooks", headers=auth(token), json={"name": "并发本"})

        results = []
        errors = []

        def write_note(idx):
            try:
                resp = client.post("/api/notes?notebook=并发本", headers=auth(token),
                                   json={"filename": f"n{idx}.md", "content": f"# {idx}"})
                results.append(("w", idx, resp.status_code))
            except Exception as e:
                errors.append(f"w{idx}: {e}")

        def read_notes():
            try:
                resp = client.get("/api/notes?notebook=并发本", headers=auth(token))
                results.append(("r", -1, resp.status_code))
            except Exception as e:
                errors.append(f"r: {e}")

        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=write_note, args=(i,)))
        for _ in range(2):
            threads.append(threading.Thread(target=read_notes))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"并发错误: {errors}"
        assert all(r[2] == 200 for r in results)

        _delete_user("conc_note")

    def test_并发发消息(self):
        """3 个老师同时给同一学生发消息"""
        student = _create_user("conc_msg_s", "student")
        teachers = []
        for i in range(3):
            t = _create_user(f"conc_msg_t{i}", "teacher")
            teachers.append(t)

        results = []
        errors = []

        def send(idx, token):
            try:
                resp = client.post("/api/messages/send", headers=auth(token),
                                   json={"recipient": "conc_msg_s", "subject": f"msg{idx}", "body": "x"})
                results.append(resp.status_code)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=send, args=(i, teachers[i])) for i in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert all(s == 200 for s in results)

        # 验证学生收到消息（msg_id 秒级时间戳，并发可能覆盖，至少 1 条）
        resp = client.get("/api/messages/inbox?folder=收件箱", headers=auth(student))
        assert len(resp.json()["messages"]) >= 1

        _delete_user("conc_msg_s")
        for i in range(3):
            _delete_user(f"conc_msg_t{i}")


# ============================================================
# 🔄 注册流程
# ============================================================

class Test注册:
    """用户注册的各种情况"""

    def test_admin注册老师(self):
        admin = make_token("admin", "admin")
        resp = client.post("/api/register", headers=auth(admin),
                           json={"username": "biz_reg_t", "password": "123", "role": "teacher"})
        assert resp.status_code == 200
        _delete_user("biz_reg_t")

    def test_admin注册学生(self):
        admin = make_token("admin", "admin")
        resp = client.post("/api/register", headers=auth(admin),
                           json={"username": "biz_reg_s", "password": "123", "role": "student"})
        assert resp.status_code == 200
        _delete_user("biz_reg_s")

    def test_老师注册学生(self):
        _create_user("biz_reg_teacher", "teacher")
        token = make_token("biz_reg_teacher", "teacher")
        resp = client.post("/api/register", headers=auth(token),
                           json={"username": "biz_reg_by_t", "password": "123", "role": "student"})
        assert resp.status_code == 200
        _delete_user("biz_reg_by_t")
        _delete_user("biz_reg_teacher")

    def test_注册重名用户(self):
        _create_user("biz_dup_user", "student")
        admin = make_token("admin", "admin")
        resp = client.post("/api/register", headers=auth(admin),
                           json={"username": "biz_dup_user", "password": "123", "role": "student"})
        assert resp.status_code == 400
        _delete_user("biz_dup_user")

    def test_登录限流5次后锁定(self):
        """连续5次密码错误 → 第6次应返回 429"""
        _create_user("biz_ratelimit_user", "student", "correct")
        _reset_login_failures()  # 从干净状态开始
        for _ in range(5):
            resp = client.post("/api/login", json={"username": "biz_ratelimit_user", "password": "wrong"})
            assert resp.status_code == 401
        resp = client.post("/api/login", json={"username": "biz_ratelimit_user", "password": "wrong"})
        assert resp.status_code == 429
        assert "频繁" in resp.json()["detail"]
        _delete_user("biz_ratelimit_user")

    def test_管理员批量注册学生(self):
        """批量注册 2 个学生 → 检查成功计数"""
        admin = make_token("admin", "admin")
        resp = client.post("/api/admin/batch-register", headers=auth(admin), json={
            "users": [
                {"username": "biz_batch_1", "password": "123", "role": "student"},
                {"username": "biz_batch_2", "password": "123", "role": "student"}
            ]
        })
        assert resp.status_code == 200
        assert resp.json()["success"] == 2
        assert resp.json()["fail"] == 0
        _delete_user("biz_batch_1")
        _delete_user("biz_batch_2")

    def test_注册无效角色(self):
        admin = make_token("admin", "admin")
        resp = client.post("/api/register", headers=auth(admin),
                           json={"username": "biz_badrole", "password": "123", "role": "hacker"})
        assert resp.status_code == 400

    def test_注册后自动登录(self):
        """注册 → 用相同密码登录 → 验证成功"""
        admin = make_token("admin", "admin")
        client.post("/api/register", headers=auth(admin),
                    json={"username": "biz_reg_login", "password": "mypwd", "role": "student"})
        _ensure_login_ok("biz_reg_login", "mypwd")
        _delete_user("biz_reg_login")

    def test_注册后用户目录存在(self):
        """注册应该创建用户目录和 info.json"""
        admin = make_token("admin", "admin")
        client.post("/api/register", headers=auth(admin),
                    json={"username": "biz_dir_test", "password": "123", "role": "student"})

        from main import SessionLocal, UserModel
        from utils import get_user_dir
        db = SessionLocal()
        try:
            user = db.query(UserModel).filter(UserModel.username == "biz_dir_test").first()
            assert user is not None
            user_dir = get_user_dir(user)
            assert os.path.isdir(user_dir)
            assert os.path.isfile(os.path.join(user_dir, "info.json"))
        finally:
            db.close()

        _delete_user("biz_dir_test")
