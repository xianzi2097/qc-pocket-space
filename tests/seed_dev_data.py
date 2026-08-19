"""
开发环境测试数据注入脚本
========================
用法：在 QC-PocketSpace 根目录运行
    python labs_dev/tests/seed_dev_data.py

功能：插入测试老师、学生、班级、课程（不含后端代码）
不会影响生产代码，只在本地开发时手动执行
"""
import os
import sys
import json
import random
from datetime import datetime

# 把 backend 目录加到 sys.path，复用 DB 模型
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "qc-pocket-space", "backend"))

from main import (
    SessionLocal, UserModel,
)
from models import USERS_DIR
from utils import hash_password, ensure_user_dirs, get_user_dir, _gen_unique_course_id

# ── 路径 ──
DATA_DIR = os.path.join(PROJECT_ROOT, "data")
COURSE_BASE = os.path.join(DATA_DIR, "courses")


def seed_users():
    """创建测试老师和学生"""
    db = SessionLocal()
    try:
        # ── 测试老师 ──
        teachers = {
            "林老师(t)": {"title": "Scratch 主讲", "avatar": "avatar_01.jpg"},
            "程老师(t)": {"title": "Python 讲师", "avatar": "avatar_02.jpg"},
            "黄老师(t)": {"title": "C++ 教练", "avatar": "avatar_03.jpg"},
        }
        teacher_ids = {}
        for username, info in teachers.items():
            existing = db.query(UserModel).filter(UserModel.username == username).first()
            if existing:
                print(f"  ⏭  跳过已存在: {username}")
                teacher_ids[username] = existing.id
                continue
            user = UserModel(
                username=username, password=hash_password("111111"), role="teacher",
                avatar_path=info["avatar"],
                qc_descriptors=json.dumps({"title": info["title"]}, ensure_ascii=False)
            )
            db.add(user)
            db.commit()
            ensure_user_dirs(user)
            teacher_ids[username] = user.id
            print(f"  ✅ 创建老师: {username} ({info['title']})")

        # ── 50 个测试学生 ──
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
        class_config = {
            "林老师(t)": {
                "classes": ["Scratch启蒙班", "Scratch初级班", "Scratch中级班", "Python基础班", "Python进阶班"],
                "count": 35,
            },
            "程老师(t)": {
                "classes": ["Python入门班", "算法提高班"],
                "count": 8,
            },
            "黄老师(t)": {
                "classes": ["C++基础班", "C++竞赛班", "机器人编程"],
                "count": 7,
            },
        }
        name_idx = 0
        for teacher, cfg in class_config.items():
            tid = teacher_ids[teacher]
            if tid is None:
                print(f"  ⚠️  {teacher} 未创建，跳过学生")
                continue

            t_dir = os.path.join(USERS_DIR, "teachers", str(tid))
            classes_path = os.path.join(t_dir, "classes.json")
            if os.path.exists(classes_path):
                print(f"  ⏭  跳过班级 (已存在): {teacher}")
                # 跳过已有班级的老师
                per_class = cfg["count"] // len(cfg["classes"])
                name_idx += cfg["count"]
                continue

            classes_list = []
            per_class = cfg["count"] // len(cfg["classes"])
            extra = cfg["count"] % len(cfg["classes"])

            for ci, cls_name in enumerate(cfg["classes"]):
                n = per_class + (1 if ci < extra else 0)
                students_list = []
                for _ in range(n):
                    if name_idx >= len(student_names):
                        break
                    stu = student_names[name_idx]
                    name_idx += 1
                    # 生成 student 账号名
                    username = f"{stu}(s)"
                    if db.query(UserModel).filter(UserModel.username == username).first():
                        username = f"{stu}{name_idx}(s)"

                    new_user = UserModel(
                        username=username, password=hash_password("111111"), role="student",
                        avatar_path="default.jpg",
                        qc_descriptors=json.dumps({"teacher": teacher}, ensure_ascii=False)
                    )
                    db.add(new_user)
                    db.commit()
                    ensure_user_dirs(new_user)
                    students_list.append({"user_id": new_user.id, "name": username})

                classes_list.append({"name": cls_name, "students": students_list, "schedule": []})
                print(f"    📚 {cls_name}: {len(students_list)} 人")

            os.makedirs(t_dir, exist_ok=True)
            with open(classes_path, "w", encoding="utf-8") as f:
                json.dump(classes_list, f, ensure_ascii=False, indent=2)

        print(f"\n  ✅ 共创建 {name_idx} 名学生")
        return teacher_ids
    finally:
        db.close()


