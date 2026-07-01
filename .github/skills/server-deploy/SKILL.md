---
name: server-deploy
description: "服务器部署运维。Keywords: 部署, 服务器, codenova.cn, SSH, Caddy, FastAPI, Start_Caddy.bat, git pull, 重启, 运维."
---

# 青橙 Studio 服务器部署

> 🚨 老师通过 qx-launcher 启动服务，不要叫他跑 Start_Caddy.bat！

## ⚠️ 两个 Python，严格区分！
| | Python 位置 | 装了什么 | 用途 | 进 git |
|---|---|---|---|---|
| ① 启动器 | 系统 Python（开发机独有） | PySide6 | 跑 qx-launcher GUI | ❌ |
| ② 后端 | `runtime/python/python.exe` | fastapi/uvicorn/pydantic/sqlalchemy | 跑 backend/main.py | ✅ |
- 后端自带 Python 是因为：用户电脑可能没装 Python，U 盘插上必须能跑
- **绝对不要**给 runtime/python/ 装 PySide6！后端不需要 GUI 库
- **绝对不要**给系统 Python 装 fastapi！启动器不需要后端依赖

## 服务器信息
- **主机**: 117.72.13.189 / codenova.cn
- **用户**: administrator / 密码 P@ssw0rd123!
- **端口**: 22 (SSH)

## 路径映射（本地 ↔ 服务器）

| 本地 | 服务器 | 说明 |
|---|---|---|
| `qc-pocket-space/` | `C:\QC-Packet\qc-pocket-space\` | 部署代码（FastAPI + Caddy + 前端） |
| `data/` | `C:\QC-Packet\data\` | 用户数据，不参与 Git，两边独立 |
| `labs_dev/qx-launcher/` | `C:\QC-Packet\qc-launcher\` | Launcher 源码 |

> 本地 `qc-pocket-space` 和 `qx-launcher` 测试完推 Gitee → 服务器 `git pull`

## 🗂️ 完整项目架构（核心理解！）
```
QC-PocketSpace/                          ← 工作区根
├── qc-pocket-space/                     ← 【部署包】自包含，git clone 即用
│   ├── backend/main.py                  ← FastAPI 后端（用 runtime/python/）
│   ├── frontend/                        ← 前端 build 产物
│   │   ├── dashboard/                   ← React 看板（qx-dashboard-ts build →）
│   │   ├── scratch_dist/                ← Scratch 编辑器（Scratch-dev build →）
│   │   └── vitepress_dist/              ← 文档站（Vitepress-dev build →）
│   ├── caddy/                           ← Caddy 启动脚本 + 配置模板
│   │   ├── Start_Caddy.bat              ← 备用手动启动
│   │   └── start_fastapi.bat            ← 硬编码指向 runtime/python/
│   └── runtime/                         ← 自带运行时！
│       ├── python/                      ← Python 3.12 + fastapi/uvicorn/pydantic/sqlalchemy
│       └── caddy/caddy.exe              ← Caddy Web 服务器
│
├── data/                                ← 用户数据（不进 git，每台独立）
│
└── labs_dev/                            ← 开发源码（各自独立 git 仓库）
    ├── qx-dashboard-ts/  → Arin-Cabin/qc-frontend       (React 看板)
    ├── qx-launcher/      → Arin-Cabin/qc-launcher        (GUI 启动器)
    ├── Scratch-dev/      → Arin-Cabin/scratch-Teaching   (Scratch 二开)
    └── Vitepress-dev/    → Arin-Cabin/qc-vitepress       (课程文档)
```

**工作流：** 改 dev 源码 → build → 产物到 `frontend/` → git push qc-pocket-space → 服务器 pull → 老师用 launcher 启动

**两个 Python 铁律：**
- ① 启动器 Python（系统/virtualenv）：**只有 PySide6**，不装后端库
- ② 后端 Python（`runtime/python/`）：**只有 fastapi/uvicorn 等**，不装 GUI 库
- 后端自带 Python 的原因：用户电脑/U 盘可能没装 Python，必须自包含

## ⚠️ 原则
- **绝不删 `.git`**、**绝不 `--force`**，涉及仓库操作先问用户
- 通过 SSH 启动的进程会在 SSH 断开时自动被杀掉（和远程桌面不同）
- 部署时 **SSH 只管拉代码，重启由用户本地运行 bat**

## 部署流程
### 标准方式：SSH 拉代码 + 用户本地重启
```bash
# 1. 拉取 website 代码
ssh administrator@117.72.13.189 "cd C:\QC-Packet\qc-pocket-space && git pull"

# 2. 拉取 qx-launcher 代码
ssh administrator@117.72.13.189 "cd C:\QC-Packet\qc-launcher && git pull"

# 3. 通知用户手动跑 Start_Caddy.bat 选 2
```

### 强制同步（仅用户要求时）
```bash
ssh administrator@117.72.13.189 "cd C:\QC-Packet\qc-pocket-space && git fetch origin && git reset --hard origin/master"
ssh administrator@117.72.13.189 "cd C:\QC-Packet\qc-launcher && git fetch origin && git reset --hard origin/master"
```

### 清理旧虚拟环境（已废弃，改用便携 Python）
```bash
ssh administrator@117.72.13.189 "cd C:\QC-Packet\qc-pocket-space && rmdir /s .venv && rmdir /s backend\.venv"
```

## 运行时架构（绿色便携）
```
qc-pocket-space/
├── backend/            ← Python 后端源码
├── frontend/           ← 前端构建产物
├── caddy/              ← Caddy 配置 + 启动脚本
│   ├── Start_Caddy.bat    ← 启动入口（选 1 本地 / 2 线上）
│   ├── start_fastapi.bat  ← 启动 FastAPI（便携 Python）
│   ├── generate_caddyfile.py
│   └── Caddyfile.template
├── runtime/
│   ├── caddy/caddy.exe    ← Caddy Web 服务器
│   └── python/python.exe  ← 便携 Python（含 fastapi/uvicorn）
└── data/               ← 用户数据（独立）
```

## 用户本地启动
```
C:\QC-Packet\qc-pocket-space\caddy\Start_Caddy.bat
```
选 `1` = 本地测试 (localhost)
选 `2` = 线上生产 (codenova.cn)
bat 自动：生成 Caddyfile → 启动 FastAPI（便携 Python）→ 启动 Caddy
