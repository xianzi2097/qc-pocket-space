---
name: server-deploy
description: "服务器部署运维。Keywords: 部署, 服务器, codenova.cn, SSH, Caddy, FastAPI, Start_Caddy.bat, git pull, 重启, 运维."
---

# 青橙 Studio 服务器部署

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

> 本地 `website` 和 `qx-launcher` 测试完推 Gitee → 服务器 `git pull`

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
