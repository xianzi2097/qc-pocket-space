"""
单元测试 — 共享文件树 file_tree（课程/资料共用的单一事实来源）
================================================================
纯函数层（tempdir，不碰 data/、不碰数据库）+ HTTP 层全链路（TestClient）。
背景：2026-08-31 资源模块从课程复用文件树时漏了 reorder，前端拖拽 404 静默失败，
本文件同时锁住「逻辑」和「路由存在性」，防回归。

运行方式：
  cd C:/Arin - cabin/CodeNova-Workspace/qc-pocket-space/tests
  venv/Scripts/python.exe -m pytest test_file_tree.py -v
"""
import json
import os
import shutil
import sys
import tempfile

import pytest
from fastapi import HTTPException

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, BACKEND_DIR)

from file_tree import COURSE_META, RESOURCE_META, reorder_entry, split_rel, walk_tree

BS = chr(92)  # 反斜杠字符，避免源码里的转义歧义


def _mk(base, *rel_dirs, files=()):
    """在 tempdir 里铺目录和文件"""
    for d in rel_dirs:
        os.makedirs(os.path.join(base, d), exist_ok=True)
    for f in files:
        fp = os.path.join(base, f)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as fh:
            fh.write("x")


def _names(tree):
    return [n["name"] for n in tree]


# ============================================================
# 切路径段 — split_rel
# ============================================================

class Test路径拆段:

    def test_正斜杠多级(self):
        assert split_rel("a/b/c") == ["a", "b", "c"]

    def test_反斜杠兼容(self):
        assert split_rel("a" + BS + "b") == ["a", "b"]

    def test_穿越与点段被过滤(self):
        assert split_rel("/.././x/..") == ["x"]

    def test_空与None返回空列表(self):
        assert split_rel("") == []
        assert split_rel(None) == []


# ============================================================
# walk — 课程模式（dirs_first=False，交错自然排序，无 path 字段）
# ============================================================

class Testwalk课程模式:

    def test_目录文件交错按自然排序(self):
        """课程现状：文件夹不置顶，和文件一起按名字混排"""
        with tempfile.TemporaryDirectory() as base:
            _mk(base, "zdir", files=("a.txt", "b.txt"))
            tree = walk_tree(base, meta_files=COURSE_META)
            assert _names(tree) == ["a.txt", "b.txt", "zdir"]

    def test_元数据与隐藏文件不出现(self):
        with tempfile.TemporaryDirectory() as base:
            _mk(base, files=("course.json", "_order.json", ".secret", "ok.txt"))
            tree = walk_tree(base, meta_files=COURSE_META)
            assert _names(tree) == ["ok.txt"]

    def test_字段现状_无path无目录size(self):
        """课程树字段：dir={name,type,children}，file={name,type,size}"""
        with tempfile.TemporaryDirectory() as base:
            _mk(base, "d1", files=("a.txt",))
            tree = walk_tree(base, meta_files=COURSE_META)
            d = next(n for n in tree if n["type"] == "dir")
            f = next(n for n in tree if n["type"] == "file")
            assert set(d.keys()) == {"name", "type", "children"}
            assert set(f.keys()) == {"name", "type", "size"}


# ============================================================
# walk — 资料模式（dirs_first=True 文件夹置顶 + path 字段）
# ============================================================

