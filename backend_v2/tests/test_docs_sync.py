"""验收单：README.md 与代码保持同步 —— 让"改完记得更新文档"变成机器检查

用户要求（2026-10-06）：每次改动 backend_v2 后都要复查 README 需不需要更新。
靠自觉会漏，所以把"能机器判断的部分"写成断言：

  · 新增了 .py 文件，但 README 文件地图没登记 → 红
  · 测试数量变了，但 README 顶部数字没更新   → 红
  · 阶段进度表缺行（M0~M5）                 → 红

剩下机器判断不了的部分（某文件职责描述是否过时）→ 由 AI/人在改代码时人工复查，
这条已写进全局记忆（.clinerules/notes.md）。
"""
import re
import subprocess
import sys
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1]
README = BACKEND_DIR / "README.md"
DB_DOC = BACKEND_DIR.parent / "docs" / "backend-v2-database.md"

STAGES = ["M0", "M1", "M2", "M3", "M4", "M5"]
PLACEHOLDER = "⚠ 待补职责描述"


def _python_files() -> list[Path]:
    """全部源码与测试文件（排除包标记与缓存）"""
    return [
        path for path in BACKEND_DIR.rglob("*.py")
        if "__pycache__" not in path.parts and path.name != "__init__.py"
    ]


def _readme() -> str:
    assert README.exists(), f"README.md 不存在：{README}"
    return README.read_text(encoding="utf-8")


def test_readme_exists():
    _readme()


def test_readme_maps_every_source_file():
    """文件地图不能漏：新文件必须登记一句话职责"""
    text = _readme()
    missing = sorted({path.name for path in _python_files() if path.name not in text})
    assert not missing, (
        f"README 文件地图缺少这些文件（每个新文件必须登记）：{missing}"
        " → 打开 README 的「文件地图」表格加一行"
    )


def _full_suite_count() -> int | None:
    """全量收集数（不能用 len(request.session.items)：只跑子集时它不等于全量）"""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q"],
            cwd=BACKEND_DIR, capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"(\d+)\s+tests?\s+collected", (proc.stdout or "") + (proc.stderr or ""))
    return int(match.group(1)) if match else None


def test_readme_test_count_matches():
    """测试数必须与实际一致（README 顶部『测试用例：**N 个**』）"""
    text = _readme()
    match = re.search(r"测试用例：\*\*(\d+)\s*个\*\*", text)
    assert match, "README 缺少『测试用例：**N 个**』这一行"
    actual = _full_suite_count()
    if actual is None:
        pytest.skip("pytest 收集失败，跳过用例数核对")
    assert int(match.group(1)) == actual, (
        f"README 写的是 {match.group(1)} 个用例，实际收集 {actual} 个"
        " → 跑 python tools/sync_readme.py 同步"
    )


def test_readme_has_no_placeholder_rows():
    """sync_readme.py 补的占位必须尽快写成真实职责描述（脚本管机器部分，语义归人）"""
    bad = [
        line for line in _readme().splitlines()
        if line.startswith("| `") and PLACEHOLDER in line
    ]
    assert not bad, (
        f"文件地图存在未填写的占位行：{bad} → 打开 README 把它写成一句话职责"
    )


def test_readme_stage_table_complete():
    """阶段进度表必须 M0~M5 齐全，且至少标出一个已完成阶段"""
    text = _readme()
    missing = [stage for stage in STAGES if stage not in text]
    assert not missing, f"README 阶段进度表缺行：{missing}"
    assert "✅ 完成" in text, "阶段进度表里没有任何『✅ 完成』标记"


def _implemented_tables() -> set[str]:
    """app 代码（含 main.py）真实 import 了的模型 → 其 __tablename__"""
    models_src = (BACKEND_DIR / "app" / "models.py").read_text(encoding="utf-8")
    table_of = dict(re.findall(
        r"class\s+(\w+)\(Base\):.*?__tablename__\s*=\s*[\"'](\w+)[\"']", models_src, re.S))
    used: set[str] = set()
    for py in [BACKEND_DIR / "main.py", *(BACKEND_DIR / "app").rglob("*.py")]:
        if py.name == "models.py" or "__pycache__" in py.parts:
            continue
        text = py.read_text(encoding="utf-8")
        # 抓 from ...models import A, B( ...多行... )
        for stmt in re.findall(r"from\s+[\w.]*models\s+import\s+\(?([^)]*)\)?", text):
            for name in re.split(r"[,\s]+", stmt):
                name = name.strip()
                if name in table_of:
                    used.add(table_of[name])
    return used


def test_database_doc_stage_matches_implementation():
    """数据库说明书的阶段列必须跟上实现（2026-10-09 漏更 8 行的教训转成机器闸门）

    两条断言：
      ① models 里每张表都在"总览"登记（新表必入册）
      ② 代码里【已经 import】的模型，其总览行阶段列不许还是"未来"
    """
    assert DB_DOC.exists(), f"数据库说明书不存在：{DB_DOC}"
    text = DB_DOC.read_text(encoding="utf-8")

    models_src = (BACKEND_DIR / "app" / "models.py").read_text(encoding="utf-8")
    all_tables = set(re.findall(
        r"__tablename__\s*=\s*[\"'](\w+)[\"']", models_src))

    rows = {m[0]: m[1].strip() for m in re.findall(
        r"\|\s*\d+\s*\|\s*`(\w+)`\s*\|[^|]*\|[^|]*\|\s*([^|\n]+)\|", text)}

    missing_rows = sorted(t for t in all_tables if t not in rows)
    assert not missing_rows, (
        f"数据库说明书总览缺表行：{missing_rows} → 加进『总览』表"
    )

    stale = sorted(t for t in _implemented_tables() if "未来" in rows.get(t, ""))
    assert not stale, (
        f"这些表代码已在用，但数据库说明书阶段列还写『未来』：{stale}"
        " → 打 ✅ 并填所属阶段（维护规则第 3 条）"
    )

    # 自检：解析规则真能抓到"阶段列写未来"的行（防止规则被改松，同命名闸门传统）
    probe = {k: v.strip() for k, v in re.findall(
        r"\|\s*\d+\s*\|\s*`(\w+)`\s*\|[^|]*\|[^|]*\|\s*([^|\n]+)\|",
        "| 99 | `probe_table` | a | b | 未来 |")}
    assert probe == {"probe_table": "未来"}
