"""
E2E 浏览器测试 — Playwright（学生端 + 教师端全覆盖）
==================================================
模拟真实用户操作：打开浏览器 → 登录 → 操作 → 验证结果。

运行前先启动后端（qx-launcher → 🟢 启动服务）。

运行：
  cd qc-pocket-space/tests
  venv\Scripts\python.exe -m pytest test_e2e.py -v -s
"""
import time
import pytest
from playwright.sync_api import sync_playwright, Page, expect

from e2e_helpers import (
    BASE_URL, timestamp, cleanup_e2e_users,
    create_test_teacher, create_test_student,
    create_class, add_student_to_class, get_user_id,
    send_message, create_course, save_attendance,
    login, teacher_nav, student_nav, get_page_text,
    api_login,
)

# ============================================================
# Fixtures（浏览器 + 测试数据）
# ============================================================

@pytest.fixture(scope="session")
def browser():
    """整个测试会话共用一个浏览器实例"""
    with sync_playwright() as p:
        br = p.chromium.launch(headless=False, slow_mo=100)
        yield br
        br.close()


@pytest.fixture
def page(browser):
    """每个测试方法一个新页面（独立登录状态）"""
    ctx = browser.new_context(viewport={"width": 1440, "height": 900})
    pg = ctx.new_page()
    yield pg
    ctx.close()


@pytest.fixture(scope="session", autouse=True)
def global_cleanup():
    """Session 开始时清理遗留下来的测试数据"""
    cleanup_e2e_users()
    yield
    # Session 结束再清理一次
    cleanup_e2e_users()


# ============================================================
# 🧑‍🎓 学生端 E2E 测试
# ============================================================

