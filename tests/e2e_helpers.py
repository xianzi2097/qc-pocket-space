"""
E2E 测试辅助工具
================
封装与后端 API 交互、浏览器操作等公共逻辑。
用 httpx 调用后端 API 注入测试数据，用 Playwright 操作浏览器。
"""
import re
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
    """获取 admin token（每次新获取，避免过期）；429=限流时按提示等待后重试一次。
    背景：服务器失败计数在进程内跨轮累积，长命服务器连续跑多轮 e2e 会触发封禁。"""
    payload = {"username": ADMIN_USER, "password": ADMIN_PASS}
    resp = httpx.post(f"{API_BASE}/login", json=payload, timeout=5)
    if resp.status_code == 429:
        m = re.search(r"(\d+)\s*秒", str(resp.json().get("detail", "")))
        time.sleep(min(int(m.group(1)) if m else 30, 65) + 1)
        resp = httpx.post(f"{API_BASE}/login", json=payload, timeout=5)
    assert resp.status_code == 200, f"admin 登录失败: {resp.text}"
    return resp.json()["token"]


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def api_login(username: str, password: str) -> Optional[dict]:
    """登录并返回完整响应 json，密码错误返回 None（429 先等封禁过期重试，避免误判成密码错）"""
    payload = {"username": username, "password": password}
    resp = httpx.post(f"{API_BASE}/login", json=payload, timeout=5)
    if resp.status_code == 429:
        m = re.search(r"(\d+)\s*秒", str(resp.json().get("detail", "")))
        time.sleep(min(int(m.group(1)) if m else 30, 65) + 1)
        resp = httpx.post(f"{API_BASE}/login", json=payload, timeout=5)
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


def feature_course(teacher_token: str, course_id: str, featured: bool = True) -> dict:
    """把课程放进「课程」架（shelf=courses）；featured=False 则放回「书架」"""
    resp = httpx.put(f"{API_BASE}/courses/{course_id}/shelf",
                     headers=_auth_header(teacher_token),
                     json={"shelf": "courses" if featured else "books"}, timeout=5)
    assert resp.status_code == 200, f"设置课程书架失败: {resp.text}"
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
    """首页 → 侧栏登录按钮 → 登录弹窗 → 填写提交"""
    page.goto(BASE_URL, wait_until="domcontentloaded", timeout=10000)
    page.wait_for_timeout(1000)
    # 登录入口两种形态：折叠侧栏=图标按钮(data-tip='登录')；改密等场景会话未失效时先清本地会话
    login_btn = page.locator("button[data-tip='登录']")
    if login_btn.count() == 0:
        # token 改密后不失效，清 user/auth_token 强制登出（sidebarRail 保留，侧栏可能为展开态）
        page.evaluate("() => ['auth_token','token','user'].forEach(k => localStorage.removeItem(k))")
        page.reload(wait_until="domcontentloaded")
        page.wait_for_timeout(1000)
        login_btn = page.locator("button[data-tip='登录']")
        if login_btn.count() == 0:
            # 展开态侧栏：入口是带「登录」文字的按钮（无 data-tip）
            login_btn = page.get_by_role("button").filter(has_text="登录").first
    login_btn.click()
    # 弹窗内两个输入框（用户名/密码）无 placeholder，按顺序定位
    inputs = page.locator("input")
    inputs.nth(0).wait_for(state="visible", timeout=5000)
    inputs.nth(0).fill(username)
    inputs.nth(1).fill(password)
    # 提交按钮文案是「进入」
    page.get_by_role("button").filter(has_text="进入").last.click()
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
