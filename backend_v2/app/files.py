"""路径安全：所有来自 URL/表单的路径都必须先过这里，防目录穿越攻击

攻击样子：下载接口传 ../../../../../windows/win.ini → 跳出课程目录读系统文件

本文件提供：
  validate_rel_path(path)   校验并清洗相对路径，返回 "a/b/c" 形式（"" = 根目录）
                            不合法直接报错 5010
  safe_join(root, rel)      把路径接到指定目录下，并用真实路径二次确认没逃出去
                            （防符号链接）
  valid_new_name(name)      单个名称的校验：新建文件夹/重命名时用

拦截的五类：
  ① .. 穿越          ② 绝对路径 /etc      ③ 盘符 C:\
  ④ 非法字符 < > : " | ? * 和控制字符     ⑤ 超长路径/名称

规范 §1.6、红线 C-22；M2 文件树 service 是唯一调用入口。
"""
from pathlib import Path, PurePosixPath

from .envelope import E_PATH_INVALID, ApiError

_INVALID_CHARS = set('<>:"|?*')
_MAX_SEGMENT_LEN = 200
_MAX_PATH_LEN = 500


def validate_rel_path(path: str | None) -> str:
    """校验并规范化相对路径；非法即抛 5010。返回 POSIX 风格相对路径（"" 表示根）"""
    if path is None:
        return ""
    raw = str(path).replace("\\", "/").strip()
    if raw.startswith("/") or (len(raw) >= 2 and raw[1] == ":"):
        raise ApiError(E_PATH_INVALID, "路径非法", 400)

    parts: list[str] = []
    for segment in raw.split("/"):
        if segment in ("", "."):
            continue
        if segment == "..":
            raise ApiError(E_PATH_INVALID, "路径非法", 400)
        if len(segment) > _MAX_SEGMENT_LEN:
            raise ApiError(E_PATH_INVALID, "路径过长", 400)
        if any(ch in _INVALID_CHARS or ord(ch) < 32 for ch in segment):
            raise ApiError(E_PATH_INVALID, "路径含非法字符", 400)
        if segment.endswith((".", " ")) or segment.startswith(" "):
            raise ApiError(E_PATH_INVALID, "路径非法", 400)
        parts.append(segment)

    result = "/".join(parts)
    if len(result) > _MAX_PATH_LEN:
        raise ApiError(E_PATH_INVALID, "路径过长", 400)
    return result


def safe_join(root: Path, rel: str | None) -> Path:
    """拼接并确保结果仍在 root 之内（realpath 兜底，防符号链接逃逸）"""
    clean = validate_rel_path(rel)
    root_resolved = root.resolve()
    target = (root_resolved / PurePosixPath(clean)).resolve() if clean else root_resolved
    if target != root_resolved and root_resolved not in target.parents:
        raise ApiError(E_PATH_INVALID, "路径非法", 400)
    return target


def valid_new_name(name: str) -> str:
    """新建/重命名的单段名称校验，返回清洗后的名称"""
    if not isinstance(name, str) or not name.strip():
        raise ApiError(E_PATH_INVALID, "名称不能为空", 400)
    name = name.strip()
    if name in (".", ".."):
        raise ApiError(E_PATH_INVALID, "名称非法", 400)
    if len(name) > _MAX_SEGMENT_LEN:
        raise ApiError(E_PATH_INVALID, "名称过长", 400)
    if any(ch in _INVALID_CHARS or ord(ch) < 32 for ch in name):
        raise ApiError(E_PATH_INVALID, "名称含非法字符", 400)
    return name