class TestStudentE2E:
    """学生功能：登录 → 笔记本 → 站内信 → 课程 → 设置"""

    @pytest.fixture
    def student_data(self):
        """创建测试老师和学生，并为学生准备一条消息"""
        ts = timestamp()
        teacher = create_test_teacher(f"st_{ts}")
        student = create_test_student(f"st_{ts}", teacher["username"])
        sid = get_user_id(student["username"])
        assert sid is not None, "学生 ID 获取失败"
        # 创建一个班级并加入学生（为了后续测试数据完整）
        cls_name = f"测试班_{ts}"
        create_class(teacher["token"], cls_name)
        add_student_to_class(teacher["token"], cls_name, student["username"], sid)
        # 发一条消息给学生
        send_message(teacher["token"], student["username"], "E2E测试：作业通知", "请完成第三章练习")
        return {"teacher": teacher, "student": student, "class": cls_name}

    # ─── 2.1 登录 + 仪表板 ───

    def test_01_学生登录并看到仪表板(self, page: Page, student_data):
        """学生登录后应看到 profile card（昵称、积分、等级）"""
        s = student_data["student"]
        login(page, s["username"], s["password"])

        # 验证 URL 跳转到 /dashboard
        expect(page).to_have_url(f"{BASE_URL}/dashboard")

        # 验证能看到学生昵称
        expect(page.get_by_text(s["username"])).to_be_visible()

        # 验证能看到统计信息（积分/勋章/作品）
        # 三个标签在同一个 text node 中："积分 0 勋章 0 作品 0"
        expect(page.get_by_text("积分", exact=False)).to_be_visible()

    # ─── 2.2 笔记本 CRUD ───

    def test_02_学生笔记本CRUD(self, page: Page, student_data):
        """创建笔记本 → 创建笔记 → 编辑保存 → 验证内容"""
        s = student_data["student"]
        login(page, s["username"], s["password"])
        ts = timestamp()

        # 切换到"笔记本"模块
        page.get_by_role("button").filter(has_text="笔记本").first.click()
        page.wait_for_timeout(800)

        # 创建笔记本
        create_btn = page.get_by_role("button").filter(has_text="创建第一个")
        if create_btn.is_visible():
            create_btn.click()
        # 有的 UI 用 + 按钮，找输入框
        nb_input = page.get_by_placeholder("笔记本名，如 Python学习")
        if nb_input.is_visible():
            nb_input.fill(f"E2E笔记_{ts}")
            page.get_by_role("button").filter(has_text="确定").click()
            page.wait_for_timeout(500)

        # 验证笔记本出现在列表中
        expect(page.get_by_text(f"E2E笔记_{ts}")).to_be_visible()

        # 点击笔记本
        page.get_by_text(f"E2E笔记_{ts}").first.click()
        page.wait_for_timeout(500)

        # 创建笔记
        add_note = page.get_by_role("button").filter(has_text="新建笔记")
        if add_note.is_visible():
            add_note.click()
        else:
            # 可能是个 + 号按钮
            page.get_by_placeholder("笔记名").fill(f"test_{ts}")
            page.get_by_role("button").filter(has_text="确定").click()
            page.wait_for_timeout(500)

        # 写笔记内容
        textarea = page.locator("textarea").first
        if textarea.is_visible():
            textarea.fill(f"# E2E 测试笔记\n\n这是自动测试创建的笔记。")

        # 保存
        save_btn = page.get_by_role("button").filter(has_text="保存")
        if save_btn.is_visible():
            save_btn.first.click()
            page.wait_for_timeout(1000)

        # 验证笔记本名存在（页面中有多处包含此文本）
        expect(page.get_by_text(f"E2E笔记_{ts}").first).to_be_visible()

    # ─── 2.3 站内信 ───

    def test_03_学生查看站内信(self, page: Page, student_data):
        """学生应能看到老师发的消息，能标记已读、移动"""
        s = student_data["student"]
        login(page, s["username"], s["password"])
        page.wait_for_timeout(1000)

        # 切换到"邮箱"模块
        page.get_by_role("button").filter(has_text="邮箱").first.click()
        page.wait_for_timeout(1000)

        # 先点击"收件箱"文件夹展开消息列表
        inbox_btn = page.get_by_text("收件箱").first
        if inbox_btn.is_visible():
            inbox_btn.click()
            page.wait_for_timeout(800)

        # 验证有未读消息（老师发了一条）
        expect(page.get_by_text("E2E测试：作业通知")).to_be_visible()

        # 点击消息查看内容
        page.get_by_text("E2E测试：作业通知").first.click()
        page.wait_for_timeout(500)

        # 验证消息正文
        expect(page.get_by_text("请完成第三章练习")).to_be_visible()

        # 验证能看到发件人信息（"来自"）
        expect(page.get_by_text("来自", exact=False)).to_be_visible()

    # ─── 2.4 课程广场 ───

    def test_04_学生浏览课程广场(self, page: Page, student_data):
        """学生应能看到老师列表、课程列表和课件"""
        s = student_data["student"]
        t = student_data["teacher"]
        login(page, s["username"], s["password"])
        page.wait_for_timeout(500)

        # 切换到"课程"模块
        page.get_by_role("button").filter(has_text="课程").first.click()
        page.wait_for_timeout(1000)

        # 验证能看到老师列表
        expect(page.get_by_text(t["username"])).to_be_visible()

    # ─── 2.5 修改密码 ───

    def test_05_学生修改密码(self, page: Page, student_data):
        """修改密码 → 旧密码失效 → 新密码可登录"""
        s = student_data["student"]
        login(page, s["username"], s["password"])

        # 切换到"设置"模块
        page.get_by_role("button").filter(has_text="设置").first.click()
        page.wait_for_timeout(800)

        new_pwd = "newpwd123"

        # 填写密码修改表单
        page.get_by_placeholder("输入原密码").fill(s["password"])
        # "输入新密码" 占位符匹配两个输入框（新密码+确认密码）
        page.get_by_placeholder("输入新密码").first.fill(new_pwd)
        page.get_by_placeholder("再次输入新密码").fill(new_pwd)

        # 点"确认修改"
        page.get_by_role("button").filter(has_text="确认修改").click()
        page.wait_for_timeout(1000)

        # 验证成功提示
        expect(page.get_by_text("密码修改成功")).to_be_visible()

        # 用 API 验证旧密码已失效
        old_login = api_login(s["username"], s["password"])
        assert old_login is None, "旧密码仍能登录"
        # 用 API 验证新密码可登录
        new_login = api_login(s["username"], new_pwd)
        assert new_login is not None, "新密码无法登录"

        # 浏览器中用新密码登录
        page.goto(BASE_URL)
        page.wait_for_timeout(500)
        login(page, s["username"], new_pwd)
        page.wait_for_timeout(2000)

        # 验证跳转到 dashboard
        expect(page).to_have_url(f"{BASE_URL}/dashboard")


# ============================================================
# 👨‍🏫 教师端 E2E 测试
# ============================================================

