"""
后端安全与权限测试
==================
覆盖所有 API 的鉴权、越权防护、数据泄露、上传安全等。
合并了原 test_security.py + test_security_fixes.py。

运行方式：
  cd C:\Arin - cabin\QC-PocketSpace\qc-pocket-space\tests
  venv\Scripts\python.exe -m pytest test_security.py -v
"""
import sys
import os
import io

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
    """生成测试 token"""
    return generate_token(username, role)


def auth(token: str) -> dict:
    """Authorization 请求头"""
    return {"Authorization": f"Bearer {token}"}


def setup_function():
    """每个测试前清理 token 和限流状态，避免互相污染"""
    ACTIVE_TOKENS.clear()
    _reset_login_failures()


def _create_user(username: str, role: str, password: str = "123") -> str:
    """通过 API 创建测试用户并返回 token（admin 操作）"""
    admin = make_token("admin", "admin")
    client.post("/api/register", headers=auth(admin),
                json={"username": username, "password": password, "role": role})
    return make_token(username, role)


def _delete_user(username: str):
    """通过 API 删除测试用户（软删 + 清空垃圾桶，确保不留残余）"""
    admin = make_token("admin", "admin")
    resp = client.delete(f"/api/users/{username}", headers=auth(admin))
    if resp.status_code == 200:
        trash_id = resp.json().get("trash", {}).get("id")
        if trash_id:
            client.delete(f"/api/trash/{trash_id}", headers=auth(make_token("admin", "admin")))

# ============================================================
# 🔑 基础认证
# ============================================================

class Test基础认证:
    """未登录 / token 无效时的行为"""

    def test_没有token访问受保护接口(self):
        """无 token → 401"""
        resp = client.get("/api/user/profile")
        assert resp.status_code == 401

    def test_假token(self):
        """伪造 token → 401"""
        resp = client.get("/api/user/profile", headers=auth("fake_token_123"))
        assert resp.status_code == 401

    def test_登录接口不需要token(self):
        """POST /api/login 在白名单中，无需 token"""
        resp = client.post("/api/login", json={"username": "不存在", "password": "x"})
        assert resp.status_code == 401

    def test_注册接口需要token(self):
        """POST /api/register 必须登录"""
        resp = client.post("/api/register", json={
            "username": "hack_user", "password": "123", "role": "student"
        })
        assert resp.status_code == 401

    def test_公开接口不需要token(self):
        """GET /api/users 和 GET /api/courses 是公开接口"""
        resp_users = client.get("/api/users")
        resp_courses = client.get("/api/courses")
        assert resp_users.status_code == 200
        assert resp_courses.status_code == 200

    def test_登录成功返回token(self):
        """正确的账号密码应该返回 token"""
        admin = make_token("admin", "admin")
        _create_user("sec_login_ok", "student", "pass123")
        resp = client.post("/api/login", json={"username": "sec_login_ok", "password": "pass123"})
        assert resp.status_code == 200
        assert "token" in resp.json()
        _delete_user("sec_login_ok")

    def test_登录密码错误(self):
        """错误密码 → 401"""
        _create_user("sec_login_fail", "student", "correct")
        resp = client.post("/api/login", json={"username": "sec_login_fail", "password": "wrong"})
        assert resp.status_code == 401
        _delete_user("sec_login_fail")

    def test_登录不存在的用户(self):
        """不存在的用户 → 401"""
        resp = client.post("/api/login", json={"username": "ghost_user_xyz", "password": "123"})
        assert resp.status_code == 401


# ============================================================
# 🚫 学生越权（核心安全测试）
# ============================================================

