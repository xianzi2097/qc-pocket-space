import os
import re
import json
import random
import shutil
from datetime import datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Depends, Request, UploadFile, File, Form
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, Integer, String, Text
from sqlalchemy.orm import declarative_base, sessionmaker, Session

# ==========================================
# 路径配置
# ==========================================
BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.abspath(os.path.join(BACKEND_DIR, "..", "..", "data", "qingcheng.db"))
SQLALCHEMY_DATABASE_URL = f"sqlite:///{DB_PATH}"

# 数据目录：website 外部的 data/，git 碰不到
USERS_DIR = os.path.abspath(os.path.join(BACKEND_DIR, "..", "..", "data", "users"))

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False, "timeout": 10})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ==========================================
# 数据库模型（三张表，零冗余）
# ==========================================
class UserModel(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)  # 登录+显示名
    password = Column(String)
    role = Column(String)                                # "admin" / "teacher" / "student"
    avatar_path = Column(String, default="avatar_01.jpg") # 头像文件名
    qc_descriptors = Column(Text, default="{}")          # JSON 万能口袋：班级、积分、宠物等

class TrashModel(Base):
    """垃圾桶：软删除的用户记录"""
    __tablename__ = "trash"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String)
    role = Column(String)
    original_path = Column(String)       # 原物理路径
    trash_folder = Column(String)        # 垃圾桶中的文件夹名
    db_snapshot = Column(Text)           # 用户 DB 记录的 JSON 快照
    trashed_at = Column(String)          # 删除时间戳

Base.metadata.create_all(bind=engine)

# ==========================================
# 路径常量
# ==========================================
TRASH_DIR = os.path.abspath(os.path.join(BACKEND_DIR, "..", "..", "data", "trash"))

# ==========================================
# 工具函数
# ==========================================
def get_user_dir(user: UserModel) -> str:
    return os.path.join(USERS_DIR, user.role + "s", str(user.id))

def get_user_dir_static(username: str, role: str) -> str:
    return os.path.join(USERS_DIR, role + "s", username)

def write_info_json(user: UserModel):
    """
    将用户信息写入文件夹内的 info.json，实现「文件夹即身份」。
    即使数据库丢失，info.json 也能完整描述文件夹主人。
    """
    user_dir = get_user_dir(user)
    os.makedirs(user_dir, exist_ok=True)
    try:
        descriptors = json.loads(user.qc_descriptors or "{}")
    except json.JSONDecodeError:
        descriptors = {}

    info = {
        "username": user.username,
        "role": user.role,
        "avatar_path": user.avatar_path,
        "descriptors": descriptors,
        "created": getattr(user, "_created_at", ""),
        "subfolders": ["sb3_projects", "notes"] if user.role == "student" else [],
    }
    info_path = os.path.join(user_dir, "info.json")
    with open(info_path, "w", encoding="utf-8") as f:
        json.dump(info, f, ensure_ascii=False, indent=2)

def ensure_user_dirs(user: UserModel):
    """为新用户创建标准目录 + info.json"""
    user_dir = get_user_dir(user)
    os.makedirs(user_dir, exist_ok=True)
    if user.role == "student":
        for sub in ["sb3_projects", "notes", "messages"]:
            os.makedirs(os.path.join(user_dir, sub), exist_ok=True)
            os.makedirs(os.path.join(user_dir, "messages", "收件箱"), exist_ok=True)
    elif user.role == "teacher":
        # 不写空 classes.json，由 ensure_admin_and_test_accounts 或 API 创建
        pass
    write_info_json(user)

def move_to_trash(user: UserModel, db: Session) -> dict:
    """
    软删除：确保 info.json 最新 → 整个文件夹移入 trash/。
    info.json 本身就是完整快照，不需要额外 db_record.json。
    """
    from datetime import datetime
    src = os.path.normpath(get_user_dir(user))
    root = os.path.normpath(USERS_DIR)
    if os.path.commonpath([root, src]) != root:
        raise HTTPException(status_code=400, detail="路径不安全")

    # 确保 info.json 是最新的
    write_info_json(user)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    trash_folder_name = f"{user.role}_{user.username}_{timestamp}"
    dst = os.path.join(TRASH_DIR, trash_folder_name)

    os.makedirs(TRASH_DIR, exist_ok=True)

    if os.path.exists(src):
        shutil.move(src, dst)
        print(f"[垃圾桶] {src} → {dst}")
    else:
        os.makedirs(dst, exist_ok=True)

    # DB 快照（冗余备份，info.json 已足够）
    db_snapshot = {
        "username": user.username,
        "password": user.password, "role": user.role,
        "avatar_path": user.avatar_path, "qc_descriptors": user.qc_descriptors,
    }

    trash = TrashModel(
        username=user.username, role=user.role,
        original_path=src, trash_folder=trash_folder_name,
        db_snapshot=json.dumps(db_snapshot, ensure_ascii=False),
        trashed_at=timestamp
    )
    db.add(trash)

    db.delete(user)
    db.commit()

    return {
        "id": trash.id, "username": trash.username, "role": trash.role,
        "trash_folder": trash.trash_folder, "trashed_at": trash.trashed_at
    }

def restore_from_trash(trash_id: int, db: Session) -> dict:
    """
    从垃圾桶恢复：优先读 info.json，降级用 DB 快照。
    文件夹移回原位置，DB 重建。
    """
    trash = db.query(TrashModel).filter(TrashModel.id == trash_id).first()
    if not trash:
        raise HTTPException(status_code=404, detail="垃圾桶中未找到该记录")

    trash_path = os.path.join(TRASH_DIR, trash.trash_folder)

    # 优先从 info.json 读取
    info_path = os.path.join(trash_path, "info.json")
    if os.path.exists(info_path):
        with open(info_path, "r", encoding="utf-8") as f:
            info = json.load(f)
        username = info["username"]
        role = info["role"]
        avatar_path = info["avatar_path"]
        qc_descriptors = json.dumps(info.get("descriptors", {}), ensure_ascii=False)
        # 密码 info.json 不存，从 DB 快照补
        db_snap = json.loads(trash.db_snapshot or "{}")
        password = db_snap.get("password", "111111")
    else:
        # 降级：纯 DB 快照恢复
        db_snap = json.loads(trash.db_snapshot or "{}")
        username = db_snap["username"]
        role = db_snap["role"]
        avatar_path = db_snap["avatar_path"]
        qc_descriptors = db_snap.get("qc_descriptors", "{}")
        password = db_snap.get("password", "111111")

    if db.query(UserModel).filter(UserModel.username == username).first():
        raise HTTPException(status_code=400, detail=f"用户 [{username}] 已存在，无法恢复")

    user = UserModel(
        username=username, password=password,
        role=role, avatar_path=avatar_path, qc_descriptors=qc_descriptors
    )
    db.add(user)

    target_dir = get_user_dir_static(username, role)
    if os.path.exists(trash_path):
        os.makedirs(os.path.dirname(target_dir), exist_ok=True)
        shutil.move(trash_path, target_dir)
        print(f"[恢复] {trash_path} → {target_dir}")

    db.delete(trash)
    db.commit()
    return {"status": "success", "message": f"用户 [{username}] 已恢复！", "username": username, "role": role}

# ==========================================
# 测试消息辅助函数（文件系统）
# ==========================================
MESSAGES_DIR_NAME = "messages"  # 学生个人空间里的消息文件夹名

def _get_msgs_dir(username: str) -> str:
    """学生消息根目录：data/users/students/{username}/messages/"""
    return os.path.join(USERS_DIR, "students", username, MESSAGES_DIR_NAME)

def _write_msg(username: str, folder: str, msg_id: str, sender: str, subject: str, body: str):
    """写一条消息到文件"""
    folder_dir = os.path.join(_get_msgs_dir(username), folder)
    os.makedirs(folder_dir, exist_ok=True)
    msg = {
        "id": msg_id,
        "sender": sender,
        "subject": subject,
        "body": body,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "is_read": False
    }
    fp = os.path.join(folder_dir, f"{msg_id}.json")
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(msg, f, ensure_ascii=False, indent=2)