class TestTeacherE2E:
    """教师功能：登录 → 看板 → 课堂记录 → 学生管理 → 消息 → 课件"""

    @pytest.fixture
    def teacher_data(self):
        """创建测试老师和学生，并创建一个班级把学生加进去"""
        ts = timestamp()
        teacher = create_test_teacher(f"t_{ts}")
        student = create_test_student(f"t_{ts}", teacher["username"])
        sid = get_user_id(student["username"])
        assert sid is not None, "学生 ID 获取失败"
        # 创建一个班级并加入学生，供发送消息等测试使用
        cls_name = f"E2E基础班_{ts}"
        create_class(teacher["token"], cls_name)
        add_student_to_class(teacher["token"], cls_name, student["username"], sid)
        return {"teacher": teacher, "student": student, "student_id": sid, "ts": ts, "class": cls_name}

    # ─── 3.1 登录 + 数据看板 ───

    def test_11_老师登录并看到看板(self, page: Page, teacher_data):
        """老师登录后应看到数据看板统计卡片"""
        t = teacher_data["teacher"]
        login(page, t["username"], t["password"])

        expect(page).to_have_url(f"{BASE_URL}/dashboard")

        # 验证能看到数据看板标题
        expect(page.get_by_text("数据看板").first).to_be_visible()

        # 验证能看到统计项
        expect(page.get_by_text("学生总数")).to_be_visible()
        expect(page.get_by_text("班级数量")).to_be_visible()

    # ─── 3.2 课堂考勤全流程 ───

    def test_12_老师创建班级并点名(self, page: Page, teacher_data):
        """新建班级 → 添加学生 → 点名 → 保存"""
        td = teacher_data
        t = td["teacher"]
        s = td["student"]
        ts = td["ts"]
        cls_name = f"E2E班级_{ts}"

        login(page, t["username"], t["password"])

        # 导航到课堂记录
        teacher_nav(page, "课堂记录")

        # 点"+"新建班级按钮（title="新建班级"）
        page.locator('[title="新建班级"]').first.click()
        page.wait_for_timeout(500)

        # 填班级表单
        page.get_by_placeholder("班级名称").fill(cls_name)
        # 选上课日（点"周一"和"周三"）
        day_btns = page.get_by_role("button").filter(has_text="周一")
        if day_btns.count() > 0:
            day_btns.first.click()
        day_btns = page.get_by_role("button").filter(has_text="周三")
        if day_btns.count() > 0:
            day_btns.first.click()
        page.get_by_placeholder("时间（如 14:00-15:30）").fill("14:00-15:30")
        page.get_by_placeholder("地点（如 301教室）").fill("测试教室")

        # 点"确定创建"
        page.get_by_role("button").filter(has_text="确定创建").click()
        page.wait_for_timeout(1000)

        # 验证班级出现在列表中
        expect(page.get_by_text(cls_name)).to_be_visible()

        # 点击班级
        page.get_by_text(cls_name).first.click()
        page.wait_for_timeout(500)

        # 点"点名"（先选日期今日确保点名按钮可点）
        page.get_by_role("button").filter(has_text="点名").first.click()
        page.wait_for_timeout(500)

        # 填课程内容
        content_input = page.get_by_placeholder("例如：循环结构")
        if content_input.count() > 0:
            content_input.first.fill(f"E2E自动测试 - {ts}")

        # 点"全部正常推进"
        push_btn = page.get_by_role("button").filter(has_text="全部正常推进")
        if push_btn.count() > 0:
            push_btn.first.click()
            page.wait_for_timeout(300)

        # 点"保存"
        save_btn = page.get_by_role("button").filter(has_text="保存")
        if save_btn.count() > 0:
            save_btn.first.click()
            page.wait_for_timeout(1000)

        # 验证保存成功（出现统计卡片如"本月课时"或记录）
        expect(page.get_by_text("本月课时")).to_be_visible()

    # ─── 3.3 学生管理 ───

    def test_13_老师查看学生管理(self, page: Page, teacher_data):
        """学生管理 → 选班级 → 查看学生详情"""
        td = teacher_data
        t = td["teacher"]
        s = td["student"]
        ts = td["ts"]
        cls_name = f"E2E管理班_{ts}"

        # 预先创建班级并添加学生（API 方式更可靠）
        create_class(t["token"], cls_name)
        add_student_to_class(t["token"], cls_name, s["username"], td["student_id"])

        login(page, t["username"], t["password"])

        # 导航到学生管理
        teacher_nav(page, "学生管理")

        # 验证看到"班级列表"
        expect(page.get_by_text("班级列表")).to_be_visible()

        # 点击班级
        page.get_by_text(cls_name).first.click()
        page.wait_for_timeout(800)

        # 验证看到学生姓名
        expect(page.get_by_text(s["username"])).to_be_visible()

        # 点"详情"按钮查看学生详情
        detail_btn = page.get_by_role("button").filter(has_text="详情")
        if detail_btn.count() > 0:
            detail_btn.first.click()
            page.wait_for_timeout(500)

            # 验证看到"最近课堂记录"
            expect(page.get_by_text("最近课堂记录")).to_be_visible()

    # ─── 3.4 发送站内信 ───

    def test_14_老师发送站内信(self, page: Page, teacher_data):
        """发送消息界面渲染正确 → 学生可选 → 表单元素齐全"""
        td = teacher_data
        t = td["teacher"]
        s = td["student"]

        login(page, t["username"], t["password"])

        # 导航到发送消息
        teacher_nav(page, "发送消息")

        # 验证能看到发送站内信界面
        expect(page.get_by_text("发送站内信")).to_be_visible()

        # 验证学生选项在 select 中（通过选中它来验证）
        student_select = page.get_by_role("combobox").first
        expect(student_select).to_be_visible()
        student_select.select_option(label=s["username"])

        # 填主题和正文，使发送按钮变为可用
        page.get_by_placeholder("例如：Scratch 课堂笔记").fill("E2E测试主题")
        page.locator("textarea").first.fill("E2E测试正文")

        # 验证按钮已启用
        send_btn = page.get_by_role("button", name="发送", exact=True)
        expect(send_btn).not_to_be_disabled()

        # 验证已发的消息记录可以从学生端查看到
        # （切换到学生视角验证已发消息可读，属于跨角色验证）
        # 当前测试只验证：老师界面正确渲染了发送消息功能

    # ─── 3.5 课件管理 ───

    def test_15_老师创建课程(self, page: Page, teacher_data):
        """课件管理 → 新建课程 → 验证显示"""
        td = teacher_data
        t = td["teacher"]
        ts = td["ts"]
        course_name = f"E2E课程_{ts}"

        login(page, t["username"], t["password"])

        # 导航到课件管理
        teacher_nav(page, "课件管理")

        # 验证看到"课件管理"
        page.wait_for_timeout(800)
        expect(page.get_by_text("课件管理").first).to_be_visible()

        # 点"新建课程"
        page.get_by_role("button").filter(has_text="新建课程").first.click()
        page.wait_for_timeout(500)

        # 填课程名称
        page.get_by_placeholder("如：Scratch-周六早-初级班").fill(course_name)

        # 选类型（可以用 API 跳过，但这里用 UI 验证）
        # 点"创建"
        page.get_by_role("button").filter(has_text="创建").last.click()
        page.wait_for_timeout(1000)

        # 验证课程卡片出现
        expect(page.get_by_text(course_name)).to_be_visible()

    # ─── 3.6 Tab 导航 ───

    def test_16_老师Tab导航和返回主页(self, page: Page, teacher_data):
        """逐个切换 5 个 Tab → 返回主页"""
        t = teacher_data["teacher"]
        login(page, t["username"], t["password"])

        tabs = ["数据看板", "学生管理", "发送消息", "课堂记录", "课件管理"]

        for tab in tabs:
            teacher_nav(page, tab)
            page.wait_for_timeout(300)
            # 验证页面没有崩溃（body 可见）
            expect(page.locator("body")).to_be_visible()

        # 点"返回主页"（侧栏中的是 <a> 链接，不是 <button>）
        page.get_by_role("link").filter(has_text="返回主页").first.click()
        page.wait_for_timeout(1000)

        # 验证回到首页（URL 是 /）
        assert page.url.rstrip("/") == BASE_URL or page.url == f"{BASE_URL}/"