class Testwalk资料模式:

    def test_文件夹置顶后各自自然排序(self):
        with tempfile.TemporaryDirectory() as base:
            _mk(base, "zdir", files=("b.txt", "a.txt"))
            tree = walk_tree(base, meta_files=RESOURCE_META, dirs_first=True)
            assert _names(tree) == ["zdir", "a.txt", "b.txt"]

    def test_字段现状_含path与目录size(self):
        with tempfile.TemporaryDirectory() as base:
            _mk(base, "d1", files=("a.txt",))
            tree = walk_tree(base, meta_files=RESOURCE_META, dirs_first=True,
                             with_path=True, with_dir_size=True)
            d, f = tree[0], tree[1]
            assert d["path"] == "d1" and isinstance(d["size"], int)
            assert f["path"] == "a.txt"

    def test_元数据与隐藏文件不出现(self):
        with tempfile.TemporaryDirectory() as base:
            _mk(base, files=("resource.json", "_order.json", ".hide", "a.txt"))
            tree = walk_tree(base, meta_files=RESOURCE_META, dirs_first=True, with_path=True)
            assert _names(tree) == ["a.txt"]

    def test_嵌套子目录path为根相对(self):
        with tempfile.TemporaryDirectory() as base:
            _mk(base, "sub", files=("sub/leaf.txt",))
            tree = walk_tree(base, meta_files=RESOURCE_META, dirs_first=True, with_path=True)
            sub = tree[0]
            assert sub["path"] == "sub"
            assert sub["children"][0]["path"] == "sub/leaf.txt"


# ============================================================
# walk — 自定义顺序 _order.json 优先
# ============================================================

class Testwalk自定义顺序:

    def test_自定义顺序优先且目录文件混排(self):
        """用户拖过拽后完全按排的来（dirs_first 不再生效）"""
        with tempfile.TemporaryDirectory() as base:
            _mk(base, "zdir", files=("a.txt", "m.txt"))
            order = {"": ["m.txt", "zdir", "a.txt"]}
            for dirs_first in (False, True):
                tree = walk_tree(base, order, meta_files=RESOURCE_META, dirs_first=dirs_first)
                assert _names(tree) == ["m.txt", "zdir", "a.txt"]

    def test_子目录key生效(self):
        with tempfile.TemporaryDirectory() as base:
            _mk(base, "sub", files=("sub/b1.txt", "sub/a2.txt"))
            order = {"sub": ["b1.txt", "a2.txt"]}
            tree = walk_tree(base, order, meta_files=COURSE_META)
            sub = next(n for n in tree if n["type"] == "dir")
            assert _names(sub["children"]) == ["b1.txt", "a2.txt"]

    def test_order中已删条目忽略_磁盘新条目排末尾(self):
        with tempfile.TemporaryDirectory() as base:
            _mk(base, files=("a.txt", "b.txt"))
            order = {"": ["b.txt", "ghost.txt"]}
            tree = walk_tree(base, order, meta_files=COURSE_META)
            assert _names(tree) == ["b.txt", "a.txt"]


# ============================================================
# reorder — 拖拽排序持久化
# ============================================================