def _send_test_messages(student_username: str):
    """给学生发测试消息（文件系统）"""
    folder = "收件箱"
    os.makedirs(os.path.join(_get_msgs_dir(student_username), folder), exist_ok=True)
    _write_msg(student_username, folder, "welcome_1", "t",
        "欢迎来到青橙 Studio 🍊",
        "你好！欢迎加入青橙 Studio。\n\n"
        "这里是你和老师之间的消息中心，老师会在这里发送：\n"
        "- 📝 **课堂笔记**\n"
        "- 💡 **代码示例**\n"
        "- 🎯 **课后任务**\n\n"
        "祝你学习愉快！")
    _write_msg(student_username, folder, "scratch_tip", "t",
        "Scratch 小技巧：克隆的使用",
        "今天我们来学习**克隆**。\n\n"
        "```scratch\n"
        "当绿旗被点击\n"
        "重复执行 10 次\n"
        "  克隆 自己\n"
        "```\n\n"
        "克隆体可以有自己的变量，试试看！")

# ==========================================
# 启动：建表 + 确保管理员 + 注入测试账号
# ==========================================

def _ensure_user(db: Session, username: str, role: str,
                 avatar: str = "default.jpg", descriptors: dict = None) -> UserModel:
    """创建用户（DB + 文件夹），如已存在则跳过"""
    user = db.query(UserModel).filter(UserModel.username == username).first()
    if not user:
        user = UserModel(
            username=username, password="111111",
            role=role, avatar_path=avatar,
            qc_descriptors=json.dumps(descriptors or {}, ensure_ascii=False)
        )
        db.add(user)
        db.commit()
        ensure_user_dirs(user)
    return user

def _write_note_file(username: str, notebook: str, filename: str, content: str):
    """写入一篇 .md 笔记（username → 查 id → 拼路径）"""
    db = SessionLocal()
    try:
        user = db.query(UserModel).filter(UserModel.username == username).first()
        if not user:
            return
        user_dir = get_user_dir(user)
    finally:
        db.close()
    nb_dir = os.path.join(user_dir, "notes", notebook)
    os.makedirs(nb_dir, exist_ok=True)
    fp = os.path.join(nb_dir, filename)
    if not os.path.exists(fp):
        with open(fp, "w", encoding="utf-8") as f:
            f.write(content)

def _create_student_notebooks(username: str):
    """为一个学生创建 3 个笔记本及其测试笔记"""
    notebooks = {
        "python": [
            ("01.变量与类型.md",
             "# 变量与类型\n\n"
             "Python 中的变量就像一个**标签**，贴在数据上。\n\n"
             "```python\n"
             "name = \"小明\"      # 字符串 str\n"
             "age = 10            # 整数 int\n"
             "score = 95.5        # 浮点数 float\n"
             "is_cool = True      # 布尔 bool\n"
             "```\n\n"
             "## 试试看\n"
             "用 `type()` 函数可以查看变量的类型：\n"
             "```python\n"
             "print(type(name))   # <class 'str'>\n"
             "print(type(age))    # <class 'int'>\n"
             "```\n"),
            ("02.条件判断.md",
             "# 条件判断 if/else\n\n"
             "让程序根据不同情况做不同的事。\n\n"
             "```python\n"
             "score = 85\n"
             "if score >= 90:\n"
             "    print(\"优秀！\")\n"
             "elif score >= 60:\n"
             "    print(\"及格\")\n"
             "else:\n"
             "    print(\"加油！\")\n"
             "```\n\n"
             "## 练习\n"
             "写一个判断奇偶数的程序：\n"
             "```python\n"
             "n = int(input(\"输入一个数: \"))\n"
             "if n % 2 == 0:\n"
             "    print(\"偶数\")\n"
             "else:\n"
             "    print(\"奇数\")\n"
             "```\n"),
        ],
        "scratch": [
            ("01.小猫散步.md",
             "# 小猫散步\n\n"
             "让 Scratch 小猫动起来！\n\n"
             "## 积木\n"
             "- 🟢 **当绿旗被点击** — 开始\n"
             "- 🔵 **移动 10 步** — 让角色移动\n"
             "- 🟣 **碰到边缘就反弹** — 不跑出屏幕\n\n"
             "```scratch\n"
             "当 🟢 被点击\n"
             "重复执行\n"
             "  移动 (10) 步\n"
             "  碰到边缘就反弹\n"
             "```\n\n"
             "## 挑战\n"
             "1. 改变步数看看小猫跑多快\n"
             "2. 加上「下一个造型」让小猫走路\n"),
            ("02.克隆魔法.md",
             "# 克隆魔法\n\n"
             "克隆 = 复制角色，做弹幕、下雨等效果的神器。\n\n"
             "```scratch\n"
             "当 🟢 被点击\n"
             "重复执行 (10) 次\n"
             "  克隆 [自己]\n"
             "  等待 (0.5) 秒\n"
             "```\n\n"
             "## 克隆体启动时\n"
             "```scratch\n"
             "当作为克隆体启动时\n"
             "  移到 [随机位置]\n"
             "  重复执行 (50) 次\n"
             "    将 y 增加 (-2)\n"
             "  删除此克隆体\n"
             "```\n"),
        ],
        "cpp": [
            ("01.你好世界.md",
             "# 第一个 C++ 程序\n\n"
             "```cpp\n"
             "#include <iostream>\n"
             "using namespace std;\n\n"
             "int main() {\n"
             "    cout << \"Hello, World!\" << endl;\n"
             "    return 0;\n"
             "}\n"
             "```\n\n"
             "## 拆解\n"
             "- `#include <iostream>` — 引入输入输出库\n"
             "- `int main()` — 程序入口\n"
             "- `cout <<` — 输出到屏幕\n"
             "- `return 0` — 告诉系统程序正常结束\n"),
            ("02.循环.md",
             "# for 循环\n\n"
             "重复做一件事，不用写 N 遍代码。\n\n"
             "```cpp\n"
             "for (int i = 1; i <= 10; i++) {\n"
             "    cout << i << \" \";\n"
             "}\n"
             "// 输出: 1 2 3 4 5 6 7 8 9 10\n"
             "```\n\n"
             "## 三个部分\n"
             "1. `int i = 1` — 起点\n"
             "2. `i <= 10` — 条件（满足就继续）\n"
             "3. `i++` — 每次加 1\n"),
        ],
    }

    for nb_name, notes in notebooks.items():
        for filename, content in notes:
            _write_note_file(username, nb_name, filename, content)

def _create_teacher_classes(teacher_username: str, classes_data: list[dict]):
    """为老师创建班级和班级学生（支持关联账号）"""
    existing = _load_classes(teacher_username)
    if existing:  # 已有班级数据不覆盖
        return

    _save_classes(teacher_username, classes_data)
    for c in classes_data:
        os.makedirs(os.path.join(USERS_DIR, "teachers", teacher_username, "attendance", c["name"]), exist_ok=True)

def _send_teacher_welcome(teacher_username: str, student_usernames: list[str]):
    """老师给学生们发欢迎消息"""
    db = SessionLocal()
    try:
        for stu in student_usernames:
            user = db.query(UserModel).filter(UserModel.username == stu).first()
            if not user:
                continue
            user_dir = get_user_dir(user)
            msg_dir = os.path.join(user_dir, "messages", "收件箱")
            os.makedirs(msg_dir, exist_ok=True)
            msg_id = f"welcome_from_{teacher_username}"
            fp = os.path.join(msg_dir, f"{msg_id}.json")
            if os.path.exists(fp):
                continue
            msg = {
                "id": msg_id,
                "sender": teacher_username,
                "recipient": stu,
                "folder": "收件箱",
                "subject": f"欢迎！我是{teacher_username}老师",
                "body": f"你好！我是{teacher_username}老师。\n\n"
                        f"很高兴和你一起学习编程！\n\n"
                        f"## 课前准备\n"
                        f"- 📝 准备好笔记本\n"
                        f"- 💻 打开 Scratch/Python\n"
                        f"- 🧠 带上好奇心\n\n"
                        f"有问题随时发消息给我哦～",
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "is_read": False,
            }
            with open(fp, "w", encoding="utf-8") as f:
                json.dump(msg, f, ensure_ascii=False, indent=2)
    finally:
        db.close()

def _make_student_username(teacher: str, class_name: str, student_name: str) -> str:
    """
    为班级学生生成唯一系统账号。
    格式：{老师}{班级缩写}{学生名缩写}
    有重名时自动追加数字后缀。
    """
    def slug(s: str) -> str:
        s = re.sub(r'[\s/\\()（）]', '', s)
        return s[:10]

    base = f"{slug(teacher)}_{slug(class_name)}_{slug(student_name)}"
    base = base.strip('_')

    db = SessionLocal()
    try:
        candidate = base
        suffix = 1
        while db.query(UserModel).filter(UserModel.username == candidate).first():
            suffix += 1
            candidate = f"{base}{suffix}"
        return candidate
    finally:
        db.close()

