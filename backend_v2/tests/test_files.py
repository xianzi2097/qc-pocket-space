"""验收单：路径安全（app/files.py）

覆盖：
  · 合法路径放行并清洗（重复斜杠、反斜杠转正斜杠、中文名）
  · 攻击路径全拦：.. 穿越、绝对路径、盘符、非法字符、控制字符、超长
  · safe_join 拼接后仍在目标目录内，逃逸即报 5010
  · 新建/重命名名称校验（空、.、..、含非法字符）
"""
from pathlib import Path

import pytest

from app.envelope import ApiError
from app.files import safe_join, valid_new_name, validate_rel_path

VALID = [
    ("", ""),
    (None, ""),
    ("lesson1/main.sb3", "lesson1/main.sb3"),
    ("a//b/./c", "a/b/c"),
    ("a\\b", "a/b"),
    ("中文名/作品.sb3", "中文名/作品.sb3"),
]

INVALID = [
    "../etc/passwd",
    "a/../../etc",
    "/etc/passwd",
    "C:/windows/system32",
    "a/b/c:",
    "a/<script>",
    "a/b\x00c",
]


@pytest.mark.parametrize("raw,expected", VALID)
def test_valid_paths_normalized(raw, expected):
    assert validate_rel_path(raw) == expected


@pytest.mark.parametrize("raw", INVALID)
def test_invalid_paths_rejected(raw):
    with pytest.raises(ApiError) as exc:
        validate_rel_path(raw)
    assert exc.value.code == 5010


def test_safe_join_stays_inside_root(tmp_path: Path):
    root = tmp_path / "course"
    root.mkdir()
    assert safe_join(root, "a/b") == (root / "a" / "b").resolve()
    assert safe_join(root, "") == root.resolve()


def test_safe_join_blocks_escape(tmp_path: Path):
    root = tmp_path / "course"
    root.mkdir()
    with pytest.raises(ApiError):
        safe_join(root, "../outside.txt")


def test_valid_new_name():
    assert valid_new_name("  课件一  ") == "课件一"
    for bad in ["", "   ", "..", ".", "a<b", "a" * 300, "a\tb"]:
        with pytest.raises(ApiError):
            valid_new_name(bad)

def test_ensure_tables_rebuilds_legacy_file_order():
    """老库守卫：file_order 残留 scope/scope_id 旧骨架 → init_db 删表重建（防目录列表 500）"""
    from app.db import engine, init_db

    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS file_order")
        conn.exec_driver_sql(
            "CREATE TABLE file_order (id INTEGER PRIMARY KEY, scope TEXT, scope_id TEXT,"
            " parent_path TEXT, name TEXT, position INTEGER)"
        )
    init_db()
    with engine.connect() as conn:
        cols = {r[1] for r in conn.exec_driver_sql("PRAGMA table_info(file_order)")}
    assert "asset_id" in cols and "scope" not in cols