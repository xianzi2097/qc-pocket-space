# Mini-Studio Website

这是一个 Windows 环境下部署的教学网站工程。

## 开发环境

当前开发环境：

- 系统：Windows 11
- 工程路径：`C:\Arin - cabin\QC-PocketSpace\website`
- 后端：Python + FastAPI + SQLite
- 前端：静态 HTML/CSS/JS
- 网关：Caddy for Windows


## 目录结构
```text
website/
├─ backend/              FastAPI 后端
│  ├─ main.py            后端入口
│  ├─ requirements.txt   Python 依赖
│  └─ qingcheng.db       SQLite 数据库，运行时文件，不进 Git
│
├─ site/                 前端和静态资源
│  ├─ portal/            主页、登录页、个人中心
│  ├─ classroom/         在线教室
│  ├─ share/             共享素材区
│  ├─ base/              学生/用户运行数据，不进 Git
│  ├─ scratch_dist/      Scratch 前端构建产物
│  └─ vitepress_dist/    文档站构建产物
│
├─ caddy/                Caddy 配置和启动脚本
│  ├─ Caddyfile
│  └─ Start_Caddy.bat
│
├─ .gitignore
├─ LICENSE
├─ Readme.md
└─ TODO.md
```

## 需要安装的软件

服务器需要安装：
- Git
- Python 64 位，建议和开发机版本一致
- Node.js，可选
- Caddy 已随工程放在 `caddy/` 目录

Node.js 只有在重新构建 VitePress 文档或 Scratch 前端源码时才需要。当前仓库里已经有：

```text
site/vitepress_dist/
site/scratch_dist/
```

如果只是部署现有网站，不需要执行 `npm install`。

## 首次部署

1. 克隆或拉取工程：

```powershell
cd "C:\Arin - cabin\QC-PocketSpace"
git clone <你的仓库地址> website
```

如果目录已经存在：

```powershell
cd "C:\Arin - cabin\QC-PocketSpace\website"
git pull
```

2. 创建后端虚拟环境：

```powershell
cd "C:\Arin - cabin\QC-PocketSpace\website\backend"
py -m venv .venv
```

3. 安装 Python 依赖：

```powershell
.\.venv\Scripts\pip.exe install -r requirements.txt
```

4. 准备运行时数据：

`backend/qingcheng.db` 和 `site/base/` 不进入 Git。生产服务器如果已有数据，不要覆盖。

如果是全新服务器，后端启动后会自动创建数据库表和基础目录。

## 启动后端

开发调试时推荐：

```powershell
cd "C:\Arin - cabin\QC-PocketSpace\website\backend"
.\.venv\Scripts\uvicorn.exe main:app --host 127.0.0.1 --port 8000 --reload
```

生产运行时推荐不要加 `--reload`：

```powershell
cd "C:\Arin - cabin\QC-PocketSpace\website\backend"
.\.venv\Scripts\uvicorn.exe main:app --host 127.0.0.1 --port 8000
```

Caddy 会把 `/api/*` 转发到：

```text
127.0.0.1:8000
```

## 启动 Caddy

双击运行：

```text
caddy/Start_Caddy.bat
```

启动后会让你选择：

```text
[1] 本地测试模式
[2] 线上生产模式
```

本地调试选择 `1`。

线上部署选择 `2`，会使用 `codenova.cn` 域名并申请 HTTPS 证书。

## 更新部署

生产服务器更新代码时：

```powershell
cd "C:\Arin - cabin\QC-PocketSpace\website"
git pull
```

如果 `backend/requirements.txt` 变了，更新 Python 依赖：

```powershell
cd "C:\Arin - cabin\QC-PocketSpace\website\backend"
.\.venv\Scripts\pip.exe install -r requirements.txt
```

如果改了 `backend/main.py`，需要重启后端。

如果改了 `site/portal/*.html`、图片、静态资源，通常刷新浏览器即可。

如果改了 `caddy/Caddyfile`，需要重启 Caddy。

## Git 忽略规则

以下内容是运行时数据，不应该推送到 Git：

```text
backend/*.db
backend/*.db-*
site/base/
backend/.venv/
backend/env/
**/__pycache__/
*.pyc
*.pyo
*.pyd
```

原因：

- `backend/qingcheng.db` 是真实账号数据库，推送会影响生产环境数据
- `site/base/` 是学生/用户文件数据，和数据库绑定，也不能被 Git 覆盖
- `.venv` 是本机虚拟环境，不适合作为代码提交

## 注意事项
- 注册学生会创建对应账号文件夹，删除学生时注意同步处理 `site/base/` 下的学生目录。
- 数据库里的 `avatar_path` 字段目前保存的是头像文件名，例如 `avatar_01.jpg`。
- 后端 Python 文件修改后，正在运行的服务不会自动更新，除非启动时使用了 `--reload`。
- 当前前端为静态构建产物，正常部署不需要 `npm install`。