class Test学生越权:
    """学生不应该能执行管理操作"""

    def test_学生注册老师(self):
        token = make_token("sec_stu", "student")
        resp = client.post("/api/register", headers=auth(token),
                           json={"username": "假老师", "password": "123", "role": "teacher"})
        assert resp.status_code == 403

    def test_学生注册学生(self):
        token = make_token("sec_stu", "student")
        resp = client.post("/api/register", headers=auth(token),
                           json={"username": "假学生", "password": "123", "role": "student"})
        assert resp.status_code == 403

    def test_学生删除用户(self):
        token = make_token("sec_stu", "student")
        resp = client.delete("/api/users/随便谁", headers=auth(token))
        assert resp.status_code == 403

    def test_学生创建班级(self):
        token = make_token("sec_stu", "student")
        resp = client.post("/api/classes", headers=auth(token), json={"name": "假班级"})
        assert resp.status_code == 403

    def test_学生更新班级(self):
        token = make_token("sec_stu", "student")
        resp = client.put("/api/classes/不存在", headers=auth(token), json={"name": "改名"})
        assert resp.status_code == 403

    def test_学生删除班级(self):
        token = make_token("sec_stu", "student")
        resp = client.delete("/api/classes/不存在", headers=auth(token))
        assert resp.status_code == 403

    def test_学生添加学生到班级(self):
        token = make_token("sec_stu", "student")
        resp = client.post("/api/classes/不存在/students", headers=auth(token),
                           json={"name": "张三"})
        assert resp.status_code == 403

    def test_学生移除班级学生(self):
        token = make_token("sec_stu", "student")
        resp = client.delete("/api/classes/不存在/students/1", headers=auth(token))
        assert resp.status_code == 403

    def test_学生查找学生(self):
        token = make_token("sec_stu", "student")
        resp = client.get("/api/classes/find-student?name=x", headers=auth(token))
        assert resp.status_code == 403

    def test_学生查看全校学生列表(self):
        token = make_token("sec_stu", "student")
        resp = client.get("/api/students/list", headers=auth(token))
        assert resp.status_code == 403

    def test_学生发消息(self):
        token = make_token("sec_stu", "student")
        resp = client.post("/api/messages/send", headers=auth(token),
                           json={"recipient": "x", "subject": "x", "body": "x"})
        assert resp.status_code == 403

    def test_学生查看用户垃圾桶(self):
        token = make_token("sec_stu", "student")
        resp = client.get("/api/trash", headers=auth(token))
        assert resp.status_code == 403

    def test_学生恢复用户(self):
        token = make_token("sec_stu", "student")
        resp = client.post("/api/trash/restore/1", headers=auth(token))
        assert resp.status_code == 403

    def test_学生清空垃圾桶(self):
        token = make_token("sec_stu", "student")
        resp = client.delete("/api/trash", headers=auth(token))
        assert resp.status_code == 403

    def test_学生保存课件sb3(self):
        token = make_token("sec_stu", "student")
        resp = client.post("/api/courses/test/materials/test/save-sb3", headers=auth(token),
                           files={"file": ("t.sb3", b"fake", "application/octet-stream")})
        assert resp.status_code == 403

    def test_学生上传课件文件(self):
        token = make_token("sec_stu", "student")
        resp = client.post("/api/courses/test/materials/test/upload", headers=auth(token),
                           files={"file": ("t.txt", b"fake", "application/octet-stream")})
        assert resp.status_code == 403

    def test_学生创建课程(self):
        token = make_token("sec_stu", "student")
        resp = client.post("/api/courses", headers=auth(token),
                           json={"teacher": "sec_stu", "name": "假课程", "course_type": "scratch"})
        assert resp.status_code == 403

    def test_学生删除课程(self):
        token = make_token("sec_stu", "student")
        resp = client.delete("/api/courses/不存在", headers=auth(token))
        assert resp.status_code == 403

    def test_学生保存课堂记录(self):
        token = make_token("sec_stu", "student")
        resp = client.post("/api/sessions/不存在", headers=auth(token),
                           json={"date": "2026-01-01", "students": []})
        assert resp.status_code == 403

    def test_学生删除课堂记录(self):
        token = make_token("sec_stu", "student")
        resp = client.delete("/api/sessions/不存在/2026-01-01", headers=auth(token))
        assert resp.status_code == 403

    def test_学生永久删除回收站项目(self):
        token = make_token("sec_stu", "student")
        resp = client.delete("/api/trash/items/fake_folder", headers=auth(token))
        assert resp.status_code == 403