def ensure_admin_and_test_accounts():
    db = SessionLocal()
    try:
        for folder in ["admins", "teachers", "students"]:
            os.makedirs(os.path.join(USERS_DIR, folder), exist_ok=True)
        os.makedirs(TRASH_DIR, exist_ok=True)

        # ── 管理员 ──
        _ensure_user(db, "admin", "admin", "default.jpg")

        # ── 测试老师 ──
        _ensure_user(db, "林老师", "teacher", "avatar_01.jpg",
                     {"title": "Scratch 主讲"})
        _ensure_user(db, "程老师", "teacher", "avatar_02.jpg",
                     {"title": "Python 讲师"})
        _ensure_user(db, "黄老师", "teacher", "avatar_03.jpg",
                     {"title": "C++ 教练"})

        teacher_ids = {"林老师": None, "程老师": None, "黄老师": None}
        for u in db.query(UserModel).filter(UserModel.role == "teacher").all():
            if u.username in teacher_ids:
                teacher_ids[u.username] = u.id

        # ── 50 个真实学生姓名 ──
        student_names = [
            "张子轩", "李雨桐", "王浩宇", "刘思琪", "陈明宇",
            "杨紫涵", "赵子豪", "黄梓萱", "周俊杰", "吴欣怡",
            "徐天佑", "孙语彤", "马浩然", "朱梓琳", "胡俊哲",
            "郭雨萱", "林俊豪", "何思颖", "高浩然", "罗梓涵",
            "梁俊熙", "宋雨桐", "唐子轩", "韩欣怡", "曹浩宇",
            "许梓萱", "邓俊杰", "冯雨彤", "彭浩然", "蒋思琪",
            "蔡俊豪", "潘梓琳", "袁浩宇", "翁雨萱", "方俊哲",
            "沈欣怡", "任浩然", "姚梓涵", "卢俊熙", "崔雨桐",
            "谭子轩", "汪欣怡", "范浩宇", "钟梓萱", "廖俊杰",
            "熊雨彤", "金浩然", "欧阳思琪", "夏侯俊豪", "慕容梓琳",
        ]

        # ── 班级配置 ──
        class_config = {
            "林老师": {
                "classes": ["Scratch启蒙班", "Scratch初级班", "Scratch中级班", "Python基础班", "Python进阶班"],
                "student_count": 35,
            },
            "程老师": {
                "classes": ["Python入门班", "算法提高班"],
                "student_count": 8,
            },
            "黄老师": {
                "classes": ["C++基础班", "C++竞赛班", "机器人编程"],
                "student_count": 7,
            },
        }

        name_idx = 0
        all_students = {}
        for teacher, cfg in class_config.items():
            tid = teacher_ids[teacher]
            t_dir = os.path.join(USERS_DIR, "teachers", str(tid))
            classes_path = os.path.join(t_dir, "classes.json")
            if os.path.exists(classes_path):
                continue

            classes_list = []
            per_class = cfg["student_count"] // len(cfg["classes"])
            extra = cfg["student_count"] % len(cfg["classes"])

            for ci, cls_name in enumerate(cfg["classes"]):
                count = per_class + (1 if ci < extra else 0)
                students_list = []
                for _ in range(count):
                    stu_name = student_names[name_idx]
                    name_idx += 1
                    new_user = _ensure_user(
                        db, stu_name, "student", "default.jpg",
                        {"teacher": teacher}
                    )
                    students_list.append({
                        "user_id": new_user.id,
                        "name": stu_name,
                    })
                    all_students.setdefault(teacher, []).append(stu_name)
                classes_list.append({
                    "name": cls_name,
                    "students": students_list,
                    "schedule": [],
                })

            os.makedirs(t_dir, exist_ok=True)
            with open(classes_path, "w", encoding="utf-8") as f:
                json.dump(classes_list, f, ensure_ascii=False, indent=2)

        # ── 发欢迎消息 ──
        for teacher, stus in all_students.items():
            _send_teacher_welcome(teacher, stus)

        print("[+] 测试账号就绪:")
        print("    管理员: admin / 111111")
        print("    老师:   林老师 / 程老师 / 黄老师  (密码 111111)")
        for teacher, cfg in class_config.items():
            for cls_name in cfg["classes"]:
                per = cfg["student_count"] // len(cfg["classes"])
                print(f"    {teacher}: {cls_name}({per}人)")
        print(f"    共 {name_idx} 名学生")
    finally:
        db.close()

def check_orphan_folders():
    """检查 users 文件夹中是否存在 DB 里没有的孤儿目录，提示用户处理"""
    db = SessionLocal()
    try:
        # 收集 DB 中所有用户 id
        db_ids = set()
        for row in db.query(UserModel.id).all():
            db_ids.add(str(row[0]))

        orphans = []
        for role_dir in ["admins", "teachers", "students"]:
            role_path = os.path.join(USERS_DIR, role_dir)
            if not os.path.exists(role_path):
                continue
            for name in os.listdir(role_path):
                p = os.path.join(role_path, name)
                if os.path.isdir(p) and not name.startswith("."):
                    if name not in db_ids:
                        orphans.append((role_dir, name, p))

        if not orphans:
            return

        print("\n" + "=" * 50)
        print("⚠️  发现孤儿文件夹（DB 中无对应记录）：")
        for role_dir, name, path in orphans:
            print(f"  📁 {role_dir}/{name}  →  {path}")
        print("=" * 50)

        try:
            ans = input("是否删除这些孤儿文件夹？[y/N] ").strip().lower()
            if ans == 'y':
                for _, _, path in orphans:
                    shutil.rmtree(path)
                    print(f"  🗑️  已删除: {path}")
                print("[+] 孤儿文件夹清理完成")
            else:
                print("[i] 跳过，保留孤儿文件夹")
        except EOFError:
            # 非交互模式（如后台启动），跳过
            print("[i] 非交互模式，保留孤儿文件夹（可手动删除）")
    finally:
        db.close()

@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_admin_and_test_accounts()
    check_orphan_folders()
    yield

app = FastAPI(lifespan=lifespan)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    print(f"!!! 报错详情: {exc.errors()}")
    return JSONResponse(status_code=422, content={"detail": exc.errors()})

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ==========================================
# Pydantic 表单
# ==========================================
class RegisterForm(BaseModel):
    username: str
    password: str
    role: str

class LoginForm(BaseModel):
    username: str
    password: str

class AvatarUpdateForm(BaseModel):
    username: str
    avatar_path: str  # 预设头像: "avatar_01.jpg" 或自定义: "custom"

class DescriptorsUpdateForm(BaseModel):
    username: str
    qc_descriptors: str  # JSON 字符串

class SendMessageForm(BaseModel):
    sender: str         # 发件人
    recipient: str      # 收件人
    folder: str = "收件箱"  # 消息文件夹
    subject: str        # 主题
    body: str           # Markdown 正文

# ==========================================
# 注册接口
# ==========================================
@app.post("/api/register")
def register_user(form: RegisterForm, db: Session = Depends(get_db)):
    if form.role not in ("teacher", "student"):
        raise HTTPException(status_code=400, detail="角色只能是 teacher 或 student")
    if db.query(UserModel).filter(UserModel.username == form.username).first():
        raise HTTPException(status_code=400, detail="该账号已存在！")

    # 创建物理文件夹
    user_dir = get_user_dir_static(form.username, form.role)
    os.makedirs(user_dir, exist_ok=True)

    # 随机默认头像
    avatar_name = f"avatar_{str(random.randint(1, 78)).zfill(2)}.jpg"

    new_user = UserModel(
        username=form.username,
        password=form.password,
        role=form.role,
        avatar_path=avatar_name,
        qc_descriptors="{}"
    )
    db.add(new_user)
    db.commit()
    ensure_user_dirs(new_user)

    return {"status": "success", "message": f"账号 [{form.username}] 创建成功！"}

# ==========================================
# 登录接口
# ==========================================
@app.post("/api/login")
def login_user(form: LoginForm, db: Session = Depends(get_db)):
    user = db.query(UserModel).filter(UserModel.username == form.username).first()
    if user and user.password == form.password:
        avatar_path = user.avatar_path or "default.jpg"
        if avatar_path.endswith(".png"):
            avatar_path = avatar_path[:-4] + ".jpg"
        # 解析 qc_descriptors
        try:
            descriptors = json.loads(user.qc_descriptors or "{}")
        except json.JSONDecodeError:
            descriptors = {}
        return {
            "status": "success",
            "message": "登录成功！",
            "user": {
                "username": user.username,
                "role": user.role,
                "avatar_path": avatar_path,
                "descriptors": descriptors
            }
        }
    raise HTTPException(status_code=401, detail="账号或密码错误")