class Testreorder逻辑:

    def test_swap拖拽定位并写盘(self):
        with tempfile.TemporaryDirectory() as base:
            _mk(base, files=("a.txt", "b.txt", "c.txt"))
            result = reorder_entry(base, "a.txt", "", "swap", "c.txt", meta_files=COURSE_META)
            assert result["status"] == "success"
            with open(os.path.join(base, "_order.json"), encoding="utf-8") as fh:
                assert json.load(fh)[""] == ["b.txt", "c.txt", "a.txt"]
            tree = walk_tree(base, {"": ["b.txt", "c.txt", "a.txt"]}, meta_files=COURSE_META)
            assert _names(tree) == ["b.txt", "c.txt", "a.txt"]

    def test_updown逐格移动(self):
        with tempfile.TemporaryDirectory() as base:
            _mk(base, files=("a.txt", "b.txt", "c.txt"))
            reorder_entry(base, "b.txt", "", "up", meta_files=COURSE_META)
            with open(os.path.join(base, "_order.json"), encoding="utf-8") as fh:
                assert json.load(fh)[""] == ["b.txt", "a.txt", "c.txt"]
            reorder_entry(base, "a.txt", "", "down", meta_files=COURSE_META)
            with open(os.path.join(base, "_order.json"), encoding="utf-8") as fh:
                assert json.load(fh)[""] == ["b.txt", "c.txt", "a.txt"]

    def test_上边界不写order文件(self):
        with tempfile.TemporaryDirectory() as base:
            _mk(base, files=("a.txt", "b.txt"))
            result = reorder_entry(base, "a.txt", "", "up", meta_files=COURSE_META)
            assert result["message"] == "已在边界，无需调整"
            assert not os.path.exists(os.path.join(base, "_order.json"))

    def test_swap到自身返回位置未变(self):
        with tempfile.TemporaryDirectory() as base:
            _mk(base, files=("a.txt", "b.txt"))
            result = reorder_entry(base, "a.txt", "", "swap", "a.txt", meta_files=COURSE_META)
            assert result["message"] == "位置未变"
            assert not os.path.exists(os.path.join(base, "_order.json"))

    def test_非法direction_400(self):
        with tempfile.TemporaryDirectory() as base:
            with pytest.raises(HTTPException) as e:
                reorder_entry(base, "a.txt", "", "sideways", meta_files=COURSE_META)
            assert e.value.status_code == 400

    def test_目录不存在_404(self):
        with tempfile.TemporaryDirectory() as base:
            with pytest.raises(HTTPException) as e:
                reorder_entry(base, "a.txt", "nope", "up", meta_files=COURSE_META)
            assert e.value.status_code == 404

    def test_条目缺失_404(self):
        with tempfile.TemporaryDirectory() as base:
            _mk(base, files=("a.txt",))
            with pytest.raises(HTTPException) as e:
                reorder_entry(base, "ghost.txt", "", "up", meta_files=COURSE_META)
            assert e.value.status_code == 404

    def test_swap目标缺失_404(self):
        with tempfile.TemporaryDirectory() as base:
            _mk(base, files=("a.txt", "b.txt"))
            with pytest.raises(HTTPException) as e:
                reorder_entry(base, "a.txt", "", "swap", "ghost.txt", meta_files=COURSE_META)
            assert e.value.status_code == 404

    def test_meta文件不进排序列表(self):
        with tempfile.TemporaryDirectory() as base:
            _mk(base, files=("course.json", "_order.json", "a.txt", "b.txt"))
            reorder_entry(base, "a.txt", "", "swap", "b.txt", meta_files=COURSE_META)
            with open(os.path.join(base, "_order.json"), encoding="utf-8") as fh:
                assert json.load(fh)[""] == ["b.txt", "a.txt"]

    def test_损坏order文件自动兑底(self):
        with tempfile.TemporaryDirectory() as base:
            _mk(base, files=("a.txt", "b.txt"))
            with open(os.path.join(base, "_order.json"), "w", encoding="utf-8") as fh:
                fh.write("{broken")
            result = reorder_entry(base, "a.txt", "", "down", meta_files=COURSE_META)
            assert result["status"] == "success"
            with open(os.path.join(base, "_order.json"), encoding="utf-8") as fh:
                assert json.load(fh)[""] == ["b.txt", "a.txt"]

    def test_子目录内reorder(self):
        with tempfile.TemporaryDirectory() as base:
            _mk(base, "sub", files=("sub/a.txt", "sub/b.txt"))
            reorder_entry(base, "a.txt", "sub", "swap", "b.txt", meta_files=COURSE_META)
            with open(os.path.join(base, "_order.json"), encoding="utf-8") as fh:
                assert json.load(fh)["sub"] == ["b.txt", "a.txt"]


# ============================================================
# 路由注册 — 防「前端有按钮、后端没接口」的 404 静默失败
# ============================================================

class Test路由已注册:

    def _paths(self, router):
        out = set()
        for r in router.routes:
            for m in (r.methods or []):
                out.add((m, r.path))
        return out

    def test_课程reorder路由存在(self):
        from routers.courses import router
        assert ("POST", "/api/courses/{course_id}/tree/reorder") in self._paths(router)

    def test_资料reorder路由存在(self):
        from routers.resources import router
        assert ("POST", "/api/resources/{resource_id}/tree/reorder") in self._paths(router)


# ============================================================
# HTTP 全链路 — TestClient（finally 清理，不残留 data/）
# ============================================================