# ============================================================
# 🚫 老师权限边界
# ============================================================

class Test老师越权:
    """老师不能做只有 admin 才能做的事"""

    def test_老师注册老师(self):
        token = make_token("sec_teacher", "teacher")
        resp = client.post("/api/register", headers=auth(token),
                           json={"username": "假老师", "password": "123", "role": "teacher"})
        assert resp.status_code == 403

    def test_老师删除用户(self):
        token = make_token("sec_teacher", "teacher")
        resp = client.delete("/api/users/随便谁", headers=auth(token))
        assert resp.status_code == 403

    def test_老师查看用户垃圾桶(self):
        token = make_token("sec_teacher", "teacher")
        resp = client.get("/api/trash", headers=auth(token))
        assert resp.status_code == 403

    def test_老师永久删除回收站(self):
        token = make_token("sec_teacher", "teacher")
        resp = client.delete("/api/trash/items/fake", headers=auth(token))
        assert resp.status_code == 403

    def test_老师清空垃圾桶(self):
        token = make_token("sec_teacher", "teacher")
        resp = client.delete("/api/trash", headers=auth(token))
        assert resp.status_code == 403


# ============================================================
# ✅ 角色权限正向测试
# ============================================================

class Test角色权限正向:
    """确认各角色该有的权限确实能用"""

    def test_admin注册老师(self):
        admin = make_token("admin", "admin")
        resp = client.post("/api/register", headers=auth(admin),
                           json={"username": "sec_ok_teacher", "password": "123", "role": "teacher"})
        assert resp.status_code == 200
        _delete_user("sec_ok_teacher")

    def test_admin注册学生(self):
        admin = make_token("admin", "admin")
        resp = client.post("/api/register", headers=auth(admin),
                           json={"username": "sec_ok_student", "password": "123", "role": "student"})
        assert resp.status_code == 200
        _delete_user("sec_ok_student")

    def test_老师注册学生(self):
        _create_user("sec_t_perm", "teacher")
        token = make_token("sec_t_perm", "teacher")
        resp = client.post("/api/register", headers=auth(token),
                           json={"username": "sec_by_teacher", "password": "123", "role": "student"})
        assert resp.status_code == 200
        _delete_user("sec_by_teacher")
        _delete_user("sec_t_perm")

    def test_admin删除用户(self):
        _create_user("sec_del_target", "student")
        admin = make_token("admin", "admin")
        resp = client.delete("/api/users/sec_del_target", headers=auth(admin))
        assert resp.status_code == 200
        # 硬清垃圾桶
        trash_id = resp.json().get("trash", {}).get("id")
        if trash_id:
            client.delete(f"/api/trash/{trash_id}", headers=auth(make_token("admin", "admin")))

    def test_不能删除管理员(self):
        """admin 不能删除自己"""
        admin = make_token("admin", "admin")
        resp = client.delete("/api/users/admin", headers=auth(admin))
        assert resp.status_code == 403

    def test_admin不能创建班级(self):
        """管理员只做管理，班级/点名是老师专属"""
        admin = make_token("admin", "admin")
        resp = client.post("/api/classes", headers=auth(admin), json={"name": "sec_admin_cls"})
        assert resp.status_code == 403

    def test_老师创建班级(self):
        _create_user("sec_cls_t", "teacher")
        token = make_token("sec_cls_t", "teacher")
        resp = client.post("/api/classes", headers=auth(token), json={"name": "sec_t_cls"})
        assert resp.status_code == 200
        client.delete("/api/classes/sec_t_cls", headers=auth(token))
        _delete_user("sec_cls_t")

    def test_admin不能发消息给学生(self):
        """管理员不参与教学，不能给学生发站内信"""
        _create_user("sec_msg_stu", "student")
        admin = make_token("admin", "admin")
        resp = client.post("/api/messages/send", headers=auth(admin),
                           json={"recipient": "sec_msg_stu", "subject": "hi", "body": "hello"})
        assert resp.status_code == 403
        _delete_user("sec_msg_stu")

    def test_老师发消息(self):
        _create_user("sec_msg_t", "teacher")
        _create_user("sec_msg_s", "student")
        token = make_token("sec_msg_t", "teacher")
        resp = client.post("/api/messages/send", headers=auth(token),
                           json={"recipient": "sec_msg_s", "subject": "hi", "body": "hello"})
        assert resp.status_code == 200
        _delete_user("sec_msg_t")
        _delete_user("sec_msg_s")