# ==========================================
# 头像接口
# ==========================================
@app.post("/api/update-avatar")
def update_avatar(form: AvatarUpdateForm, db: Session = Depends(get_db)):
    user = db.query(UserModel).filter(UserModel.username == form.username).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")

    # 预设头像：avatar_01~78.jpg；自定义：custom；数字 ID：avatar_0~11
    if form.avatar_path == "custom":
        user.avatar_path = "custom"
    elif re.fullmatch(r"avatar_(0[1-9]|[1-6][0-9]|7[0-8])\.jpg", form.avatar_path):
        user.avatar_path = form.avatar_path
    elif re.fullmatch(r"avatar_\d+", form.avatar_path):
        user.avatar_path = form.avatar_path
    else:
        raise HTTPException(status_code=400, detail="头像文件名不合法")

    db.commit()
    return {
        "status": "success",
        "message": "头像更新成功！",
        "avatar_path": user.avatar_path
    }

@app.post("/api/upload-avatar")
async def upload_avatar(username: str, file: UploadFile = File(...), db: Session = Depends(get_db)):
    """学生上传自定义头像，存到 students/{username}/avatar.jpg"""
    user = db.query(UserModel).filter(UserModel.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.role != "student":
        raise HTTPException(status_code=400, detail="仅学生可上传自定义头像")

    student_dir = get_user_dir(user)
    os.makedirs(student_dir, exist_ok=True)
    avatar_path = os.path.join(student_dir, "avatar.jpg")

    contents = await file.read()
    if len(contents) > 2 * 1024 * 1024:  # 2MB 限制
        raise HTTPException(status_code=400, detail="头像文件不能超过 2MB")

    with open(avatar_path, "wb") as f:
        f.write(contents)

    user.avatar_path = "custom"
    db.commit()

    return {"status": "success", "message": "头像上传成功！"}

# ==========================================
# qc_descriptors 接口（万能口袋）
# ==========================================
@app.post("/api/update-descriptors")
def update_descriptors(form: DescriptorsUpdateForm, db: Session = Depends(get_db)):
    """更新用户的 qc_descriptors JSON。前端负责整个 JSON 的读写。"""
    user = db.query(UserModel).filter(UserModel.username == form.username).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    # 验证是合法 JSON
    try:
        json.loads(form.qc_descriptors)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="qc_descriptors 不是合法 JSON")
    user.qc_descriptors = form.qc_descriptors
    db.commit()
    return {"status": "success", "message": "描述符更新成功！"}

# ==========================================
# 用户管理接口
# ==========================================
@app.get("/api/users")
def list_all_users(db: Session = Depends(get_db)):
    """列出所有用户（管理员用）"""
    users = db.query(UserModel).all()
    return {
        "status": "success",
        "users": [{
            "id": u.id, "username": u.username,
            "role": u.role, "avatar_path": u.avatar_path
        } for u in users]
    }

