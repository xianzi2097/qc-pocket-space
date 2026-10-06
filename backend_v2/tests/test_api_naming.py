"""验收单：接口命名与规范一致性（规范 §1.6 / §1.7 / §3）—— 把关交给机器

为什么存在：2026-10-06 审出 5 处命名漂移，靠人眼审不可靠。
本文件把"能写成断言的规矩"全部自动化，任何新增路由只要
  ① 段名不在白名单 → 红（逼你先在规范 §3 登记、§1.7 写理由）
  ② 命名不合 §1.6  → 红（路径 camelCase / 查询 snake_case / 全小写连字符）
  ③ 域在规范里查不到 → 红（代码与图纸不同步）
就会在这里失败，到不了人工评审那一步。

新增接口的正确顺序：改规范 §3 → 在下方白名单加一行 → 写实现 → 本文件转绿
"""
import re
from pathlib import Path

from fastapi.routing import APIRoute

from main import app

SPEC_PATH = Path(__file__).resolve().parents[2] / "docs" / "backend-v2-api-spec.md"

# 资源段（复数名词）—— 规范 §3 已登记的资源
RESOURCE_SEGMENTS = {
    "auth", "accounts", "avatar", "avatars", "classes", "students", "courses",
    "lessons", "files", "resources", "messages", "message-folders", "notebooks",
    "notes", "class-records", "settings", "trash", "health",
}

# 动作段（规范 §1.7 登记过理由的一次性动作 / 语义子路径）
ACTION_SEGMENTS = {
    "login", "logout", "me", "password", "avatar", "presets", "preset",
    "upload", "mkdir", "rename", "order", "batch",
    "publish", "publish-status", "restore", "download",
    "unread-count", "recent-opens",
}

ALLOWED_SEGMENTS = RESOURCE_SEGMENTS | ACTION_SEGMENTS

# 段字符集：全小写，多词用连字符（禁下划线、禁大写、禁中文）
SEGMENT_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
# 路径参数：camelCase
PATH_PARAM_RE = re.compile(r"\{([A-Za-z0-9_]+)(?::[^}]*)?\}")
CAMEL_RE = re.compile(r"^[a-z][A-Za-z0-9]*$")
# 查询参数：snake_case
SNAKE_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def _business_routes():
    """只取我们自己 router 挂的接口（APIRoute），排除 /docs、/openapi.json 等框架路由"""
    return [
        route for route in app.routes
        if isinstance(route, APIRoute)
        and route.methods
        and route.path
        and route.include_in_schema
    ]


def _segments(path: str) -> list[str]:
    """去掉 /api 前缀与路径参数，返回纯段名"""
    parts = [p for p in path.split("/") if p and p != "api"]
    cleaned = []
    for part in parts:
        part = PATH_PARAM_RE.sub("{param}", part)
        if part == "{param}":
            continue
        cleaned.append(part)
    return cleaned


def test_routes_live_under_api_prefix():
    stray = [r.path for r in _business_routes() if not r.path.startswith("/api/")]
    assert not stray, f"业务路由必须在 /api/ 下（规范 §1.6）：{stray}"


def test_every_segment_is_registered():
    """新段必须先在规范 §3 登记 + 本文件白名单加一行，否则这里红"""
    illegal = {}
    for route in _business_routes():
        for segment in _segments(route.path):
            if segment not in ALLOWED_SEGMENTS:
                illegal.setdefault(segment, []).append(route.path)
    assert not illegal, (
        "未登记的路径段（规范 §1.6 命名 / §1.7 动作端点登记制）："
        f"{ {seg: sorted(set(paths)) for seg, paths in illegal.items()} }"
        " → 先改规范 §3，再在本文件白名单加一行"
    )


def test_segments_are_lowercase_hyphenated():
    bad = [
        f"{route.path} ← 段 {seg!r}"
        for route in _business_routes()
        for seg in _segments(route.path)
        if not SEGMENT_RE.match(seg)
    ]
    assert not bad, f"段名必须全小写、多词用连字符（禁下划线/大写）：{bad}"


def test_path_params_are_camel_case():
    bad = [
        f"{route.path} ← {{{param}}}"
        for route in _business_routes()
        for param in PATH_PARAM_RE.findall(route.path)
        if not CAMEL_RE.match(param)
    ]
    assert not bad, f"路径参数必须 camelCase（规范 §1.6）：{bad}"


def test_query_params_are_snake_case():
    schema = app.openapi()
    bad = []
    for path, item in schema["paths"].items():
        if not path.startswith("/api/"):
            continue
        for operation in item.values():
            if not isinstance(operation, dict):
                continue
            for param in operation.get("parameters", []):
                if param.get("in") == "query" and not SNAKE_RE.match(param["name"]):
                    bad.append(f"{path} ← ?{param['name']}=")
    assert not bad, f"查询参数必须 snake_case（规范 §1.6）：{bad}"


def test_route_domains_exist_in_spec():
    """代码与图纸同步：每个接口域都能在规范 §3 里查到"""
    spec = SPEC_PATH.read_text(encoding="utf-8")
    missing = sorted({
        "/".join(route.path.split("/")[:3])
        for route in _business_routes()
        if "/".join(route.path.split("/")[:3]) not in spec
    })
    assert not missing, (
        f"这些接口域在规范 {SPEC_PATH.name} 里找不到（图纸先行）：{missing}"
        " → 先在 §3 加行，再写实现"
    )


def test_checker_itself_catches_bad_names():
    """自检：证明这些规则真能拦住坏命名（防止规则被改松）"""
    assert not SEGMENT_RE.match("class_records"), "下划线段必须被拒"
    assert not SEGMENT_RE.match("ClassRecords"), "大写段必须被拒"
    assert not CAMEL_RE.match("class_id"), "路径参数下划线必须被拒"
    assert _segments("/api/classes/{classId}/student_list") == ["classes", "student_list"]
    assert "student_list" not in ALLOWED_SEGMENTS, "未登记段必须不在白名单"

    """已知的语义铁律：改密必须 PATCH、创建类必须 POST、读必须 GET"""
    method_by_path = {r.path: m for r in _business_routes() for m in r.methods}
    rules = {
        ("/api/auth/password", "PATCH"): "改密是更新，不是创建（2026-10-06 整改）",
        ("/api/auth/login", "POST"): "登录是动作端点，惯例 POST",
        ("/api/auth/logout", "POST"): "登出是动作端点，惯例 POST",
        ("/api/auth/me", "GET"): "读取当前账号",
        ("/api/health", "GET"): "探活只读",
    }
    violations = [
        f"期望 {path} 为 {method}：{why}"
        for (path, method), why in rules.items()
        if method_by_path.get(path) != method
    ]
    assert not violations, violations