# ============================================================
# 🔒 删除用户后 token 失效
# ============================================================

class TestToken失效:
    """用户被删除后，其所有 token 应立即失效"""

    def test_删除用户后token被清除(self):
        _create_user("sec_token_u", "student")
        student_token = make_token("sec_token_u", "student")
        assert student_token in ACTIVE_TOKENS

        admin = make_token("admin", "admin")
        resp = client.delete("/api/users/sec_token_u", headers=auth(admin))
        assert student_token not in ACTIVE_TOKENS
        # 硬清垃圾桶
        trash_id = resp.json().get("trash", {}).get("id")
        if trash_id:
            client.delete(f"/api/trash/{trash_id}", headers=auth(make_token("admin", "admin")))

    def test_删除用户后无法访问接口(self):
        _create_user("sec_token_u2", "student")
        student_token = make_token("sec_token_u2", "student")

        admin = make_token("admin", "admin")
        resp = client.delete("/api/users/sec_token_u2", headers=auth(admin))
        # 硬清垃圾桶
        trash_id = resp.json().get("trash", {}).get("id")
        if trash_id:
            client.delete(f"/api/trash/{trash_id}", headers=auth(make_token("admin", "admin")))

        resp = client.get("/api/user/profile", headers=auth(student_token))
        assert resp.status_code == 401


# ============================================================
# � 密码哈希
# ============================================================

class Test密码哈希:
    """密码必须以哈希存储，不能明文"""

    def test_数据库中密码不是明文(self):
        """新注册用户的密码字段应以 sha256$ 开头"""
        from main import SessionLocal, UserModel
        _create_user("sec_hash_check", "student", "mypassword")
        db = SessionLocal()
        try:
            user = db.query(UserModel).filter(UserModel.username == "sec_hash_check").first()
            assert user is not None
            assert user.password.startswith("sha256$"), f"密码是明文: {user.password}"
            assert user.password != "mypassword"
        finally:
            db.close()
        _delete_user("sec_hash_check")

    def test_修改密码后也是哈希(self):
        _create_user("sec_hash_chg", "student", "old123")
        token = make_token("sec_hash_chg", "student")
        client.post("/api/user/change-password", headers=auth(token),
                    json={"username": "sec_hash_chg", "old_password": "old123", "new_password": "new456"})
        from main import SessionLocal, UserModel
        db = SessionLocal()
        try:
            user = db.query(UserModel).filter(UserModel.username == "sec_hash_chg").first()
            assert user.password.startswith("sha256$")
        finally:
            db.close()
        _delete_user("sec_hash_chg")


# ============================================================
# �📁 上传安全
# ============================================================

class Test上传文件名安全:
    """文件名路径穿越防护"""

    def _setup_course(self):
        admin = make_token("admin", "admin")
        resp = client.post("/api/courses", headers=auth(admin),
                    json={"teacher": "admin", "name": "sec_upload", "course_type": "scratch"})
        if resp.status_code == 200:
            course_id = resp.json()["course"]["id"]
        else:
            # 已存在 → 从列表取 ID
            list_resp = client.get("/api/courses?teacher=admin")
            courses = [c for c in list_resp.json()["courses"] if c["name"] == "sec_upload"]
            course_id = courses[0]["id"]
        return admin, course_id

    def _upload(self, filename: str, token_or_tuple):
        if isinstance(token_or_tuple, tuple):
            token, course_id = token_or_tuple
        else:
            token = token_or_tuple
            course_id = "sec_upload"
        return client.post(
            f"/api/courses/{course_id}/materials/lesson1/upload",
            headers=auth(token),
            files={"file": (filename, io.BytesIO(b"fake"), "application/octet-stream")}
        )

    def test_正常文件名(self):
        admin, cid = self._setup_course()
        resp = self._upload("hello.png", (admin, cid))
        assert resp.status_code == 200

    def test_路径穿越文件名(self):
        admin, cid = self._setup_course()
        resp = self._upload("../../etc/passwd", (admin, cid))
        if resp.status_code == 200:
            assert "../" not in resp.json().get("message", "")
        else:
            assert resp.status_code in (400, 403, 413)

    def test_点号开头文件名(self):
        admin, cid = self._setup_course()
        resp = self._upload(".hidden_file", (admin, cid))
        assert resp.status_code in (200, 400, 403)