@pytest.mark.parametrize("kind", ["resource", "course"])
def test_拖拽排序全链路(kind):
    """建数据 → 拖拽 → 重新读树验证顺序保持（原 bug：资料侧这一步 404 静默失败）
    注：首次 reorder 的基线是 os.listdir 顺序（Windows NTFS = 字母序），断言按此写。"""
    from fastapi.testclient import TestClient
    from main import app
    from utils import RESOURCE_BASE, _get_teacher_course_dir, _get_teacher_key, generate_token

    client = TestClient(app)
    username = "bft_tree"
    token = generate_token(username, "teacher")
    h = {"Authorization": f"Bearer {token}"}
    cleanup_dirs = []
    try:
        if kind == "resource":
            resp = client.post("/api/resources", headers=h, json={
                "name": "bft排序测试", "category": "reference",
                "description": "", "teacher": username,
            })
            assert resp.status_code == 200, resp.text
            rid = resp.json()["resource"]["id"]
            res_dir = os.path.join(RESOURCE_BASE, f"{_get_teacher_key(username)}_resource")
            cleanup_dirs.append(res_dir)
            # 建一个文件夹 + 上传三个文件（tree 读取是公开端点，无需登录）
            r = client.post(f"/api/resources/{rid}/tree/folder?path=zdir", headers=h)
            assert r.status_code == 200, r.text
            for fname in ("a.txt", "b.txt", "c.txt"):
                r = client.post(f"/api/resources/{rid}/tree/upload",
                                headers=h, files={"file": (fname, b"data", "text/plain")})
                assert r.status_code == 200, r.text
            # 现状断言：文件夹置顶 + path 字段 + 元数据隐藏
            tree = client.get(f"/api/resources/{rid}/tree").json()["tree"]
            assert _names(tree) == ["zdir", "a.txt", "b.txt", "c.txt"]
            assert all("path" in n for n in tree)
            assert not any(n["name"] in ("resource.json", "_order.json") for n in tree)
            # 拖拽：a.txt 拖到 c.txt 位置（原 bug：这里 404 静默失败）
            r = client.post(f"/api/resources/{rid}/tree/reorder", headers=h,
                            params={"name": "a.txt", "dir": "",
                                    "direction": "swap", "target": "c.txt"})
            assert r.status_code == 200, r.text
            # 重新读树（等价刷新）：顺序保持
            tree = client.get(f"/api/resources/{rid}/tree").json()["tree"]
            assert _names(tree) == ["b.txt", "c.txt", "a.txt", "zdir"]
            # 未授权的拖拽要被挡住（不是 404，是 401/403）
            r = client.post(f"/api/resources/{rid}/tree/reorder",
                            params={"name": "b.txt", "direction": "up"})
            assert r.status_code in (401, 403)
        else:
            teacher_dir = _get_teacher_course_dir(username)
            cleanup_dirs.append(teacher_dir)
            course_dir = os.path.join(teacher_dir, "bftdemo")
            os.makedirs(os.path.join(course_dir, "zdir"), exist_ok=True)
            for fname in ("a.txt", "b.txt", "c.txt"):
                with open(os.path.join(course_dir, fname), "w", encoding="utf-8") as fh:
                    fh.write("x")
            # 现状断言：课程交错排序 + 无 path 字段（改造前后行为不变）
            tree = client.get("/api/courses/bftdemo/tree", headers=h).json()["tree"]
            assert _names(tree) == ["a.txt", "b.txt", "c.txt", "zdir"]
            assert "path" not in tree[0]
            # 拖拽：a.txt 拖到 c.txt 位置
            r = client.post("/api/courses/bftdemo/tree/reorder", headers=h,
                            params={"name": "a.txt", "dir": "",
                                    "direction": "swap", "target": "c.txt"})
            assert r.status_code == 200, r.text
            tree = client.get("/api/courses/bftdemo/tree", headers=h).json()["tree"]
            assert _names(tree) == ["b.txt", "c.txt", "a.txt", "zdir"]
    finally:
        for d in cleanup_dirs:
            shutil.rmtree(d, ignore_errors=True)


