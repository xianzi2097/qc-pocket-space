"""机械同步 README.md 的两处易过期字段（测试用例数 + 文件地图缺项）

背景：用户要求"每次 build 或改动都顺便更新 README"（2026-10-06）。
分工——机器只改它算得出来的，语义判断留给人/AI：

  本脚本负责：
    ① 顶部『测试用例：**N 个**』按 pytest 实际收集数改写
    ② 文件地图里缺失的 .py 文件补一行占位（⚠ 待补职责描述）
  人工 / AI 负责：
    职责描述是否过时、阶段进度、"本阶段做了什么"

质量闸门：tests/test_docs_sync.py 既检查同步结果，也**禁止残留 ⚠ 占位**
（脚本补了行但没人写描述 → 测试照样红）。

用法：
    python tools/sync_readme.py            # 任意 cwd
    hooks/pre-commit                       # backend_v2 有变更时自动调用
"""
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
README = BACKEND_DIR / "README.md"

COUNT_RE = re.compile(r"^测试用例：\*\*\d+\s*个\*\*.*$", re.M)
PLACEHOLDER = "⚠ 待补职责描述"

TESTS_HEADER = "| 文件 | 验什么 | 用例数 |"
TESTS_ROW = "| `{name}` | {placeholder} | — |"
APP_HEADER = "| 文件 | 一句话职责 | 阶段 |"
APP_ROW = "| `{name}` | {placeholder} | 待补 |"


def collect_test_count() -> int | None:
    """用 pytest 实际收集数（与 test_docs_sync 的断言同源）"""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "tests", "--collect-only", "-q"],
            cwd=BACKEND_DIR, capture_output=True, text=True, timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (proc.stdout or "") + (proc.stderr or "")
    match = re.search(r"(\d+)\s+tests?\s+collected", output)
    return int(match.group(1)) if match else None


def update_count(text: str, count: int) -> tuple[str, bool]:
    match = COUNT_RE.search(text)
    if not match:
        return text, False
    new_line = f"测试用例：**{count} 个**（最近自动同步：{date.today().isoformat()}）"
    if match.group(0) == new_line:
        return text, False
    return text[:match.start()] + new_line + text[match.end():], True


def insert_rows(lines: list[str], pending: list[tuple[str, bool]]) -> list[str]:
    """pending: (文件名, 是否测试文件)；测试文件进 tests 表，其余进 app 表"""

    def find_table(header: str) -> tuple[int, int] | None:
        idxs = [i for i, line in enumerate(lines) if line.startswith(header)]
        if not idxs:
            return None
        start = idxs[-1] if header == APP_HEADER else idxs[0]
        end = start + 1
        while end < len(lines) and lines[end].startswith("|"):
            end += 1
        return start, end

    for name, is_test in pending:
        header, row_tmpl = (TESTS_HEADER, TESTS_ROW) if is_test else (APP_HEADER, APP_ROW)
        found = find_table(header)
        if not found:
            print(f"  ⚠ 找不到表格（{header}），跳过：{name}")
            continue
        _, end = found
        lines.insert(end, row_tmpl.format(name=name, placeholder=PLACEHOLDER))
        print(f"  + 文件地图补行：{name}")
    return lines


def main() -> int:
    if not README.exists():
        print(f"README 不存在：{README}")
        return 1

    original = README.read_text(encoding="utf-8")
    text = original

    # ① 测试数
    count = collect_test_count()
    if count is None:
        print("  ⚠ pytest 收集失败，跳过测试数同步")
    else:
        text, changed = update_count(text, count)
        print(f"  {'~ 测试数已更新为 ' + str(count) if changed else '· 测试数已是 ' + str(count)}")

    # ② 文件地图缺项（排除包标记与缓存）
    files = sorted(
        path for path in BACKEND_DIR.rglob("*.py")
        if "__pycache__" not in path.parts
        and ".venv" not in path.parts
        and path.name != "__init__.py"
    )
    pending = [
        (path.name, path.relative_to(BACKEND_DIR).parts[0] == "tests")
        for path in files
        if path.name not in text
    ]
    if pending:
        lines = text.splitlines()
        lines = insert_rows(lines, pending)
        text = "\n".join(lines) + ("\n" if original.endswith("\n") else "")

    if text != original:
        README.write_text(text, encoding="utf-8")
        print(f"✅ README.md 已同步：{README}")
    else:
        print("· README.md 无需改动")
    return 0


if __name__ == "__main__":
    sys.exit(main())