class Test上传大小限制:
    """文件大小不能超过 50MB"""

    def _create_or_find_course(self, name):
        admin = make_token("admin", "admin")
        resp = client.post("/api/courses", headers=auth(admin),
                    json={"teacher": "admin", "name": name, "course_type": "scratch"})
        if resp.status_code == 200:
            return admin, resp.json()["course"]["id"]
        list_resp = client.get("/api/courses?teacher=admin")
        courses = [c for c in list_resp.json()["courses"] if c["name"] == name]
        return admin, courses[0]["id"]

    def test_超大文件被拒绝(self):
        admin, cid = self._create_or_find_course("sec_big")
        big = b"x" * (51 * 1024 * 1024)
        resp = client.post(
            f"/api/courses/{cid}/materials/lesson1/upload",
            headers=auth(admin),
            files={"file": ("big.bin", io.BytesIO(big), "application/octet-stream")}
        )
        assert resp.status_code == 413

    def test_sb3超大文件被拒绝(self):
        admin, cid = self._create_or_find_course("sec_sb3big")
        big = b"PK" + b"\x00" * (51 * 1024 * 1024)
        resp = client.post(
            f"/api/courses/{cid}/materials/lesson1/save-sb3?file_type=main",
            headers=auth(admin),
            files={"file": ("main.sb3", io.BytesIO(big), "application/zip")}
        )
        assert resp.status_code == 413

    def test_正常大小文件通过(self):
        admin, cid = self._create_or_find_course("sec_smallok")
        small = b"x" * 1024
        resp = client.post(
            f"/api/courses/{cid}/materials/lesson1/upload",
            headers=auth(admin),
            files={"file": ("ok.txt", io.BytesIO(small), "text/plain")}
        )
        assert resp.status_code == 200


# ============================================================
# 🔍 数据泄露防护
# ============================================================

class Test数据泄露:
    """API 返回数据不能包含敏感字段"""

    def test_用户列表不含密码(self):
        sensitive = {"password", "pwd", "passwd", "pass", "secret", "token", "hash"}
        resp = client.get("/api/users")
        for user in resp.json().get("users", []):
            leaked = sensitive & set(user.keys())
            assert not leaked, f"用户 {user.get('username')} 泄露了：{leaked}"

    def test_用户列表只含必要字段(self):
        allowed = {"id", "username", "role", "avatar_path"}
        resp = client.get("/api/users")
        for user in resp.json().get("users", []):
            extra = set(user.keys()) - allowed
            assert not extra, f"多返回了字段：{extra}"

    def test_profile不含密码(self):
        admin = make_token("admin", "admin")
        resp = client.get("/api/user/profile", headers=auth(admin))
        assert "password" not in resp.json()["user"]

    def test_登录返回不含密码(self):
        _create_user("sec_leak_test", "student", "test123")
        resp = client.post("/api/login", json={"username": "sec_leak_test", "password": "test123"})
        assert "password" not in resp.json()["user"]
        _delete_user("sec_leak_test")

    def test_班级列表不含密码(self):
        _create_user("sec_cls_leak_t", "teacher")
        token = make_token("sec_cls_leak_t", "teacher")
        client.post("/api/classes", headers=auth(token), json={"name": "sec_cls_leak"})
        resp = client.get("/api/classes", headers=auth(token))
        assert resp.status_code == 200
        # students 里不应含 password
        for c in resp.json().get("classes", []):
            for s in c.get("students", []):
                assert "password" not in s
        client.delete("/api/classes/sec_cls_leak", headers=auth(token))
        _delete_user("sec_cls_leak_t")