# ==========================================
# 软删除用户（移入垃圾桶）
# ==========================================
@app.delete("/api/users/{username}")
def delete_user(username: str, db: Session = Depends(get_db)):
    """软删除：移至垃圾桶，admin 可恢复或彻底删除"""
    user = db.query(UserModel).filter(UserModel.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    if user.role == "admin":
        raise HTTPException(status_code=403, detail="不能删除管理员")

    trash_info = move_to_trash(user, db)
    return {
        "status": "success",
        "message": f"用户 [{username}] 已移入垃圾桶",
        "trash": trash_info
    }

# ==========================================
# 垃圾桶管理（仅 admin）
# ==========================================
def require_admin(username: str, db: Session) -> UserModel:
    caller = db.query(UserModel).filter(UserModel.username == username).first()
    if not caller or caller.role != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可操作")
    return caller

@app.get("/api/trash")
def list_trash(username: str = "", db: Session = Depends(get_db)):
    """列出垃圾桶中所有记录"""
    require_admin(username, db)
    items = db.query(TrashModel).order_by(TrashModel.id.desc()).all()
    return {
        "status": "success",
        "trash": [{
            "id": t.id, "username": t.username, "role": t.role,
            "trash_folder": t.trash_folder, "trashed_at": t.trashed_at
        } for t in items]
    }

@app.post("/api/trash/restore/{trash_id}")
def restore_trash_item(trash_id: int, username: str = "", db: Session = Depends(get_db)):
    """从垃圾桶恢复用户"""
    require_admin(username, db)
    return restore_from_trash(trash_id, db)

@app.delete("/api/trash/{trash_id}")
def permanent_delete(trash_id: int, username: str = "", db: Session = Depends(get_db)):
    """彻底删除垃圾桶中的一条记录（删物理文件夹）"""
    require_admin(username, db)
    trash = db.query(TrashModel).filter(TrashModel.id == trash_id).first()
    if not trash:
        raise HTTPException(status_code=404, detail="垃圾桶中未找到该记录")

    trash_path = os.path.join(TRASH_DIR, trash.trash_folder)
    if os.path.exists(trash_path):
        shutil.rmtree(trash_path)
        print(f"[-] 彻底删除: {trash_path}")

    db.delete(trash)
    db.commit()
    return {"status": "success", "message": f"已彻底删除 [{trash.username}]"}

@app.delete("/api/trash")
def empty_trash(username: str = "", db: Session = Depends(get_db)):
    """清空整个垃圾桶"""
    require_admin(username, db)
    items = db.query(TrashModel).all()
    for t in items:
        tp = os.path.join(TRASH_DIR, t.trash_folder)
        if os.path.exists(tp):
            shutil.rmtree(tp)
        db.delete(t)
    db.commit()
    print("[-] 垃圾桶已清空")
    return {"status": "success", "message": f"已清空 {len(items)} 条垃圾桶记录"}

# ==========================================
# 用户资料接口（含 qc_descriptors）
# ==========================================
@app.get("/api/user/profile")
def user_profile(username: str = "", db: Session = Depends(get_db)):
    """返回用户完整信息，包括 qc_descriptors 解析后的对象"""
    user = db.query(UserModel).filter(UserModel.username == username).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    try:
        descriptors = json.loads(user.qc_descriptors or "{}")
    except json.JSONDecodeError:
        descriptors = {}
    return {
        "status": "success",
        "user": {
            "username": user.username,
            "role": user.role,
            "avatar_path": user.avatar_path,
            "descriptors": descriptors
        }
    }

# ==========================================
# 站内信接口（文件系统，和笔记本一致的存储模式）
# 路径：students/{username}/messages/{folder}/{msg_id}.json
# ==========================================

@app.post("/api/messages/send")
def send_message(form: SendMessageForm, db: Session = Depends(get_db)):
    """发送站内信（老师→学生），存为 JSON 文件"""
    # 检查收件人是否存在
    recipient = db.query(UserModel).filter(
        UserModel.username == form.recipient, UserModel.role == "student"
    ).first()
    if not recipient:
        raise HTTPException(status_code=404, detail=f"学生 [{form.recipient}] 不存在，请检查账号名")
    folder = form.folder or "收件箱"
    msg_id = f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{form.sender}"
    _write_msg(form.recipient, folder, msg_id, form.sender, form.subject, form.body)
    return {"status": "success", "message": "消息发送成功！", "id": msg_id}

@app.get("/api/messages/folders")
def list_folders(username: str = ""):
    """列出消息文件夹及未读数"""
    base = _get_msgs_dir(username)
    if not os.path.exists(base):
        return {"status": "success", "folders": [{"name": "收件箱", "count": 0, "unread": 0}], "total_unread": 0}
    folders = []
    total_unread = 0
    for folder_name in sorted(os.listdir(base)):
        fp = os.path.join(base, folder_name)
        if not os.path.isdir(fp) or folder_name.startswith("."):
            continue
        files = [f for f in os.listdir(fp) if f.endswith(".json")]
        unread = 0
        for f in files:
            try:
                with open(os.path.join(fp, f), "r", encoding="utf-8") as fh:
                    data = json.load(fh)
                    if not data.get("is_read", True):
                        unread += 1
            except (json.JSONDecodeError, OSError):
                pass
        folders.append({"name": folder_name, "count": len(files), "unread": unread})
        total_unread += unread
    if not any(f["name"] == "收件箱" for f in folders):
        folders.insert(0, {"name": "收件箱", "count": 0, "unread": 0})
    return {"status": "success", "folders": folders, "total_unread": total_unread}

@app.get("/api/messages/inbox")
def inbox(username: str = "", folder: str = ""):
    """收件箱：列出某文件夹下的所有消息"""
    dir_path = os.path.join(_get_msgs_dir(username), folder)
    if not os.path.exists(dir_path):
        return {"status": "success", "messages": []}
    msgs = []
    for fname in sorted(os.listdir(dir_path), reverse=True):
        if not fname.endswith(".json"):
            continue
        fp = os.path.join(dir_path, fname)
        try:
            with open(fp, "r", encoding="utf-8") as f:
                data = json.load(f)
                if data.get("subject"):  # 过滤空消息
                    msgs.append(data)
        except (json.JSONDecodeError, OSError):
            pass
    return {"status": "success", "messages": msgs}

@app.get("/api/messages/sent")
def sent_messages(username: str = "", db: Session = Depends(get_db)):
    """发件箱：扫描所有学生文件夹，找出某人发的消息"""
    students_dir = os.path.join(USERS_DIR, "students")
    if not os.path.exists(students_dir):
        return {"status": "success", "messages": []}
    results = []
    for student_name in os.listdir(students_dir):
        msgs_base = _get_msgs_dir(student_name)
        if not os.path.exists(msgs_base):
            continue
        for folder_name in os.listdir(msgs_base):
            fp = os.path.join(msgs_base, folder_name)
            if not os.path.isdir(fp):
                continue
            for fname in os.listdir(fp):
                if not fname.endswith(".json"):
                    continue
                try:
                    with open(os.path.join(fp, fname), "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if data.get("sender") == username and data.get("subject"):
                            data["recipient"] = student_name
                            data["folder"] = folder_name
                            results.append(data)
                except (json.JSONDecodeError, OSError):
                    pass
    results.sort(key=lambda x: x.get("id", ""), reverse=True)
    return {"status": "success", "messages": results}

@app.post("/api/messages/{msg_id}/read")
def mark_read(msg_id: str, username: str = "", folder: str = ""):
    """标记消息为已读"""
    fp = _find_msg_file(username, folder, msg_id)
    if not fp:
        raise HTTPException(status_code=404, detail="消息不存在")
    with open(fp, "r", encoding="utf-8") as f:
        data = json.load(f)
    data["is_read"] = True
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    return {"status": "success"}

@app.post("/api/messages/folders")
def create_folder(username: str = "", folder: str = ""):
    """创建新文件夹"""
    if not folder or folder == "收件箱":
        raise HTTPException(status_code=400, detail="文件夹名无效")
    dir_path = os.path.join(_get_msgs_dir(username), folder)
    if os.path.exists(dir_path):
        raise HTTPException(status_code=400, detail="文件夹已存在")
    os.makedirs(dir_path, exist_ok=True)
    return {"status": "success", "message": f"文件夹 [{folder}] 创建成功！"}

@app.delete("/api/messages/folders/{folder_name:path}")
def delete_folder(folder_name: str, username: str = ""):
    """删除文件夹"""
    if folder_name == "收件箱":
        raise HTTPException(status_code=400, detail="不能删除收件箱")
    path = _safe_path(_get_msgs_dir(username), folder_name)
    if os.path.exists(path):
        shutil.rmtree(path)
    return {"status": "success", "message": f"文件夹 [{folder_name}] 已删除"}

@app.put("/api/messages/folders/{folder_name:path}")
def rename_folder(folder_name: str, username: str = "", new_name: str = ""):
    """重命名文件夹"""
    if folder_name == "收件箱":
        raise HTTPException(status_code=400, detail="不能重命名收件箱")
    if not new_name or new_name == folder_name:
        raise HTTPException(status_code=400, detail="新名称无效")
    base = _get_msgs_dir(username)
    old_path = _safe_path(base, folder_name)
    new_path = _safe_path(base, new_name)
    if not os.path.exists(old_path):
        raise HTTPException(status_code=404, detail="文件夹不存在")
    if os.path.exists(new_path):
        raise HTTPException(status_code=400, detail="同名文件夹已存在")
    os.rename(old_path, new_path)
    return {"status": "success", "message": f"文件夹 [{folder_name}] → [{new_name}]"}

@app.delete("/api/messages/{msg_id}")
def delete_message(msg_id: str, username: str = "", folder: str = ""):
    """删除单条消息"""
    if not folder:
        raise HTTPException(status_code=400, detail="请指定文件夹")
    base = _get_msgs_dir(username)
    fp = _safe_path(base, folder, f"{msg_id}.json")
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="消息不存在")
    os.remove(fp)
    return {"status": "success", "message": "消息已删除"}

@app.post("/api/messages/{msg_id}/move")
def move_message(msg_id: str, folder: str = "", username: str = ""):
    """移动消息到指定文件夹"""
    # 在所有文件夹中查找消息
    src_fp = _find_msg_file(username, None, msg_id)
    if not src_fp:
        raise HTTPException(status_code=404, detail="消息不存在")
    dst_dir = os.path.join(_get_msgs_dir(username), folder)
    os.makedirs(dst_dir, exist_ok=True)
    dst_fp = os.path.join(dst_dir, f"{msg_id}.json")
    os.rename(src_fp, dst_fp)
    return {"status": "success", "message": f"已移至 [{folder}]"}

@app.get("/api/messages/unread-count")
def unread_count(username: str = ""):
    """未读消息总数"""
    result = list_folders(username)
    return {"status": "success", "count": result.get("total_unread", 0)}

def _find_msg_file(username: str, folder: str | None, msg_id: str) -> str | None:
    """在消息目录中查找指定 ID 的 .json 文件"""
    base = _get_msgs_dir(username)
    if not os.path.exists(base):
        return None
    if folder:
        path = os.path.join(base, folder, f"{msg_id}.json")
        return path if os.path.exists(path) else None
    # 遍历所有文件夹
    for fd in os.listdir(base):
        path = os.path.join(base, fd, f"{msg_id}.json")
        if os.path.exists(path):
            return path
    return None

# ==========================================
# 笔记本 + 笔记接口（两层：笔记本文件夹 → .md 笔记）
# 路径：students/{username}/notes/{notebook}/{note}.md
# ==========================================
import glob as _glob

class NotebookForm(BaseModel):
    name: str  # 笔记本名称，如 "Python学习"

class NoteSaveForm(BaseModel):
    filename: str
    content: str

def _safe_path(base: str, *parts: str) -> str:
    """防路径穿越检查"""
    fp = os.path.normpath(os.path.join(base, *parts))
    if os.path.commonpath([os.path.normpath(base), fp]) != os.path.normpath(base):
        raise HTTPException(status_code=403, detail="路径不安全")
    return fp

# ─── 笔记本（文件夹）───

@app.get("/api/notebooks")
def list_notebooks(username: str = ""):
    """列出学生的所有笔记本"""
    base = os.path.join(USERS_DIR, "students", username, "notes")
    if not os.path.exists(base):
        return {"status": "success", "notebooks": []}
    items = []
    for name in sorted(os.listdir(base)):
        p = os.path.join(base, name)
        if os.path.isdir(p) and not name.startswith("."):
            md_count = len(_glob.glob(os.path.join(p, "*.md")))
            items.append({"name": name, "note_count": md_count})
    return {"status": "success", "notebooks": items}

@app.post("/api/notebooks")
def create_notebook(form: NotebookForm, username: str = ""):
    """创建笔记本（文件夹）"""
    name = form.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="笔记本名称不能为空")
    base = os.path.join(USERS_DIR, "students", username, "notes")
    nb_path = _safe_path(base, name)
    if os.path.exists(nb_path):
        raise HTTPException(status_code=400, detail="笔记本已存在")
    os.makedirs(nb_path, exist_ok=True)
    return {"status": "success", "message": f"笔记本 [{name}] 创建成功！", "name": name}

@app.delete("/api/notebooks/{name:path}")
def delete_notebook(name: str, username: str = ""):
    """删除笔记本及其所有笔记"""
    base = os.path.join(USERS_DIR, "students", username, "notes")
    nb_path = _safe_path(base, name)
    if not os.path.exists(nb_path):
        raise HTTPException(status_code=404, detail="笔记本不存在")
    shutil.rmtree(nb_path)
    return {"status": "success", "message": f"笔记本 [{name}] 已删除"}

