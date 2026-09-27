"""
pytest 全局配置
===============
Session 开始/结束时清理测试残留数据，防止 trash 表无限膨胀。
"""
import sys
import os

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, BACKEND_DIR)

import pytest
from models import SessionLocal, TrashModel, UserModel
from utils import hash_password


def _clean_test_trash():
    """清空垃圾桶中所有 biz_/e2e_/sec_/conc_ 前缀的测试残留"""
    db = SessionLocal()
    try:
        items = db.query(TrashModel).all()
        cleaned = 0
        for t in items:
            if t.username and (t.username.startswith("biz_") or t.username.startswith("e2e_")
                               or t.username.startswith("conc_") or t.username.startswith("sec_")):
                trash_path = os.path.join(
                    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                    "data", "trash", t.trash_folder
                )
                if os.path.exists(trash_path):
                    import shutil
                    shutil.rmtree(trash_path, ignore_errors=True)
                db.delete(t)
                cleaned += 1
        if cleaned:
            db.commit()
            print(f"\n  🧹 conftest: 清理了 {cleaned} 条测试垃圾记录")
    finally:
        db.close()


def _clean_test_course_dirs():
    """清理测试产生的课程目录（biz_*, e2e_* 前缀的老师课程目录）"""
    data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
    courses_dir = os.path.join(data_dir, "courses")
    if not os.path.isdir(courses_dir):
        return
    cleaned = 0
    for name in os.listdir(courses_dir):
        if name.endswith("_course") and any(name.startswith(p) for p in ("biz_", "e2e_", "conc_", "sec_")):
            import shutil
            shutil.rmtree(os.path.join(courses_dir, name), ignore_errors=True)
            cleaned += 1
    if cleaned:
        print(f"\n  🧹 conftest: 清理了 {cleaned} 个测试课程目录")


def _clean_test_resource_dirs():
    """清理测试产生的资源目录（biz_*, e2e_* 前缀的老师资源目录）"""
    data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
    resources_dir = os.path.join(data_dir, "resources")
    if not os.path.isdir(resources_dir):
        return
    cleaned = 0
    for name in os.listdir(resources_dir):
        if name.endswith("_resource") and any(name.startswith(p) for p in ("biz_", "e2e_", "conc_", "sec_")):
            import shutil
            shutil.rmtree(os.path.join(resources_dir, name), ignore_errors=True)
            cleaned += 1
    if cleaned:
        print(f"\n  🧹 conftest: 清理了 {cleaned} 个测试资源目录")


def _clean_test_file_trash():
    """清理文件版回收站中的测试残留（课程/资源回收目录，形如 course__biz_xxx__ts）"""
    data_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "data"))
    trash_dir = os.path.join(data_dir, "trash")
    if not os.path.isdir(trash_dir):
        return
    cleaned = 0
    for name in os.listdir(trash_dir):
        if any(f"__{p}" in name for p in ("biz_", "e2e_", "conc_", "sec_")):
            import shutil
            shutil.rmtree(os.path.join(trash_dir, name), ignore_errors=True)
            cleaned += 1
    if cleaned:
        print(f"\n  🧹 conftest: 清理了 {cleaned} 个文件回收站目录")


def _ensure_admin():
    """确保 admin 存在（测试可能删了它）"""
    db = SessionLocal()
    try:
        admin = db.query(UserModel).filter(UserModel.username == "admin").first()
        if not admin:
            user = UserModel(
                username="admin", password=hash_password("111111"),
                role="admin", avatar_path="default.jpg", qc_descriptors="{}"
            )
            db.add(user)
            db.commit()
            print("\n  🔧 conftest: 重建了 admin 账号")
    finally:
        db.close()


@pytest.fixture(scope="session", autouse=True)
def session_cleanup():
    """Session 开始前清理残留，结束后再清理一次"""
    _clean_test_trash()
    _clean_test_course_dirs()
    _clean_test_resource_dirs()
    _clean_test_file_trash()
    _ensure_admin()
    yield
    _clean_test_trash()
    _clean_test_course_dirs()
    _clean_test_resource_dirs()
    _clean_test_file_trash()