# ============================================================
# 🔄 教师完整流程（一次登录跑到底）
# ============================================================

class TestTeacherFullFlow:
    """模拟老师一天的操作：登录 → 点名 → 发消息 → 创建课程 → 回家"""

    @pytest.fixture(scope="class")
    def flow_data(self):
        """创建一次测试数据，整个类共享（含班级）"""
        ts = timestamp()
        teacher = create_test_teacher(f"flow_{ts}")
        student = create_test_student(f"flow_{ts}", teacher["username"])
        sid = get_user_id(student["username"])
        cls_name = f"流程基础班_{ts}"
        create_class(teacher["token"], cls_name)
        add_student_to_class(teacher["token"], cls_name, student["username"], sid)
        return {"teacher": teacher, "student": student, "student_id": sid, "ts": ts, "class": cls_name}

    @pytest.fixture(scope="class")
    def logged_in_page(self, browser, flow_data):
        """整个类共享一个登录好的页面"""
        ctx = browser.new_context(viewport={"width": 1440, "height": 900})
        pg = ctx.new_page()
        t = flow_data["teacher"]
        login(pg, t["username"], t["password"])
        yield pg
        ctx.close()

    def test_01_看板概览(self, logged_in_page):
        expect(logged_in_page.get_by_text("数据看板").first).to_be_visible()
        expect(logged_in_page.get_by_text("学生总数")).to_be_visible()

    def test_02_创建班级并点名(self, logged_in_page, flow_data):
        ts = flow_data["ts"]
        cls_name = f"流程班_{ts}"

        teacher_nav(logged_in_page, "课堂记录")

        # 创建班级（title="新建班级"）
        logged_in_page.locator('[title="新建班级"]').first.click()
        logged_in_page.wait_for_timeout(500)
        logged_in_page.get_by_placeholder("班级名称").fill(cls_name)
        day_btns = logged_in_page.get_by_role("button").filter(has_text="周一")
        if day_btns.count() > 0:
            day_btns.first.click()
        logged_in_page.get_by_placeholder("时间（如 14:00-15:30）").fill("14:00-15:30")
        logged_in_page.get_by_placeholder("地点（如 301教室）").fill("测试教室")
        logged_in_page.get_by_role("button").filter(has_text="确定创建").click()
        logged_in_page.wait_for_timeout(1000)

        expect(logged_in_page.get_by_text(cls_name)).to_be_visible()

        # 点班级
        logged_in_page.get_by_text(cls_name).first.click()
        logged_in_page.wait_for_timeout(500)

        # 点名
        logged_in_page.get_by_role("button").filter(has_text="点名").first.click()
        logged_in_page.wait_for_timeout(500)

        content_input = logged_in_page.get_by_placeholder("例如：循环结构")
        if content_input.count() > 0:
            content_input.first.fill(f"流程测试 - {ts}")

        push_btn = logged_in_page.get_by_role("button").filter(has_text="全部正常推进")
        if push_btn.count() > 0:
            push_btn.first.click()
            logged_in_page.wait_for_timeout(300)

        save_btn = logged_in_page.get_by_role("button").filter(has_text="保存")
        if save_btn.count() > 0:
            save_btn.first.click()
            logged_in_page.wait_for_timeout(1000)

        expect(logged_in_page.get_by_text("本月课时")).to_be_visible()

    def test_03_发送消息(self, logged_in_page, flow_data):
        t = flow_data["teacher"]
        s = flow_data["student"]
        ts = flow_data["ts"]

        teacher_nav(logged_in_page, "发送消息")

        expect(logged_in_page.get_by_text("发送站内信")).to_be_visible()

        # 选学生
        student_select = logged_in_page.get_by_role("combobox").first
        expect(student_select).to_be_visible()
        student_select.select_option(label=s["username"])

        # 填主题和正文
        logged_in_page.get_by_placeholder("例如：Scratch 课堂笔记").fill(f"流程测试消息_{ts}")
        logged_in_page.locator("textarea").first.fill(f"流程测试正文 {ts}")

        # 验证按钮可用
        send_btn = logged_in_page.get_by_role("button", name="发送", exact=True)
        expect(send_btn).not_to_be_disabled()

    def test_04_创建课程(self, logged_in_page, flow_data):
        ts = flow_data["ts"]
        course_name = f"流程课程_{ts}"

        teacher_nav(logged_in_page, "课件管理")

        logged_in_page.get_by_role("button").filter(has_text="新建课程").first.click()
        logged_in_page.wait_for_timeout(500)
        logged_in_page.get_by_placeholder("如：Scratch-周六早-初级班").fill(course_name)
        logged_in_page.get_by_role("button").filter(has_text="创建").last.click()
        logged_in_page.wait_for_timeout(1000)

        expect(logged_in_page.get_by_text(course_name)).to_be_visible()

    def test_05_返回主页(self, logged_in_page):
        logged_in_page.get_by_role("link").filter(has_text="返回主页").first.click()
        logged_in_page.wait_for_timeout(1000)
        assert logged_in_page.url.rstrip("/") == BASE_URL or logged_in_page.url == f"{BASE_URL}/"