@app.put("/api/notebooks/{name:path}")
def rename_notebook(name: str, username: str = "", new_name: str = ""):
    """重命名笔记本"""
    if not new_name or new_name == name:
        raise HTTPException(status_code=400, detail="新名称无效")
    base = os.path.join(USERS_DIR, "students", username, "notes")
    old_path = _safe_path(base, name)
    new_path = _safe_path(base, new_name)
    if not os.path.exists(old_path):
        raise HTTPException(status_code=404, detail="笔记本不存在")
    if os.path.exists(new_path):
        raise HTTPException(status_code=400, detail="同名笔记本已存在")
    os.rename(old_path, new_path)
    return {"status": "success", "message": f"笔记本 [{name}] → [{new_name}]"}

# ─── 笔记（.md 文件）───

@app.get("/api/notes")
def list_notes(username: str = "", notebook: str = ""):
    """列出笔记本内的所有笔记"""
    base = os.path.join(USERS_DIR, "students", username, "notes", notebook)
    if not os.path.exists(base):
        return {"status": "success", "notes": []}
    files = sorted(
        _glob.glob(os.path.join(base, "*.md")),
        key=os.path.basename
    )
    notes = []
    for fp in files:
        fname = os.path.basename(fp)
        stat = os.stat(fp)
        notes.append({"filename": fname, "size": stat.st_size, "modified": stat.st_mtime})
    return {"status": "success", "notes": notes}

@app.get("/api/notes/{notebook}/{filename:path}")
def read_note(notebook: str, filename: str, username: str = ""):
    """读取单篇笔记"""
    base = os.path.join(USERS_DIR, "students", username, "notes")
    fp = _safe_path(base, notebook, filename)
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="笔记不存在")
    with open(fp, "r", encoding="utf-8") as f:
        return {"status": "success", "filename": filename, "notebook": notebook, "content": f.read()}

@app.post("/api/notes")
def save_note(form: NoteSaveForm, username: str = "", notebook: str = ""):
    """创建或更新笔记"""
    if not notebook:
        raise HTTPException(status_code=400, detail="请指定笔记本")
    if not form.filename.endswith(".md"):
        form.filename += ".md"
    base = os.path.join(USERS_DIR, "students", username, "notes", notebook)
    os.makedirs(base, exist_ok=True)
    fp = _safe_path(base, form.filename)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(form.content)
    return {"status": "success", "message": f"笔记 [{form.filename}] 保存成功！"}

@app.put("/api/notes/{notebook}/{filename:path}")
def rename_note(notebook: str, filename: str, username: str = "", new_name: str = ""):
    """重命名笔记"""
    if not new_name or new_name == filename:
        raise HTTPException(status_code=400, detail="新名称无效")
    if not new_name.endswith(".md"):
        new_name += ".md"
    base = os.path.join(USERS_DIR, "students", username, "notes")
    old_fp = _safe_path(base, notebook, filename)
    new_fp = _safe_path(base, notebook, new_name)
    if not os.path.exists(old_fp):
        raise HTTPException(status_code=404, detail="笔记不存在")
    if os.path.exists(new_fp):
        raise HTTPException(status_code=400, detail="同名笔记已存在")
    os.rename(old_fp, new_fp)
    return {"status": "success", "message": f"笔记 [{filename}] → [{new_name}]"}

@app.delete("/api/notes/{notebook}/{filename:path}")
def delete_note(notebook: str, filename: str, username: str = ""):
    """删除一篇笔记"""
    base = os.path.join(USERS_DIR, "students", username, "notes")
    fp = _safe_path(base, notebook, filename)
    if not os.path.exists(fp):
        raise HTTPException(status_code=404, detail="笔记不存在")
    os.remove(fp)
    return {"status": "success", "message": f"笔记 [{filename}] 已删除"}

# ==========================================
# 老师班级管理 API
# ==========================================
def _get_classes_path(teacher_username: str) -> str:
    """根据老师用户名查找其 classes.json（用 id 做文件夹名）"""
    db = SessionLocal()
    try:
        user = db.query(UserModel).filter(UserModel.username == teacher_username).first()
        if user:
            return os.path.join(USERS_DIR, "teachers", str(user.id), "classes.json")
        # 降级：用 username（兼容旧数据或未创建的情况）
        return os.path.join(USERS_DIR, "teachers", teacher_username, "classes.json")
    finally:
        db.close()

def _load_classes(teacher_username: str) -> list:
    path = _get_classes_path(teacher_username)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_classes(teacher_username: str, classes: list):
    path = _get_classes_path(teacher_username)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(classes, f, ensure_ascii=False, indent=2)

class ClassForm(BaseModel):
    name: str
    schedule: list[str] = []

class StudentForm(BaseModel):
    name: str
    account: str = ""
    remark: str = ""
    existing_id: str = ""  # 非空时表示沿用已有学生 ID

@app.get("/api/classes")
def list_classes(username: str = "", db: Session = Depends(get_db)):
    classes = _load_classes(username)
    # 为每个学生填充当前 nickname
    for c in classes:
        enriched = []
        for s in c.get("students", []):
            user_id = s.get("user_id")
            if user_id:
                user = db.query(UserModel).filter(UserModel.id == user_id).first()
                enriched.append({
                    "user_id": user_id,
                    "username": user.username if user else "未知",
                })
            else:
                enriched.append(s)
        c["students"] = enriched
    return {"status": "success", "classes": classes}

@app.post("/api/classes")
def create_class(form: ClassForm, username: str = ""):
    classes = _load_classes(username)
    if any(c["name"] == form.name for c in classes):
        raise HTTPException(status_code=400, detail="班级已存在")
    classes.append({"name": form.name, "schedule": form.schedule, "students": []})
    _save_classes(username, classes)
    return {"status": "success", "message": f"班级 [{form.name}] 已创建"}

@app.put("/api/classes/{class_name}")
def update_class(class_name: str, form: ClassForm, username: str = ""):
    classes = _load_classes(username)
    for c in classes:
        if c["name"] == class_name:
            c["name"] = form.name
            c["schedule"] = form.schedule
            _save_classes(username, classes)
            return {"status": "success"}
    raise HTTPException(status_code=404, detail="班级不存在")

# ==========================================
# 统一回收站
# ==========================================
def _move_to_trash(source_path: str, trash_type: str, owner: str, original_path: str) -> str:
    """将文件/夹移至回收站，写入 .trash.json 元数据"""
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    name = os.path.basename(source_path.rstrip("/\\"))
    trash_folder_name = f"{trash_type}__{owner}__{name}__{timestamp}"
    dst = os.path.join(TRASH_DIR, trash_folder_name)
    os.makedirs(TRASH_DIR, exist_ok=True)
    os.makedirs(dst, exist_ok=True)
    shutil.move(source_path, os.path.join(dst, name))
    meta = {
        "type": trash_type,
        "owner": owner,
        "original_path": original_path,
        "deleted_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    with open(os.path.join(dst, ".trash.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return trash_folder_name

def _list_trash_items(owner: str = "") -> list:
    """列出所有回收项，可选按 owner 过滤"""
    if not os.path.exists(TRASH_DIR):
        return []
    items = []
    for folder_name in sorted(os.listdir(TRASH_DIR), reverse=True):
        folder_path = os.path.join(TRASH_DIR, folder_name)
        if not os.path.isdir(folder_path):
            continue
        meta_path = os.path.join(folder_path, ".trash.json")
        if not os.path.exists(meta_path):
            continue
        try:
            with open(meta_path, "r", encoding="utf-8") as f:
                meta = json.load(f)
        except (json.JSONDecodeError, OSError):
            continue
        if owner and meta.get("owner") != owner:
            continue
        # 估算文件夹大小
        total_size = 0
        for dirpath, dirnames, filenames in os.walk(folder_path):
            for fn in filenames:
                if fn == ".trash.json":
                    continue
                fp = os.path.join(dirpath, fn)
                try:
                    total_size += os.path.getsize(fp)
                except OSError:
                    pass
        meta["_folder"] = folder_name
        meta["_size"] = total_size
        items.append(meta)
    return items

@app.delete("/api/classes/{class_name}")
def delete_class(class_name: str, username: str = ""):
    classes = _load_classes(username)
    deleted = None
    for c in classes:
        if c["name"] == class_name:
            deleted = c
            break
    if not deleted:
        raise HTTPException(status_code=404, detail="班级不存在")
    # 先保存班级数据到临时文件并移入回收站，成功后再从 classes.json 移除
    tmp = os.path.join(TRASH_DIR, f"__tmp_{username}_{class_name}.json")
    os.makedirs(TRASH_DIR, exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"class_data": deleted}, f, ensure_ascii=False, indent=2)
    owner_path = os.path.join("users", "teachers", username, "trash")
    try:
        _move_to_trash(tmp, "class", username, class_name)
    except Exception:
        # 垃圾箱失败则清理临时文件，保留班级
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
        raise HTTPException(status_code=500, detail="移动到回收站失败，班级未删除")
    # 垃圾箱成功后安全移除
    classes = [c for c in classes if c["name"] != class_name]
    _save_classes(username, classes)
    return {"status": "success", "message": f"班级 [{class_name}] 已移到回收站"}

