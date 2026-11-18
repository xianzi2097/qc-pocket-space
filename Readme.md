# CodeNova

**An open-source kids' coding education platform** — Scratch 4.0 online programming, VitePress-rendered e-books, and a classroom management system for small training centers and LAN-only deployments. Zero installation, portable, runs from a USB drive.

少儿编程教学平台 — 专为培训机构和小课堂打造：Scratch 在线编程 + 电子书课程 + 课堂管理，纯内网部署，解压即用。

技术栈：**FastAPI + React + Tailwind CSS + SQLite + VitePress**，单机绿色交付，无需公网、无需运维。

## 功能

| 功能 | 说明 | 使用者 |
|---|---|---|
| 在线编程 | Scratch 4.0-QC 魔改版、Python / C++ 在线运行（Judge0） | 学生 |
| 电子书课程 | 课程源文件（Markdown + 图片）服务端渲染为静态电子书，支持按课程类型切换主题 | 学生/访客 |
| 课程分享 | 每门课可开启下载开关，导出完整源文件 zip | 老师 |
| 课堂点名 / 学生记录 | 接入班级，按日期记录与查询 | 老师 |
| 资产管理 | 课程 / 书架 / 资料 / 软件下载四类资产，文件树在线管理 | 老师 |
| 消息与笔记 | 站内信、学生笔记本 | 师生 |
| 回收站 | 删除进回收站，可恢复 | 老师 |
| 控制面板 | 桌面端一键启停服务、注册账号、自动备份（qx-launcher） | 部署者 |
| 数据安全 | PBKDF2 密码哈希、会话表、登录限流；结构化数据入库，大文件落盘，自动备份保留 14 份 | — |

## 🚀 快速开始

绿色交付包 = `qc-pocket-space/`（程序主体）+ `qx-launcher/`（控制面板）+ `data_v2/`（数据）三个文件夹。

```bash
# 1. 克隆部署包
git clone https://github.com/xianzi2097/qc-pocket-space.git

# 2. 打开控制面板（qx-launcher），点 🟢 启动服务
```

> 首次启动自动创建管理员（admin / 111111），请立即修改密码。

## 📦 组件

| 仓库 | 说明 |
|---|---|
| [qc-pocket-space](.) | 主体：FastAPI 后端 + React 前端 + 运行时 + 文档 |
| [qc-launcher](https://github.com/xianzi2097/qc-launcher) | 控制面板：启停服务、注册、备份（Python 桌面 GUI） |
| [qc-frontend](https://github.com/xianzi2097/qc-frontend) | React 前端源码（Vite + TypeScript + Tailwind） |
| [scratch-Teaching](https://github.com/xianzi2097/scratch-Teaching) | Scratch 4.0-QC 魔改版源码 |

## 📖 文档

- API 规范 / 数据库设计 / 架构决策：`docs/` 目录（backend-v2-api-spec、backend-v2-database、frontend-migration-plan）
- 在线学习手册：站内 `/wiki` 路径（VitePress 构建，含 C++ / Python / Scratch 课程）

## ⚖️ 许可

[MIT License](LICENSE)
