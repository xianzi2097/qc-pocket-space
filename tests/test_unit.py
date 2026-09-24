"""
单元测试 — 纯函数层
====================
不经过 HTTP、不碰数据库：直接调用 utils / auth 里的纯函数。
（import 时 models 建引擎建表属进程级副作用，由 conftest 统一处理，测试本体不触库）

运行方式：
  cd C:\\Arin - cabin\\CodeNova-Workspace\\qc-pocket-space\\tests
  venv\\Scripts\\python.exe -m pytest test_unit.py -v
"""
import json
import os
import sys
import tempfile
import threading

import pytest
from fastapi import HTTPException

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, BACKEND_DIR)

from utils import (
    _atomic_save, _get_teacher_key, _read_order_file, _safe_path,
    _tmp_name, _valid_new_name, hash_password, verify_password,
)
from routers.auth import _get_lockout_seconds


# ============================================================
# 🔒 路径安全 — _safe_path 是外部输入拼路径的唯一安全出口
# ============================================================

class Test路径安全:

    def test_正常子路径放行(self):
        with tempfile.TemporaryDirectory() as base:
            fp = _safe_path(base, "sessions", "一班", "2026-09.json")
            assert fp == os.path.normpath(os.path.join(base, "sessions", "一班", "2026-09.json"))

    def test_上级穿越被拦截(self):
        with tempfile.TemporaryDirectory() as base:
            with pytest.raises(HTTPException) as e:
                _safe_path(base, "..")
            assert e.value.status_code == 403

    def test_多级穿越被拦截(self):
        with tempfile.TemporaryDirectory() as base:
            with pytest.raises(HTTPException) as e:
                _safe_path(base, "a", "..", "..", "evil")
            assert e.value.status_code == 403

    def test_含点点的普通文件名不误伤(self):
        """文件名里带 .. 但不构成穿越 → 应放行"""
        with tempfile.TemporaryDirectory() as base:
            assert os.path.basename(_safe_path(base, "a..b.json")) == "a..b.json"


# ============================================================
# 🏷️ 名字校验 — _valid_new_name
# ============================================================

class Test名字校验:

    def test_空名拒绝(self):
        with pytest.raises(HTTPException) as e:
            _valid_new_name("")
        assert e.value.status_code == 400

    def test_正反斜杠拒绝(self):
        for bad in ("a/b", "a\\b"):
            with pytest.raises(HTTPException) as e:
                _valid_new_name(bad)
            assert e.value.status_code == 400

    def test_去首尾空格(self):
        assert _valid_new_name("  一班  ") == "一班"


# ============================================================
# 🔑 密码哈希 — 随机盐 sha256
# ============================================================

class Test密码哈希:

    def test_往返验证通过(self):
        assert verify_password("pw123", hash_password("pw123"))

    def test_错误密码拒绝(self):
        assert not verify_password("pw456", hash_password("pw123"))

    def test_哈希不含明文且带格式前缀(self):
        stored = hash_password("pw123")
        assert "pw123" not in stored
        assert stored.startswith("sha256$")

    def test_同密码两次哈希不同(self):
        """随机盐：同密码两次结果必须不同"""
        assert hash_password("x") != hash_password("x")

    @pytest.mark.parametrize("stored", ["", "plaintext", "sha256$onlytwo"])
    def test_损坏的存储格式一律拒绝(self, stored):
        assert not verify_password("x", stored)


# ============================================================
# 🚫 登录梯度封禁 — _get_lockout_seconds
# ============================================================

class Test登录封禁梯度:

    @pytest.mark.parametrize("count,expect_sec", [
        (0, 0), (4, 0),
        (5, 30), (9, 30),
        (10, 300), (14, 300),
        (15, 1800), (19, 1800),
        (20, 7200), (99, 7200),
    ])
    def test_封禁秒数(self, count, expect_sec):
        assert _get_lockout_seconds(count) == expect_sec


# ============================================================
# 💾 原子写 — _tmp_name / _atomic_save
# ============================================================

class Test原子写:

    def test_临时文件名含进程线程标识(self):
        name = _tmp_name("/x/y.json")
        assert name.startswith("/x/y.json.")
        assert name.endswith(".tmp")
        assert str(os.getpid()) in name

    def test_不同线程临时名不同(self):
        """pid+tid 后缀：两线程绝不抢同一个 .tmp"""
        names = [_tmp_name("/x/y.json")]

        def grab():
            names.append(_tmp_name("/x/y.json"))

        t = threading.Thread(target=grab)
        t.start()
        t.join()
        assert len(set(names)) == 2

    def test_写入后内容完整且无临时残留(self):
        with tempfile.TemporaryDirectory() as d:
            fp = os.path.join(d, "s.json")
            _atomic_save(fp, {"a": 1, "中文": "值"})
            with open(fp, encoding="utf-8") as f:
                assert json.load(f) == {"a": 1, "中文": "值"}
            assert [x for x in os.listdir(d) if x.endswith(".tmp")] == []

    def test_并发写同文件不损坏(self):
        """两线程反复写同一文件：最终必须是完整 JSON（其一完整胜出），无 .tmp 残留"""
        with tempfile.TemporaryDirectory() as d:
            fp = os.path.join(d, "s.json")

            def w(v):
                for _ in range(5):
                    _atomic_save(fp, {"who": v, "n": [1, 2, 3]})

            ts = [threading.Thread(target=w, args=(v,)) for v in ("a", "b")]
            for t in ts:
                t.start()
            for t in ts:
                t.join()
            with open(fp, encoding="utf-8") as f:
                data = json.load(f)  # 解不开 = 文件损坏 = 本断言红
            assert data["who"] in ("a", "b")
            assert not [x for x in os.listdir(d) if x.endswith(".tmp")]


# ============================================================
# 📋 课程排序文件 — _read_order_file 容错
# ============================================================

class Test课程排序文件:

    def test_文件缺失返回空字典(self):
        with tempfile.TemporaryDirectory() as d:
            assert _read_order_file(d) == {}

    def test_文件损坏返回空字典不抛异常(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "_order.json"), "w", encoding="utf-8") as f:
                f.write("{broken")
            assert _read_order_file(d) == {}

    def test_正常内容原样返回(self):
        with tempfile.TemporaryDirectory() as d:
            _atomic_save(os.path.join(d, "_order.json"), {"": ["b.md", "a.md"]})
            assert _read_order_file(d) == {"": ["b.md", "a.md"]}


# ============================================================
# 📁 课程目录键 — _get_teacher_key
# ============================================================

class Test课程目录键:

    @pytest.mark.parametrize("raw,expect", [
        ("李老师(t)", "李老师"),
        ("my teacher (t)", "myteacher"),
        ("plain", "plain"),
    ])
    def test_清洗规则(self, raw, expect):
        assert _get_teacher_key(raw) == expect