@app.delete("/api/courses/{course_name:path}")
def delete_course(course_name: str, teacher: str = ""):
    """删除课程（移到回收站）"""
    base = _get_teacher_course_dir(teacher)
    course_path = _safe_path(base, course_name)
    if not os.path.exists(course_path):
        raise HTTPException(status_code=404, detail="课程不存在")
    rel_path = os.path.join("courses", f"{teacher}_course", course_name)
    _move_to_trash(course_path, "course", teacher, rel_path)
    return {"status": "success", "message": f"课程 [{course_name}] 已移到回收站"}

# ── 统一回收站 API ──

@app.get("/api/trash/items")
def list_trash_items(username: str = ""):
    """列出回收站所有项目（可选按 owner 过滤）"""
    items = _list_trash_items(owner=username)
    return {"status": "success", "items": items}

@app.post("/api/trash/items/{folder_name}/restore")
def restore_trash_item(folder_name: str, username: str = ""):
    """恢复回收站项目"""
    folder_path = os.path.join(TRASH_DIR, folder_name)
    meta_path = os.path.join(folder_path, ".trash.json")
    if not os.path.exists(meta_path):
        raise HTTPException(status_code=404, detail="回收站中未找到该项目")
    with open(meta_path, "r", encoding="utf-8") as f:
        meta = json.load(f)
    # 恢复班级
    if meta["type"] == "class":
        # 把临时 JSON 文件读回来
        json_files = [f for f in os.listdir(folder_path) if f.endswith(".json") and f != ".trash.json"]
        if not json_files:
            raise HTTPException(status_code=404, detail="回收站数据损坏")
        with open(os.path.join(folder_path, json_files[0]), "r", encoding="utf-8") as f:
            data = json.load(f)
        restored = data.get("class_data")
        if not restored:
            raise HTTPException(status_code=404, detail="回收站数据损坏")
        classes = _load_classes(username)
        classes.append(restored)
        _save_classes(username, classes)
    # 恢复课程
    elif meta["type"] == "course":
        base = _get_teacher_course_dir(meta["owner"])
        target = os.path.join(base, os.path.basename(meta["original_path"]))
        shutil.move(folder_path, target)
        # 删掉 .trash.json（目标里不需要它）
        trash_meta = os.path.join(target, ".trash.json")
        if os.path.exists(trash_meta):
            os.remove(trash_meta)
        return {"status": "success", "message": "课程已恢复"}
    else:
        raise HTTPException(status_code=400, detail="不支持的回收站类型")
    # 删除回收站中的临时文件
    shutil.rmtree(folder_path)
    return {"status": "success", "message": "已恢复"}

@app.delete("/api/trash/items/{folder_name}")
def permanent_delete_trash_item(folder_name: str, username: str = ""):
    """永久删除回收站项目"""
    folder_path = os.path.join(TRASH_DIR, folder_name)
    if not os.path.exists(folder_path):
        raise HTTPException(status_code=404, detail="回收站中未找到该项目")
    shutil.rmtree(folder_path)
    return {"status": "success", "message": "已永久删除"}

@app.post("/api/classes/{class_name}/students")
def add_student(class_name: str, form: StudentForm, username: str = ""):
    db_local = SessionLocal()
    try:
        classes = _load_classes(username)
        for c in classes:
            if c["name"] == class_name:
                if form.existing_id:
                    # 跨班共享已有学生
                    user_id = int(form.existing_id)
                    user = db_local.query(UserModel).filter(UserModel.id == user_id).first()
                    if not user:
                        raise HTTPException(status_code=404, detail="学生不存在")
                else:
                    # 新学生 → 自动创建系统账号
                    new_username = _make_student_username(username, class_name, form.name)
                    new_user = _ensure_user(
                        db_local, new_username, "student", "default.jpg",
                        {"teacher": username}
                    )
                    user_id = new_user.id

                c["students"].append({
                    "user_id": user_id,
                    "name": form.name,
                })
                _save_classes(username, classes)
                return {
                    "status": "success",
                    "user_id": user_id,
                }
        raise HTTPException(status_code=404, detail="班级不存在")
    finally:
        db_local.close()

@app.get("/api/classes/find-student")
def find_student(name: str = ""):
    """全校搜索同名学生在哪些老师的哪些班级"""
    teachers_dir = os.path.join(USERS_DIR, "teachers")
    if not os.path.exists(teachers_dir):
        return {"status": "success", "matches": []}

    matches = []
    for t_folder in os.listdir(teachers_dir):
        t_path = os.path.join(teachers_dir, t_folder)
        if not os.path.isdir(t_path):
            continue
        classes_path = os.path.join(t_path, "classes.json")
        if not os.path.exists(classes_path):
            continue
        with open(classes_path, "r", encoding="utf-8") as f:
            try:
                classes = json.load(f)
            except json.JSONDecodeError:
                continue
        for c in classes:
            for s in c.get("students", []):
                if s.get("name") == name and s.get("user_id"):
                    matches.append({
                        "teacher": t_folder,
                        "class_name": c["name"],
                        "user_id": s["user_id"],
                    })
    return {"status": "success", "matches": matches}

@app.get("/api/students/list")
def list_all_students():
    """扫 students/ 目录，返回全校学生列表"""
    students_dir = os.path.join(USERS_DIR, "students")
    if not os.path.exists(students_dir):
        return {"status": "success", "students": []}

    result = []
    for folder in sorted(os.listdir(students_dir), key=lambda x: int(x) if x.isdigit() else 0):
        info_path = os.path.join(students_dir, folder, "info.json")
        if not os.path.exists(info_path):
            continue
        try:
            with open(info_path, "r", encoding="utf-8") as f:
                info = json.load(f)
            result.append({
                "user_id": int(folder),
                "username": info.get("username", ""),
                "avatar_path": info.get("avatar_path", "default.jpg"),
            })
        except (json.JSONDecodeError, ValueError):
            continue
    return {"status": "success", "students": result}

@app.delete("/api/classes/{class_name}/students/{student_id}")
def remove_student(class_name: str, student_id: str, username: str = ""):
    classes = _load_classes(username)
    target_id = int(student_id)
    for c in classes:
        if c["name"] == class_name:
            c["students"] = [s for s in c["students"] if s.get("user_id") != target_id]
            _save_classes(username, classes)
            return {"status": "success"}
    raise HTTPException(status_code=404, detail="班级不存在")

# ==========================================
# 课堂记录 API（文件系统）
# 路径：teachers/{username}/sessions/{class_name}/{YYYY-MM}.json
# 按月归档，每月一个 JSON 文件，内为数组，每节课一条记录
# ==========================================

class SessionStudentRecord(BaseModel):
    id: str
    name: str
    remark: str = ""
    status: str = "present"  # present / absent
    content: str = ""
    next_plan: str = ""

class SessionForm(BaseModel):
    date: str  # YYYY-MM-DD
    students: list[SessionStudentRecord]

def _get_session_path(username: str, class_name: str, month: str) -> str:
    """获取某月课堂记录文件路径"""
    return os.path.join(USERS_DIR, "teachers", username, "sessions", class_name, f"{month}.json")

def _load_sessions(username: str, class_name: str, month: str) -> list:
    """加载某月课堂记录，文件不存在则返回空数组"""
    fp = _get_session_path(username, class_name, month)
    if not os.path.exists(fp):
        return []
    with open(fp, "r", encoding="utf-8") as f:
        return json.load(f)

def _save_sessions(username: str, class_name: str, month: str, sessions: list):
    """保存某月课堂记录"""
    fp = _get_session_path(username, class_name, month)
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    with open(fp, "w", encoding="utf-8") as f:
        json.dump(sessions, f, ensure_ascii=False, indent=2)

