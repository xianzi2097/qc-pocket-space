# QC-Studio / 青橙 Studio

少儿编程学习平台 — 青橙课堂管理系统。

## 目录结构（必须一致）

```
QC-PocketSpace/                 ← 工作区根目录
  ├── qc-pocket-space/          ← ← 本仓库（后端 + Caddy + 前端产物）
  │   ├── backend/main.py       ← FastAPI
  │   ├── caddy/Caddyfile       ← 反向代理
  │   └── frontend/dashboard/   ← 前端 build 产物
  ├── data/                     ← 用户数据（不参与 git）
  ├── labs_dev/
  │   ├── qx-dashboard-ts/      ← 前端源码（build → ../qc-pocket-space/frontend/dashboard/）
  │   ├── qx-launcher/          ← 启动器（自动找兄弟 qc-pocket-space/）
  │   ├── Vitepress-dev/        ← 文档
  │   └── tests/                ← 种子数据脚本
  └── docs/
```

## 首次运行（注入测试数据）

```bash
python labs_dev/tests/seed_dev_data.py
```

幂等脚本，会创建：3 位老师、50 位学生、10 个班级、18 门课程（Scratch/Python/C++）。可重复运行，已存在的跳过。

## 测试账号

| 账号      | 密码   | 角色   |
| --------- | ------ | ------ |
| admin     | 111111 | 管理员 |
| 林老师(t) | 111111 | 老师   |
| 程老师(t) | 111111 | 老师   |
| 黄老师(t) | 111111 | 老师   |

## ⚠️ 约束

以下路径是硬编码的，目录结构不能动：

- `qx-dashboard-ts/vite.config.ts` → `outDir: ../../qc-pocket-space/frontend/dashboard`
- `backend/main.py` → `_DATA_ROOT: ../../data/`
- `qx-launcher/app/config.py` → 向上找兄弟 `qc-pocket-space/`
- `tests/seed_dev_data.py` → `PROJECT_ROOT` 向上 3 级