# ============================================================
# 🛡️ 边界和异常输入
# ============================================================

class Test边界输入:
    """各种异常输入不应导致服务崩溃"""

    def test_不存在的路由(self):
        resp = client.get("/users/")
        assert resp.status_code == 404

    def test_不存在的API(self):
        resp = client.get("/api/nonexistent")
        assert resp.status_code in (404, 405)

    def test_登录空密码(self):
        resp = client.post("/api/login", json={"username": "admin", "password": ""})
        assert resp.status_code == 401

    def test_修改密码原密码错误(self):
        admin = make_token("admin", "admin")
        resp = client.post("/api/user/change-password", headers=auth(admin),
                           json={"username": "admin", "old_password": "wrong", "new_password": "new"})
        assert resp.status_code == 400

    def test_描述符非法JSON(self):
        admin = make_token("admin", "admin")
        resp = client.post("/api/update-descriptors", headers=auth(admin),
                           json={"qc_descriptors": "不是JSON"})
        assert resp.status_code == 400

    def test_头像文件名不合法(self):
        admin = make_token("admin", "admin")
        resp = client.post("/api/update-avatar", headers=auth(admin),
                           json={"username": "admin", "avatar_path": "../../../etc/passwd"})
        assert resp.status_code == 400

    def test_创建重名班级(self):
        token = _create_user("sec_dup_t", "teacher")
        client.post("/api/classes", headers=auth(token), json={"name": "sec_dup"})
        resp = client.post("/api/classes", headers=auth(token), json={"name": "sec_dup"})
        assert resp.status_code == 400
        client.delete("/api/classes/sec_dup", headers=auth(token))
        _delete_user("sec_dup_t")

    def test_删除不存在的班级(self):
        token = _create_user("sec_del_cls_t", "teacher")
        resp = client.delete("/api/classes/不存在xyz", headers=auth(token))
        assert resp.status_code == 404
        _delete_user("sec_del_cls_t")

    def test_注册无效角色(self):
        admin = make_token("admin", "admin")
        resp = client.post("/api/register", headers=auth(admin),
                           json={"username": "sec_badrole", "password": "123", "role": "hacker"})
        assert resp.status_code == 400

    def test_注册重名用户(self):
        _create_user("sec_dup_user", "student")
        admin = make_token("admin", "admin")
        resp = client.post("/api/register", headers=auth(admin),
                           json={"username": "sec_dup_user", "password": "123", "role": "student"})
        assert resp.status_code == 400
        _delete_user("sec_dup_user")

    def test_删除不存在的用户(self):
        admin = make_token("admin", "admin")
        resp = client.delete("/api/users/不存在xyz", headers=auth(admin))
        assert resp.status_code == 404

    def test_更新不存在用户的头像(self):
        """用一个 token 访问时用户必须存在"""
        token = make_token("ghost_user", "student")
        resp = client.post("/api/update-avatar", headers=auth(token),
                           json={"username": "ghost_user", "avatar_path": "avatar_01.jpg"})
        assert resp.status_code == 404

    def test_发送消息给不存在的学生(self):
        _create_user("sec_msg_err_t", "teacher")
        token = make_token("sec_msg_err_t", "teacher")
        resp = client.post("/api/messages/send", headers=auth(token),
                           json={"recipient": "不存在的学生", "subject": "x", "body": "x"})
        assert resp.status_code == 404
        _delete_user("sec_msg_err_t")

    def test_查看不存在学生的笔记(self):
        token = make_token("不存在的用户", "student")
        resp = client.get("/api/notebooks", headers=auth(token))
        # 用户不在 DB → _resolve_user_dir 返回 None → 404
        assert resp.status_code == 404