@app.post("/api/sessions/{class_name}")
def save_session(class_name: str, form: SessionForm, username: str = ""):
    """创建或更新课堂记录（按日期 upsert）"""
    month = form.date[:7]  # "2026-06"
    sessions = _load_sessions(username, class_name, month)
    # 转 dict 方便序列化
    new_record = {
        "date": form.date,
        "students": [s.model_dump() for s in form.students]
    }
    # 查找是否有同日期记录，有则替换，无则追加
    for i, s in enumerate(sessions):
        if s["date"] == form.date:
            sessions[i] = new_record
            break
    else:
        sessions.append(new_record)
    # 按日期倒序排列
    sessions.sort(key=lambda x: x["date"], reverse=True)
    _save_sessions(username, class_name, month, sessions)
    return {"status": "success", "message": f"{form.date} 课堂记录已保存"}

@app.get("/api/sessions/{class_name}")
def list_sessions(class_name: str, username: str = "", month: str = ""):
    """列出某班级某月的课堂记录，不传 month 则返回所有月份合并"""
    sessions_dir = os.path.join(USERS_DIR, "teachers", username, "sessions", class_name)
    if month:
        return {"status": "success", "sessions": _load_sessions(username, class_name, month)}
    # 不传 month → 遍历所有月份文件
    if not os.path.exists(sessions_dir):
        return {"status": "success", "sessions": []}
    all_sessions = []
    for fname in sorted(os.listdir(sessions_dir), reverse=True):
        if not fname.endswith(".json"):
            continue
        fp = os.path.join(sessions_dir, fname)
        try:
            with open(fp, "r", encoding="utf-8") as f:
                all_sessions.extend(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    all_sessions.sort(key=lambda x: x["date"], reverse=True)
    return {"status": "success", "sessions": all_sessions}

@app.get("/api/sessions/{class_name}/{date}")
def get_session(class_name: str, date: str, username: str = ""):
    """获取某天的课堂记录"""
    month = date[:7]
    sessions = _load_sessions(username, class_name, month)
    for s in sessions:
        if s["date"] == date:
            return {"status": "success", "session": s}
    raise HTTPException(status_code=404, detail=f"{date} 没有课堂记录")

@app.delete("/api/sessions/{class_name}/{date}")
def delete_session(class_name: str, date: str, username: str = ""):
    """删除某天的课堂记录"""
    month = date[:7]
    sessions = _load_sessions(username, class_name, month)
    filtered = [s for s in sessions if s["date"] != date]
    if len(filtered) == len(sessions):
        raise HTTPException(status_code=404, detail=f"{date} 没有课堂记录")
    _save_sessions(username, class_name, month, filtered)
    return {"status": "success", "message": f"{date} 课堂记录已删除"}

# ==========================================
# 课件管理 API（文件系统）
# 路径：data/share/{teacher}_course/{course_name}/
# 每个课程文件夹内含 course.json + 课件子文件夹
# ==========================================
COURSE_BASE = os.path.abspath(os.path.join(BACKEND_DIR, "..", "..", "data", "courses"))

# 启动时自动创建必要目录（防止新环境部署缺少目录）
os.makedirs(COURSE_BASE, exist_ok=True)

def _get_teacher_course_dir(teacher_username: str) -> str:
    return os.path.join(COURSE_BASE, f"{teacher_username}_course")

class CourseCreateForm(BaseModel):
    teacher: str
    name: str
    course_type: str = "scratch"

@app.get("/api/courses")
def list_courses(teacher: str = ""):
    """列出老师的所有课程（扁平文件夹 + course.json）"""
    course_dir = _get_teacher_course_dir(teacher)
    if not os.path.exists(course_dir):
        return {"status": "success", "courses": []}
    courses = []
    for name in sorted(os.listdir(course_dir)):
        folder_path = os.path.join(course_dir, name)
        if not os.path.isdir(folder_path) or name.startswith("."):
            continue
        meta = {"name": name, "type": "scratch", "description": "", "material_count": 0}
        meta_path = os.path.join(folder_path, "course.json")
        if os.path.exists(meta_path):
            try:
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta.update(json.load(f))
            except json.JSONDecodeError:
                pass
        else:
            with open(meta_path, "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
        materials = [
            d for d in os.listdir(folder_path)
            if os.path.isdir(os.path.join(folder_path, d)) and not d.startswith(".") and d != "course.json"
        ]
        meta["material_count"] = len(materials)
        courses.append(meta)
    return {"status": "success", "courses": courses}

@app.post("/api/courses")
def create_course(form: CourseCreateForm):
    """创建课程（建文件夹 + course.json）"""
    course_dir = _get_teacher_course_dir(form.teacher)
    os.makedirs(course_dir, exist_ok=True)
    course_path = os.path.join(course_dir, form.name)
    if os.path.exists(course_path):
        raise HTTPException(status_code=400, detail="课程已存在")
    os.makedirs(course_path)
    meta = {
        "name": form.name,
        "type": form.course_type,
        "description": "",
        "created_at": datetime.now().strftime("%Y-%m-%d"),
        "material_count": 0
    }
    with open(os.path.join(course_path, "course.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    return {"status": "success", "message": f"课程 [{form.name}] 已创建", "course": meta}

# ==========================================
# 课件素材 API（列出/删除课程下的课件文件夹）
# 注意：delete_material 必须在 delete_course 之前定义，
#       否则 {course_name:path} 会贪婪匹配吞掉 /materials/{lesson_name}
# ==========================================
@app.get("/api/courses/{course_name}/materials")
def list_materials(course_name: str, teacher: str = ""):
    """列出课程下的所有课件（子文件夹）"""
    base = _get_teacher_course_dir(teacher)
    course_path = _safe_path(base, course_name)
    if not os.path.exists(course_path):
        raise HTTPException(status_code=404, detail="课程不存在")
    items = []
    for name in sorted(os.listdir(course_path)):
        fp = os.path.join(course_path, name)
        if os.path.isdir(fp) and not name.startswith("."):
            # 检查是否有 sb3 文件
            sb3_files = [f for f in os.listdir(fp) if f.endswith(".sb3")]
            items.append({
                "name": name,
                "has_main": "main.sb3" in sb3_files,
                "has_student": "student.sb3" in sb3_files,
                "sb3_count": len(sb3_files),
                "url": f"/share/{teacher}_course/{course_name}/{name}/"
            })
    return {"status": "success", "materials": items}

@app.delete("/api/courses/{course_name}/materials/{lesson_name}")
def delete_material(course_name: str, lesson_name: str, teacher: str = ""):
    """删除课件（物理删除文件夹）"""
    base = _get_teacher_course_dir(teacher)
    course_path = _safe_path(base, course_name)
    if not os.path.exists(course_path):
        raise HTTPException(status_code=404, detail="课程不存在")
    lesson_path = _safe_path(course_path, lesson_name)
    if not os.path.exists(lesson_path):
        raise HTTPException(status_code=404, detail="课件不存在")
    shutil.rmtree(lesson_path)
    return {"status": "success", "message": f"课件 [{lesson_name}] 已删除"}

@app.delete("/api/courses/{course_name:path}")
def delete_course(course_name: str, teacher: str = ""):
    """删除课程"""
    base = _get_teacher_course_dir(teacher)
    course_path = _safe_path(base, course_name)
    if not os.path.exists(course_path):
        raise HTTPException(status_code=404, detail="课程不存在")
    shutil.rmtree(course_path)
    return {"status": "success", "message": f"课程 [{course_name}] 已删除"}

# ==========================================
# 保存课件 sb3（Scratch 编辑器保存到课程）
# ==========================================
@app.post("/api/courses/{course_name}/materials/{lesson_name}/save-sb3")
async def save_sb3(
    course_name: str,
    lesson_name: str,
    teacher: str = "",
    file_type: str = "main",
    file: UploadFile = File(...),
):
    """保存单个 sb3 文件到课件文件夹
    - file_type: "main" → main.sb3, "student" → student.sb3
    """
    base = _get_teacher_course_dir(teacher)
    course_path = _safe_path(base, course_name)
    if not os.path.exists(course_path):
        raise HTTPException(status_code=404, detail="课程不存在")

    # 创建课件文件夹
    lesson_path = os.path.join(course_path, lesson_name)
    os.makedirs(lesson_path, exist_ok=True)

    # 文件命名
    filename = "main.sb3" if file_type == "main" else "student.sb3"
    dest = os.path.join(lesson_path, filename)

    # 写入文件
    content = await file.read()
    with open(dest, "wb") as f:
        f.write(content)

    return {
        "status": "success",
        "message": f"{filename} 已保存",
        "path": f"/share/{teacher}_course/{course_name}/{lesson_name}/"
    }
