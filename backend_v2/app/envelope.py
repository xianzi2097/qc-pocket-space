"""统一信封：保证每个接口（成功或失败）返回格式都长一个样

格式（规范 §1）：
  成功：HTTP 200 + {"code": 0, "msg": "ok", "data": ...}
  失败：HTTP 4xx/5xx + {"code": <错误码>, "msg": <中文说明>, "data": null}

本文件提供：
  ① 错误码常量     E_ 开头，4 位分域：1xxx 通用 / 2xxx 认证 / 3xxx 账号 /
                  4xxx 班级 / 5xxx 课程 / 6xxx 资料 / 7xxx 站内信 / 8xxx 笔记 /
                  9xxx 服务端（对照规范 §1.3 表格）
  ② ApiError       业务异常类：接口里 raise ApiError(code, "中文", 状态码) 即可，
                  至于怎么返回、返回什么格式，本文件全包了
  ③ ok(data)       成功信封快捷方式：return ok({...})
  ④ install_error_handlers(app)  在 main.py 装配时调用一次，挂 4 个兜底处理器：
                  ApiError → 业务错误 | RequestValidationError → 参数 400 |
                  HTTPException（含路由 404）→ 映射 | Exception → 500 服务繁忙

记忆点：接口代码永远不用自己拼错误 JSON，只管 raise。
"""
import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger("codenova.v2")

OK = 0

# ---- 1xxx 通用 ----
E_PARAM_MISSING = 1001
E_PARAM_INVALID = 1002
E_NOT_FOUND = 1004
E_CONFLICT = 1009
E_FILE_TOO_LARGE = 1113

# ---- 2xxx 认证与会话 ----
E_BAD_CREDENTIALS = 2001
E_NOT_LOGGED_IN = 2002
E_TOKEN_EXPIRED = 2003
E_ACCOUNT_DISABLED = 2004
E_FORBIDDEN = 2005
E_RATE_LIMITED = 2029

# ---- 3xxx 账号 ----
E_USERNAME_TAKEN = 3001
E_ROLE_NOT_ALLOWED = 3003
E_ACCOUNT_NOT_FOUND = 3004
E_CANNOT_DELETE_SELF = 3010

# ---- 4xxx 班级与学生 ----
E_CLASS_NAME_TAKEN = 4001
E_CLASS_NOT_FOUND = 4004
E_STUDENT_ALREADY_IN_CLASS = 4009

# ---- 5xxx 课程与课件 ----
E_COURSE_NAME_TAKEN = 5001
E_COURSE_NOT_FOUND = 5004
E_PATH_INVALID = 5010
E_PATH_EXISTS = 5011

# ---- 6xxx 资料与软件 ----
E_RESOURCE_NAME_TAKEN = 6001
E_RESOURCE_NOT_FOUND = 6004

# ---- 7xxx 站内信 ----
E_MESSAGE_FORBIDDEN = 7003
E_MESSAGE_NOT_FOUND = 7004
E_FOLDER_PROTECTED = 7010

# ---- 8xxx 笔记 ----
E_NOTE_NOT_FOUND = 8004
E_NOTEBOOK_TAKEN = 8009

# ---- 9xxx 服务端 ----
E_INTERNAL = 9000
E_STORAGE_FULL = 9001      # 磁盘空间不足 / 写文件失败（HTTP 507）


class ApiError(Exception):
    """业务异常：携带信封所需的 code/msg/HTTP 状态"""

    def __init__(self, code: int, msg: str, http_status: int = 400):
        super().__init__(msg)
        self.code = code
        self.msg = msg
        self.http_status = http_status


def ok(data=None, msg: str = "ok") -> dict:
    return {"code": OK, "msg": msg, "data": data}


def _fail(code: int, msg: str, http_status: int) -> JSONResponse:
    return JSONResponse(status_code=http_status, content={"code": code, "msg": msg, "data": None})


_STATUS_TO_CODE = {
    400: (E_PARAM_INVALID, "参数错误"),
    401: (E_NOT_LOGGED_IN, "未登录或登录已过期"),
    403: (E_FORBIDDEN, "没有权限"),
    404: (E_NOT_FOUND, "资源不存在"),
    409: (E_CONFLICT, "资源冲突"),
    413: (E_FILE_TOO_LARGE, "文件过大"),
    429: (E_RATE_LIMITED, "操作过于频繁"),
}

_VALIDATION_CODES = {
    "missing": E_PARAM_MISSING,
    "string_type": E_PARAM_INVALID,
    "string_too_short": E_PARAM_INVALID,
    "string_too_long": E_PARAM_INVALID,
    "value_error": E_PARAM_INVALID,
    "json_invalid": E_PARAM_INVALID,
    "model_attributes_type": E_PARAM_INVALID,
}


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError):
        return _fail(exc.code, exc.msg, exc.http_status)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
        code = _VALIDATION_CODES.get(first.get("type", ""), E_PARAM_INVALID)
        if code == E_PARAM_MISSING:
            msg = f"缺少参数：{loc or first.get('msg', '')}"
        else:
            msg = f"参数格式错误：{loc or first.get('msg', '')}"
        logger.info("validation error: %s", exc.errors())
        return _fail(code, msg, 400)

    # 路由层 404/405 抛的是 starlette 基类异常，FastAPI 的 HTTPException 是其子类，两者都要挂
    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_request: Request, exc: StarletteHTTPException):
        fallback = (E_PARAM_INVALID, "请求错误") if exc.status_code < 500 else (E_INTERNAL, "服务繁忙，请稍后再试")
        code, default_msg = _STATUS_TO_CODE.get(exc.status_code, fallback)
        detail = exc.detail
        msg = detail if isinstance(detail, str) and detail else default_msg
        return _fail(code, msg, exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(_request: Request, exc: Exception):
        logger.exception("unhandled error: %r", exc)
        return _fail(E_INTERNAL, "服务繁忙，请稍后再试", 500)
