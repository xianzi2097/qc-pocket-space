"""
E2E 测试辅助工具
================
封装与后端 API 交互、浏览器操作等公共逻辑。
用 httpx 调用后端 API 注入测试数据，用 Playwright 操作浏览器。
"""
import time
import httpx
from typing import Optional
from playwright.sync_api import Page, expect

BASE_URL = "http://localhost"
API_BASE = f"{BASE_URL}/api"
ADMIN_USER = "admin"
ADMIN_PASS = "111111"


# ============================================================
# API 交互（httpx）
# ============================================================

def _admin_token() -> str:
    """获取 admin token（每次新获取，避免过期）"""
    resp = httpx.post(f"{API_BASE}/login", json={
        "username": ADMIN_USER, "password": ADMIN_PASS
    }, timeout=5)
    assert resp.status_code == 200, f"admin 登录失败: {resp.text}"
    return resp.json()["token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def api_login(username: str, password: str) -> Optional[dict]:
    """登录并返回完整响应 json，失败返回 None"""
    resp = httpx.post(f"{API_BASE}/login", json={
        "username": username, "password": password
    }, timeout=5)
    if resp.status_code == 200:
        return resp.json()
    return None


def create_test_teacher(suffix: str) -> dict:
    """创建一个测试老师账号，返回 {username, password, token}"""
    username = f"e2e_t_{suffix}"
    password = "111111"
    token = _admin_token()
    resp = httpx.post(f"{API_BASE}/register", headers=_auth_header(token), json={
        "username": username, "password": password, "role": "teacher"
    }, timeout=5)
    if resp.status_code == 400 and "已存在" in resp.text:
        # 已存在则重用
        pass
    else:
        assert resp.status_code == 200, f"创建老师失败: {resp.text}"
    # 获取新 token
    login_resp = api_login(username, password)
    assert login_resp, f"老师登录失败: {username}"
    return {"username": username, "password": password, "token": login_resp["token"]}


def create_test_student(suffix: str, teacher_name: str = "") -> dict:
    """创建一个测试学生账号，返回 {username, password, token}"""
    username = f"e2e_s_{suffix}"
    password = "111111"
    # 老师可以注册学生
    token = _admin_token()
    resp = httpx.post(f"{API_BASE}/register", headers=_auth_header(token), json={
        "username": username, "password": password, "role": "student"
    }, timeout=5)
    if resp.status_code == 400 and "已存在" in resp.text:
        pass
    else:
        assert resp.status_code == 200, f"创建学生失败: {resp.text}"
    login_resp = api_login(username, password)
    assert login_resp, f"学生登录失败: {username}"

    # 如果指定了老师，设置描述符
    if teacher_name:
        student_token = login_resp["token"]
        httpx.post(f"{API_BASE}/update-descriptors", headers=_auth_header(student_token), json={
            "qc_descriptors": f'{{"teacher": "{teacher_name}"}}'
        }, timeout=5)

    return {"username": username, "password": password, "token": login_resp["token"]}


def create_class(teacher_token: str, class_name: str, schedule: Optional[list] = None) -> dict:
    """为老师创建一个班级，返回响应 json"""
    if schedule is None:
        schedule = {"days": ["周一", "周三"], "time": "14:00-15:30", "location": "测试教室"}
    resp = httpx.post(f"{API_BASE}/classes", headers=_auth_header(teacher_token), json={
        "name": class_name, "schedule": schedule
    }, timeout=5)
    assert resp.status_code == 200, f"创建班级失败: {resp.text}"
    return resp.json()


def add_student_to_class(teacher_token: str, class_name: str,
                         student_name: str, student_id: int) -> dict:
    """添加已有学生到班级"""
    resp = httpx.post(f"{API_BASE}/classes/{class_name}/students",
                      headers=_auth_header(teacher_token), json={
                          "name": student_name, "existing_id": str(student_id)
                      }, timeout=5)
    assert resp.status_code == 200, f"添加学生失败: {resp.text}"
    return resp.json()


def get_user_id(username: str) -> Optional[int]:
    """通过公开 API 获取用户 ID"""
    resp = httpx.get(f"{API_BASE}/users", timeout=5)
    assert resp.status_code == 200
    for u in resp.json().get("users", []):
        if u["username"] == username:
            return u["id"]
    return None


def send_message(teacher_token: str, recipient: str, subject: str, body: str) -> dict:
    """老师发送站内信给学生"""
    resp = httpx.post(f"{API_BASE}/messages/send", headers=_auth_header(teacher_token), json={
        "recipient": recipient, "subject": subject, "body": body
    }, timeout=5)
    assert resp.status_code == 200, f"发送消息失败: {resp.text}"
    return resp.json()


def create_course(teacher_token: str, teacher_name: str,
                  course_name: str, course_type: str = "scratch") -> dict:
    """创建一门课程"""
    resp = httpx.post(f"{API_BASE}/courses", headers=_auth_header(teacher_token), json={
        "teacher": teacher_name, "name": course_name, "course_type": course_type
    }, timeout=5)
    if resp.status_code == 400 and "已存在" in resp.text:
        pass
    else:
        assert resp.status_code == 200, f"创建课程失败: {resp.text}"
    return resp.json()


def save_attendance(teacher_token: str, class_name: str, date: str,
                    students: list) -> dict:
    """保存课堂记录"""
    resp = httpx.post(f"{API_BASE}/sessions/{class_name}",
                      headers=_auth_header(teacher_token), json={
                          "date": date, "students": students
                      }, timeout=5)
    assert resp.status_code == 200, f"保存课堂记录失败: {resp.text}"
    return resp.json()


def delete_user(username: str):
    """删除用户（admin 权限）"""
    token = _admin_token()
    httpx.delete(f"{API_BASE}/users/{username}", headers=_auth_header(token), timeout=5)


def cleanup_e2e_users():
    """删除所有 e2e_ 前缀的测试用户（软删 + 硬清垃圾桶）"""
    token = _admin_token()
    resp = httpx.get(f"{API_BASE}/users", timeout=5)
    assert resp.status_code == 200
    for u in resp.json().get("users", []):
        if u["username"].startswith("e2e_"):
            del_resp = httpx.delete(f"{API_BASE}/users/{u['username']}",
                         headers=_auth_header(token), timeout=5)
            # 硬清垃圾桶
            if del_resp.status_code == 200:
                trash_id = del_resp.json().get("trash", {}).get("id")
                if trash_id:
                    t = _admin_token()
                    httpx.delete(f"{API_BASE}/trash/{trash_id}",
                                 headers=_auth_header(t), timeout=5)
    # 也直接清空所有 e2e_ 垃圾桶记录
    t = _admin_token()
    trash_resp = httpx.get(f"{API_BASE}/trash", headers=_auth_header(t), timeout=5)
    if trash_resp.status_code == 200:
        for item in trash_resp.json().get("trash", []):
            if item["username"].startswith("e2e_"):
                httpx.delete(f"{API_BASE}/trash/{item['id']}",
                             headers=_auth_header(t), timeout=5)


# ============================================================
# 浏览器操作（Playwright）
# ============================================================

def login(page: Page, username: str, password: str = "111111"):
    """首页 → 个人中心卡片 → 登录表单 → 填写提交"""
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=10000)
    page.wait_for_timeout(1000)
    # 点"个人中心"卡片上的登录按钮
    login_btn = page.get_by_role("button").filter(has_text="登录").first
    login_btn.wait_for(state="visible", timeout=5000)
    login_btn.click()
    page.wait_for_timeout(500)
    # 填写登录表单
    page.get_by_placeholder("用户名").fill(username)
    page.get_by_placeholder("密码").fill(password)
    page.get_by_role("button", name="登录").filter(has_text="登录").last.click()
    page.wait_for_timeout(2000)


def teacher_nav(page: Page, tab_text: str):
    """教师仪表板侧栏导航"""
    page.get_by_role("button").filter(has_text=tab_text).first.click()
    page.wait_for_timeout(500)


def student_nav(page: Page, item_title: str):
    """学生仪表板 FloatingDock 导航"""
    # FloatingDock 中的图标按钮可能没有 visible text，用 title 或 aria-label
    dock_item = page.locator("[class*='floating-dock'] button, [class*='floating-dock'] a")
    dock_item.filter(has_text=item_title).first.click()
    page.wait_for_timeout(500)


def get_page_text(page: Page) -> str:
    """获取页面主体可见文本"""
    return page.locator("body").inner_text()


def timestamp() -> str:
    """生成时间戳后缀，用于唯一命名"""
    return str(int(time.time() * 1000))[-8:]
