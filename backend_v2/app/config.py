"""全局配置：所有"改一次、全局生效"的参数都只放这一个文件

┌──────────────────────┬────────────────────────────┬──────────────────────────┐
│ 配置项                │ 当前值                     │ 用途                     │
├──────────────────────┼────────────────────────────┼──────────────────────────┤
│ DATA_ROOT            │ <工作区>/data_v2          │ V2 数据根目录，           │
│                      │                            │ 与 V1 的 data/ 平级隔离    │
│ DB_PATH              │ <DATA_ROOT>/codenova_v2.db │ SQLite 数据库文件          │
│ TOKEN_TTL_DAYS       │ 7 天                       │ 登录 token 有效期          │
│ TOKEN_TTL_SECONDS    │ 604800                     │ 同上，给前端的秒数          │
│ MIN_PASSWORD_LEN     │ 6                          │ 密码最小长度               │
│ DEFAULT_ACCOUNT_     │ 123456                     │ 批量注册的默认密码          │
│   PASSWORD           │                            │                          │
│ PRESET_AVATAR_DIR    │ <backend_v2>/assets/       │ 预设头像（随代码走，        │
│                      │   avatars                  │  不进数据目录）            │
│ DEFAULT_PRESET_      │ preset/avatar_01.svg       │ 没传过头像时的回落值        │
│   AVATAR             │                            │                          │
│ MAX_USERNAME_LEN     │ 64                         │ 用户名最大长度             │
│ AVATAR_MAX_BYTES     │ 2 MB                       │ 头像上传上限               │
│ UPLOAD_MAX_BYTES     │ 50 MB                      │ 课件/文件树上传上限         │
│ RECENT_OPENS_LIMIT   │ 6 条                       │ "最近打开"最多记几条        │
│ _SUBDIRS             │ users/courses/resources/   │ 启动时自动创建的子目录      │
│                      │ trash                      │                          │
└──────────────────────┴────────────────────────────┴──────────────────────────┘

环境变量覆盖（只在测试/部署时用）：
  QC_V2_DATA_ROOT  改数据目录    QC_V2_DB  改数据库文件    QC_V2_PORT  改端口
"""
import os
from pathlib import Path

BACKEND_V2_DIR = Path(__file__).resolve().parents[1]
QC_DIR = BACKEND_V2_DIR.parent
WORKSPACE_DIR = QC_DIR.parent                       # 工作区根（qc-pocket-space 的上级）

# V2 数据根：与 V1 的 data/ 平级的 data_v2/（换版本 = 换整个根目录，互不掺和）
DATA_ROOT = Path(os.environ.get("QC_V2_DATA_ROOT") or WORKSPACE_DIR / "data_v2")
DB_PATH = Path(os.environ.get("QC_V2_DB") or DATA_ROOT / "codenova_v2.db")

TOKEN_TTL_DAYS = 7
TOKEN_TTL_SECONDS = TOKEN_TTL_DAYS * 86400
MIN_PASSWORD_LEN = 6
DEFAULT_ACCOUNT_PASSWORD = "123456"   # 批量注册未指定密码时的默认值（≥6 位，建完提示改）
MAX_USERNAME_LEN = 64
AVATAR_MAX_BYTES = 2 * 1024 * 1024
UPLOAD_MAX_BYTES = 50 * 1024 * 1024
RECENT_OPENS_LIMIT = 6
PRESET_AVATAR_DIR = BACKEND_V2_DIR / "assets" / "avatars"   # 预设头像（随代码分发，不进数据目录）
DEFAULT_PRESET_AVATAR = "preset/avatar_01.svg"

_SUBDIRS = ["users", "courses", "resources", "trash"]


def ensure_dirs() -> None:
    DATA_ROOT.mkdir(parents=True, exist_ok=True)
    for name in _SUBDIRS:
        (DATA_ROOT / name).mkdir(exist_ok=True)