def seed_courses():
    """创建测试课程（文件系统）— 使用随机 ID 做文件夹名，与 API 行为一致"""
    courses = {
        "林老师": [
            {"name": "Scratch 入门基础", "type": "scratch", "desc": "从零开始学 Scratch，适合零基础学生"},
            {"name": "Scratch 进阶项目", "type": "scratch", "desc": "综合项目实战，做一个完整的小游戏"},
            {"name": "Scratch 游戏开发", "type": "scratch", "desc": "用 Scratch 开发各种有趣的小游戏"},
            {"name": "Python 基础入门", "type": "python", "desc": "Python 语法基础，变量、循环、函数"},
            {"name": "Python 小项目合集", "type": "python", "desc": "用 Python 做实用小工具"},
            {"name": "Python 图形界面", "type": "python", "desc": "用 tkinter 做桌面小应用"},
            {"name": "C++ 入门", "type": "cpp", "desc": "C++ 基础语法和简单算法"},
            {"name": "C++ 算法思维", "type": "cpp", "desc": "用 C++ 学习经典算法和数据结构"},
        ],
        "黄老师": [
            {"name": "Scratch 趣味编程", "type": "scratch", "desc": "用 Scratch 做动画和简单游戏"},
            {"name": "Scratch 故事创作", "type": "scratch", "desc": "用 Scratch 创作互动故事"},
            {"name": "Python 爬虫入门", "type": "python", "desc": "学 Python 写爬虫，抓取网页数据"},
            {"name": "Python 自动化办公", "type": "python", "desc": "用 Python 自动处理 Excel/Word"},
            {"name": "C++ 竞赛预备", "type": "cpp", "desc": "信息学奥赛入门"},
            {"name": "C++ 数据结构", "type": "cpp", "desc": "链表、栈、队列、树"},
        ],
        "程老师": [
            {"name": "Scratch 创意工坊", "type": "scratch", "desc": "自由创作，发挥想象力做自己的项目"},
            {"name": "Python 数据分析", "type": "python", "desc": "用 Python 处理数据、画图表"},
            {"name": "Python Web 入门", "type": "python", "desc": "用 Flask 搭建简单的网页应用"},
            {"name": "C++ 算法进阶", "type": "cpp", "desc": "动态规划、图论等进阶算法"},
        ],
    }

    for teacher, course_list in courses.items():
        teacher_dir = os.path.join(COURSE_BASE, f"{teacher}_course")
        os.makedirs(teacher_dir, exist_ok=True)

        # 收集已有课程名（避免重复创建）
        existing_names = set()
        if os.path.exists(teacher_dir):
            for folder in os.listdir(teacher_dir):
                meta_path = os.path.join(teacher_dir, folder, "course.json")
                if os.path.exists(meta_path):
                    with open(meta_path, "r", encoding="utf-8") as f:
                        try:
                            existing_names.add(json.load(f).get("name", ""))
                        except (json.JSONDecodeError, OSError):
                            pass

        for c in course_list:
            if c["name"] in existing_names:
                print(f"  ⏭  跳过课程 (已存在): {teacher}/{c['name']}")
                continue

            course_id = _gen_unique_course_id()
            course_dir = os.path.join(teacher_dir, course_id)
            os.makedirs(course_dir, exist_ok=True)
            meta = {
                "id": course_id, "name": c["name"], "type": c["type"],
                "description": c["desc"],
                "teacher": f"{teacher}(t)",
                "publish": {"status": "idle"},
                "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
            with open(os.path.join(course_dir, "course.json"), "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)
            # 每个课程 2~5 个课件
            for i in range(random.randint(2, 5)):
                mat_name = f"第{i+1}课" if c["type"] == "scratch" else f"Lesson_{i+1}"
                os.makedirs(os.path.join(course_dir, mat_name), exist_ok=True)
            existing_names.add(c["name"])
            print(f"  ✅ {teacher}: {c['name']} ({c['type']}) → {course_id}")


if __name__ == "__main__":
    print("🌱 开发环境数据注入")
    print("=" * 50)
    print("\n👤 创建测试用户...")
    seed_users()
    print("\n📚 创建测试课程...")
    seed_courses()
    print("\n✅ 全部完成！")
    print("   管理员: admin / 111111")
    print("   老师密码: 111111")
    print("   学生密码: 111111")
