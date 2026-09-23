"""
测试老师种子脚本（专门给「老师 → 班级 → 学生 → 记录」链路测试用）
=============================================================
用法：在 CodeNova-Workspace 根目录运行
    python labs_dev/tests/seed_test_teacher.py

造的数据（幂等，可重复跑）：
    1 个测试老师：测试老师(t) / 111111
    3 个班级：    测试一班(5人) / 测试二班(7人) / 测试三班(3人)
    15 个学生：   测试学生01(s) ~ 测试学生15(s)，密码都是 111111
"""
import os
import sys
import json

# 把 backend 目录加到 sys.path，复用 DB 模型
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(PROJECT_ROOT, "qc-pocket-space", "backend"))

from main import SessionLocal, UserModel
from models import USERS_DIR
from utils import hash_password, ensure_user_dirs

TEACHER_USERNAME = "测试老师(t)"
TEACHER_TITLE = "综合测试老师"
PASSWORD = "111111"

# 3 个班级，每班人数在 3~8 之间
CLASS_CONFIG = [
    {"name": "测试一班", "count": 5},
    {"name": "测试二班", "count": 7},
    {"name": "测试三班", "count": 3},
]


def _find_or_create_user(db, username: str, role: str, desc: str):
    """按用户名查找，不存在则创建，返回 user 对象"""
    user = db.query(UserModel).filter(UserModel.username == username).first()
    if user:
        print(f"  ⏭  已存在: {username} ({user.role})")
        return user, False
    user = UserModel(
        username=username, password=hash_password(PASSWORD), role=role,
        avatar_path="default.jpg", qc_descriptors=json.dumps(desc, ensure_ascii=False)
    )
    db.add(user)
    db.commit()
    ensure_user_dirs(user)
    print(f"  ✅ 创建 {role}: {username}")
    return user, True


def seed():
    db = SessionLocal()
    try:
        # ── 1. 测试老师 ──
        teacher, _ = _find_or_create_user(
            db, TEACHER_USERNAME, "teacher", {"title": TEACHER_TITLE}
        )

        # ── 2. 学生 + 班级 ──
        student_idx = 1
        classes_list = []
        for cfg in CLASS_CONFIG:
            students_list = []
            for _ in range(cfg["count"]):
                username = f"测试学生{student_idx:02d}(s)"
                student_idx += 1
                stu, _ = _find_or_create_user(
                    db, username, "student", {"teacher": TEACHER_USERNAME}
                )
                students_list.append({"user_id": stu.id, "name": username})
            classes_list.append({
                "name": cfg["name"],
                "schedule": {"days": [], "time": "", "location": ""},
                "students": students_list,
            })
            print(f"  📚 {cfg['name']}: {len(students_list)} 人")

        # ── 3. 写 classes.json（老师端主流程读这里） ──
        classes_path = os.path.join(USERS_DIR, "teachers", str(teacher.id), "classes.json")
        os.makedirs(os.path.dirname(classes_path), exist_ok=True)
        with open(classes_path, "w", encoding="utf-8") as f:
            json.dump(classes_list, f, ensure_ascii=False, indent=2)
        print(f"\n  📄 classes.json 已写入: {classes_path}")

        print("\n  ================= 测试账号一览 =================")
        print(f"  老师: {TEACHER_USERNAME} / {PASSWORD}")
        for cfg in CLASS_CONFIG:
            print(f"    - {cfg['name']} ({cfg['count']}人)")
        print(f"  学生: 测试学生01(s) ~ 测试学生{student_idx-1:02d}(s) / {PASSWORD}")
        print("  ================================================")
    finally:
        db.close()


if __name__ == "__main__":
    print("🌱 注入测试老师 + 3 班级 + 15 学生")
    print("=" * 50)
    seed()
