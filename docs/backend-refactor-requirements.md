# CodeNova 后端重构需求文档

> **基线**：qc-pocket-space @ `c1e6031d`（2026-10-04 盘点）
> **状态**：📦 **历史输入文档（已冻结，不再更新）** —— 本文档是重构的"初稿/需求盘点"，其中第 2 章（V1 现状）永远反映基线时点的 V1，第 4/5 章方案已被实施取代。
> **权威以 [backend-v2-api-spec.md](./backend-v2-api-spec.md) 为准**（接口、D 决策、M 阶段一律看那边；与本文档冲突时 spec 赢）。
> **生成方式**：第 2 章接口/权限/字段清单由代码自动提取，与基线 commit 严格对应

**编号规则**：P = 痛点，C = 约束，D = 设计决策，V = 验收，O = Out of Scope。正文交叉引用一律用编号。

**阅读顺序**：第 2 章（事实）→ 第 3 章（红线）→ 第 1 章（决策）→ 第 4/5 章（方案）→ 第 6/7 章（验收与退路）。

---

## 第 1 章 · 背景与目标 【待填写】

### 1.1 痛点（P-xx）

> 写法：一条痛点 = 编号 + 描述 + 影响。每条痛点必须在第 5 章有对应解法、在第 6 章有对应验收。
> 下面是按现状预填的草案，**请删改确认**，可继续追加。

| 编号 | 痛点 | 影响 | 严重度 |
|---|---|---|---|
| P-01 | `utils.py` 631 行大杂烩，40+ 函数混放（密码/token/路径/软删/班级/课堂/课程元数据/站内信…） | 改任一模块都得先读它，改动风险高 | 待评 |
| P-02 | 功能全靠读代码才知道，无文档，无法评审、无法交接、重构无依据 | 本文档即解药 | 待评 |
| P-03 | 测试与旧设计绑定，重构时旧测试会拦截每次 push | 开工前需"关两道门"（见 4.1） | 待评 |
| P-04 | （自填） | | |

### 1.2 目标 【待填写】

> 一句话公式：**"把 X 从 A 状态改成 B 状态，为了 C。"**
> 示例："把后端从『utils 大杂烩 + 薄路由』改成『模块自包含』，为了新增功能时只需理解一个模块。"

（待填）

### 1.3 范围 【待填写】

| ✅ 本期做 | ❌ 本期不做 | ⏸ 以后再说 |
|---|---|---|
| （待填） | | |

**检查点**：1.3 的 ❌/⏸ 两项必须与第 7 章 Out of Scope 一致；1.1 每条痛点必须能追溯到第 5 章解法与第 6 章验收。

---

## 第 2 章 · 现状盘点（代码提取 · 基线 `c1e6031d`）

### 2.1 技术栈与代码结构

- **技术栈**：FastAPI + SQLAlchemy(SQLite) + 文件系统，Caddy 反代 `:8000`，Windows 部署
- **数据根**：`data/`（不进 git，与代码分离）

| 文件 | 行数 | 职责 |
|---|---|---|
| `backend/main.py` | 72 | 入口：lifespan（admin 种子、目录自检、每日自动备份）、鉴权中间件（只解析不拦截）、ProxyHeaders（仅信 127.0.0.1）、挂载 10 个 router |
| `backend/models.py` | 42 | SQLAlchemy 引擎（WAL 模式）、`users` / `trash` 两表、data/ 目录引导创建 |
| `backend/utils.py` | 631 | 共享工具大杂烩：密码、token、DB 会话、鉴权（get_current_user/require_role）、路径安全（_safe_path/_valid_new_name）、原子写+文件锁、用户目录、软删/恢复、班级/课堂/课程/资源/站内信工具、全部 Pydantic 表单 |
| `backend/file_tree.py` | 121 | 课程/资料共用文件树**单一事实来源**：walk_tree + reorder（`_order.json`），杜绝两处代码分叉 |
| `backend/backup.py` | 128 | 每日定时自动备份整个 data/，保留最近 N 份 |
| `backend/build.py` | 259 | 电子书课程 vitepress 发布队列（queue_publish/remove_site） |
| `backend/routers/` | 10 个模块 | 共 **91 个接口**（见 2.2） |
| `tests/` | 6 个测试文件 | 快测 199 个 / 全量 215 个（见 2.6） |

### 2.2 接口清单（91 个 · 10 个模块）

> 列说明：**鉴权**列中「公开」= 无需登录即可访问（产品设计，见 C-02）；「登录」= 任意已登录角色；「角色」= require_role 白名单。
> 数据落点：SQLite 表 / 文件路径（相对 `data/`）。

#### 2.2.1 auth — 注册登录（6 个）

| 方法 | 路径 | 功能 | 鉴权 | 数据落点 |
|---|---|---|---|---|
| POST | `/api/register` | 创建账号（仅 teacher/student 角色） | 建 teacher→admin；建 student→admin/teacher | SQLite `users` + 建用户目录 |
| POST | `/api/login` | 登录，梯度限流（5→30s / 10→5min / 15→30min / 20→2h，按真实 IP） | 公开 | SQLite `users` + 内存 token 表 |
| POST | `/api/logout` | 注销当前 token | 持 token | 内存 `ACTIVE_TOKENS` |
| POST | `/api/admin/batch-register` | 批量注册（启动器专用，可带默认密码） | admin | SQLite `users` + 用户目录 |
| GET | `/api/admin/classes/{teacher}` | 读指定老师的班级列表 | admin | `users/{teachers}/{id}/classes.json` |
| POST | `/api/admin/classes/{teacher}` | 写指定老师的班级列表（覆盖） | admin | `classes.json` 原子写 |

#### 2.2.2 users — 用户与资料（7 个）

| 方法 | 路径 | 功能 | 鉴权 | 数据落点 |
|---|---|---|---|---|
| GET | `/api/users` | 全部账号列表（首页展示老师用） | **公开**（C-02） | SQLite `users` |
| DELETE | `/api/users/{username}` | 删除用户（软删；admin 不可删） | admin | `users`→`trash` 表 + 目录→`trash/` |
| GET | `/api/user/profile` | 本人资料 + 描述符 | 登录 | SQLite `users` |
| POST | `/api/update-avatar` | 切换预设/自定义头像 | 登录 | `users.avatar_path` |
| POST | `/api/upload-avatar` | 上传自定义头像（仅学生，≤2MB） | 登录（仅 student） | `users/{students}/{id}/avatar.jpg` |
| POST | `/api/user/change-password` | 改密（验原密码，哈希存储） | 登录 | `users.password` |
| POST | `/api/update-descriptors` | 更新描述符（JSON 校验） | 登录 | `users.qc_descriptors` |

#### 2.2.3 classes — 班级与学生（9 个）

| 方法 | 路径 | 功能 | 鉴权 | 数据落点 |
|---|---|---|---|---|
| GET | `/api/classes` | 读本人班级列表（含学生、排课，兼容旧 schedule 格式） | 登录 | `users/{teachers}/{id}/classes.json` |
| POST | `/api/classes` | 创建班级（重名校验，文件锁） | teacher | `classes.json` |
| PUT | `/api/classes/{class_name}` | 改名/改排课（改名联动重命名 sessions 目录） | teacher | `classes.json` + `sessions/` 目录 |
| DELETE | `/api/classes/{class_name}` | 删除班级（移入文件回收站，可恢复） | teacher | `classes.json` + `trash/` |
| POST | `/api/classes/{class_name}/students` | 加学生（已有账号挂接 / 新建账号，文件锁） | teacher | `classes.json` + SQLite `users` |
| GET | `/api/classes/find-student` | 跨老师全局搜学生 | teacher | 扫描全体 `classes.json` |
| GET | `/api/students/list` | 全体学生列表（读 info.json） | teacher | `users/{students}/*/info.json` |
| DELETE | `/api/classes/{class_name}/students/{id}` | 移出班级（**不删账号**） | teacher | `classes.json` |
| PATCH | `/api/classes/{class_name}/students/{id}/schedule` | 学生个人上课日（null=恢复跟随班级） | teacher | `classes.json` |

#### 2.2.4 courses — 课程与课件（24 个）

| 方法 | 路径 | 功能 | 鉴权 | 数据落点 |
|---|---|---|---|---|
| GET | `/api/courses` | 课程列表（含书架/发布态/教师归属） | **公开**（C-02） | `courses/{teacher}_course/*/course.json` |
| POST | `/api/courses` | 创建课程（生成唯一 id 文件夹，同名校验） | admin/teacher | `courses/{teacher}_course/{id}/` |
| GET | `/api/courses/{name}/materials` | 课件列表（scratch 看 sb3 / 其他看文件） | **公开**（C-02） | 课程目录 |
| DELETE | `/api/courses/{name}/materials/{lesson}` | 删除课件文件夹 | admin/teacher | 课程目录（rmtree） |
| DELETE | `/api/courses/{name}/materials/{lesson}/{file}` | 删除课件内文件 | admin/teacher | 课程目录 |
| POST | `/api/courses/{name}/materials/{lesson}/upload` | 上传课件文件（≤50MB，文件名清洗） | admin/teacher | 课程目录 |
| POST | `/api/courses/{name}/materials/{lesson}/folder` | 新建课件文件夹 | admin/teacher | 课程目录 |
| PUT | `/api/courses/{course_id}` | 更新课程元信息（名称/描述/标签/类型/打开方式） | admin/teacher | `course.json` |
| PUT | `/api/courses/{course_id}/shelf` | 移动书架（books=收纳 / courses=正在教） | admin/teacher | `course.json`（shelf+featured 双写） |
| PUT | `/api/courses/{course_id}/featured` | 旧接口兼容（featured 布尔→书架语义） | admin/teacher | `course.json` |
| POST | `/api/courses/{name}/rename` | 课程改名（只改显示名，文件夹名=id 不动） | admin/teacher | `course.json` |
| POST | `/api/courses/{name}/materials/{lesson}/rename` | 课件文件夹改名 | admin/teacher | 课程目录 |
| POST | `/api/courses/{name}/materials/{lesson}/{file}/rename` | 课件内文件改名 | admin/teacher | 课程目录 |
| POST | `/api/courses/{name}/materials/{lesson}/save-sb3` | 保存 main/student.sb3（≤50MB） | admin/teacher | `main.sb3` / `student.sb3` |
| GET | `/api/courses/{id}/tree` | 文件树（资源管理器视图） | admin/teacher | 课程目录 + `_order.json` |
| POST | `/api/courses/{id}/tree/reorder` | 拖拽排序（up/down/swap，文件锁+原子写） | admin/teacher | `_order.json` |
| POST | `/api/courses/{id}/tree/folder` | 树内建文件夹 | admin/teacher | 课程目录 |
| POST | `/api/courses/{id}/tree/upload` | 树内上传（≤50MB；电子书首传自动定名） | admin/teacher | 课程目录 + 可能回写 `course.json` |
| POST | `/api/courses/{id}/tree/rename` | 树内重命名 | admin/teacher | 课程目录 |
| DELETE | `/api/courses/{id}/tree` | 树内删除（**物理删除，不经回收站**） | admin/teacher | 课程目录 |
| POST | `/api/courses/{id}/publish` | 电子书发布（vitepress 渲染入队） | admin/teacher | `course.json.publish` + `build` 队列 |
| GET | `/api/courses/{id}/publish/status` | 发布状态查询 | admin/teacher | `course.json` + 内存队列 |
| DELETE | `/api/courses/{name:path}/permanent` | 永久删除课程（含发布产物） | admin/teacher | 物理 rmtree + `build.remove_site` |
| DELETE | `/api/courses/{name:path}` | 删除课程（移入文件回收站） | admin/teacher | `trash/` + `build.remove_site` |

#### 2.2.5 resources — 资料/软件下载（11 个）

| 方法 | 路径 | 功能 | 鉴权 | 数据落点 |
|---|---|---|---|---|
| GET | `/api/resources` | 资料列表（按老师/分类过滤） | **公开**（C-02） | `resources/{teacher}_resource/*/resource.json` |
| POST | `/api/resources` | 创建资料（唯一 id，同名校验） | admin/teacher | `resources/{teacher}_resource/{id}/` |
| GET | `/api/resources/{id}/tree` | 资料文件树 | **公开**（C-02，只读） | 资料目录 + `_order.json` |
| POST | `/api/resources/{id}/tree/reorder` | 拖拽排序（与课程同一套逻辑） | admin/teacher | `_order.json` |
| GET | `/api/resources/{id}/download` | 下载（单文件直发 / 多文件实时打包 zip） | **公开**（C-02） | 资料目录（只读） |
| POST | `/api/resources/{id}/tree/upload` | 树内上传（≤50MB） | admin/teacher | 资料目录 |
| POST | `/api/resources/{id}/tree/folder` | 树内建文件夹 | admin/teacher | 资料目录 |
| POST | `/api/resources/{id}/tree/rename` | 树内重命名 | admin/teacher | 资料目录 |
| DELETE | `/api/resources/{id}/tree` | 树内删除（**物理删除**） | admin/teacher | 资料目录 |
| PUT | `/api/resources/{id}` | 更新资料元信息（名称/描述/标签/格式） | admin/teacher | `resource.json` |
| DELETE | `/api/resources/{id}` | 删除资料（移入文件回收站） | admin/teacher | `trash/` |

#### 2.2.6 messages — 站内信（10 个）

| 方法 | 路径 | 功能 | 鉴权 | 数据落点 |
|---|---|---|---|---|
| POST | `/api/messages/send` | 发消息（**仅教师→学生**） | teacher | `users/{students}/{id}/messages/{文件夹}/*.json` |
| GET | `/api/messages/folders` | 文件夹列表 + 未读统计 | 登录 | 本人 `messages/` |
| GET | `/api/messages/inbox` | 收件箱列表（folder 过 _safe_path） | 登录 | 本人 `messages/` |
| POST | `/api/messages/{id}/read` | 标记已读（文件锁+原子写） | 登录 | 本人 `messages/*.json` |
| POST | `/api/messages/folders` | 新建文件夹（收件箱保护） | 登录 | 本人 `messages/` |
| DELETE | `/api/messages/folders/{name}` | 删除文件夹（收件箱不可删） | 登录 | 本人 `messages/` |
| PUT | `/api/messages/folders/{name}` | 重命名文件夹（收件箱不可改） | 登录 | 本人 `messages/` |
| DELETE | `/api/messages/{id}` | 删除消息 | 登录 | 本人 `messages/*.json` |
| POST | `/api/messages/{id}/move` | 移动消息到其他文件夹 | 登录 | 本人 `messages/` |
| GET | `/api/messages/unread-count` | 未读总数（给角标轮询） | 登录 | 本人 `messages/` |

#### 2.2.7 notes — 笔记本与笔记（9 个）

| 方法 | 路径 | 功能 | 鉴权 | 数据落点 |
|---|---|---|---|---|
| GET | `/api/notebooks` | 笔记本列表（含笔记计数） | 登录 | `users/{id}/notes/*/` |
| POST | `/api/notebooks` | 新建笔记本 | 登录 | `users/{id}/notes/{名}/` |
| DELETE | `/api/notebooks/{name}` | 删除笔记本（整个文件夹） | 登录 | `notes/` |
| PUT | `/api/notebooks/{name}` | 重命名笔记本 | 登录 | `notes/` |
| GET | `/api/notes` | 笔记列表（文件名/大小/修改时间） | 登录 | `notes/{本}/*.md` |
| GET | `/api/notes/{notebook}/{file}` | 读笔记内容 | 登录 | `notes/*.md` |
| POST | `/api/notes` | 保存笔记（自动补 .md，原子写） | 登录 | `notes/*.md` |
| PUT | `/api/notes/{notebook}/{file}` | 重命名笔记 | 登录 | `notes/*.md` |
| DELETE | `/api/notes/{notebook}/{file}` | 删除笔记 | 登录 | `notes/*.md` |

#### 2.2.8 sessions — 课堂记录（5 个）

| 方法 | 路径 | 功能 | 鉴权 | 数据落点 |
|---|---|---|---|---|
| POST | `/api/sessions/{class}` | 保存某天记录（同日覆盖，按月分文件，文件锁） | teacher | `users/{teachers}/{id}/sessions/{班级}/{YYYY-MM}.json` |
| GET | `/api/sessions/{class}` | 查记录（按月或全部，倒序） | 登录 | 同上 |
| GET | `/api/sessions/student/{name}` | 跨老师全局搜学生课堂记录 | teacher | 扫描全体 `sessions/` |
| GET | `/api/sessions/{class}/{date}` | 查单日记录 | 登录 | 同上 |
| DELETE | `/api/sessions/{class}/{date}` | 删除单日记录（文件锁） | teacher | 同上 |

#### 2.2.9 settings — 用户设置（3 个）

| 方法 | 路径 | 功能 | 鉴权 | 数据落点 |
|---|---|---|---|---|
| GET | `/api/user/settings` | 读设置（缺省字段自动补默认值） | 登录 | `users/{id}/settings.json` |
| PUT | `/api/user/settings` | 改设置（主题/色相/字号 12-28/侧栏，文件锁） | 登录 | `settings.json` |
| POST | `/api/user/recent-opens` | 记「最近打开」（去重置顶，上限 6 条） | 登录（游客 401） | `settings.json` |

#### 2.2.10 trash — 回收站（7 个，**两套体系**）

| 方法 | 路径 | 功能 | 鉴权 | 数据落点 |
|---|---|---|---|---|
| GET | `/api/trash` | 用户垃圾桶列表（DB 版） | admin | SQLite `trash` 表 |
| POST | `/api/trash/restore/{id}` | 恢复被删用户（目录+账号） | admin | `trash` 表 + `trash/` 目录 |
| DELETE | `/api/trash` | 清空用户垃圾桶 | admin | `trash` 表 + `trash/` |
| DELETE | `/api/trash/{id}` | 彻底删除单个用户 | admin | `trash` 表 + `trash/` |
| GET | `/api/trash/items` | 文件回收站列表（班级/课程/资料；非 admin 只看自己的） | 登录 | `trash/*/.trash.json` |
| POST | `/api/trash/items/{folder}/restore` | 恢复（班级回 classes.json / 课程/资料回原目录） | admin/teacher | `trash/` → 原位置 |
| DELETE | `/api/trash/items/{folder}` | 彻底删除（老师可看可恢复，**仅 admin 可彻底删**） | admin | `trash/` |

### 2.3 权限矩阵（角色 × 操作，从 require_role 提取）

> 角色：admin > teacher > student > guest（游客=未登录，middleware 赋 guest 身份）

| 操作 | admin | teacher | student | guest |
|---|:---:|:---:|:---:|:---:|
| 创建教师账号 | ✅ | ❌ | ❌ | ❌ |
| 创建学生账号 | ✅ | ✅ | ❌ | ❌ |
| 删除用户（软删） | ✅（不能删 admin 自己） | ❌（只能移出班级，≠删号） | ❌ | ❌ |
| 修改自己密码 / 头像 / 描述符 | ✅ | ✅ | ✅ | ❌ |
| 上传自定义头像 | ❌ | ❌ | ✅（仅学生，≤2MB） | ❌ |
| 班级增删改 / 加学生 / 排课 | ✅（经 `/api/admin/classes`） | ✅（本人班级） | ❌ | ❌ |
| 课程 CRUD / 文件树操作 / 发布 | ✅ | ✅ | ❌ | ❌ |
| 资料 CRUD / 文件树写操作 | ✅ | ✅ | ❌ | ❌ |
| 浏览课程/资料列表、文件树、下载 | ✅ | ✅ | ✅ | ✅（**公开只读 = 产品设计，C-02**） |
| 发站内信 | ❌（仅 teacher→student） | ✅ | ❌ | ❌ |
| 收发件箱/文件夹管理 | ✅ | ✅ | ✅ | ❌ |
| 写课堂记录 | ❌ | ✅ | ❌ | ❌ |
| 查课堂记录（本人相关） | ✅ | ✅ | ❌ | ❌ |
| 笔记本/笔记/设置（本人数据） | ✅ | ✅ | ✅ | ❌ |
| 用户垃圾桶（DB 版）全部操作 | ✅ | ❌ | ❌ | ❌ |
| 文件回收站：查看/恢复 | ✅（全部） / ✅ | ✅（本人的） / ✅ | ❌ / ❌ | ❌ |
| 文件回收站：彻底删除 | ✅ | ❌ | ❌ | ❌ |

### 2.4 数据存储盘点

#### SQLite（`data/qingcheng.db`，WAL 模式，仅 2 张表）

| 表 | 字段 | 用途 |
|---|---|---|
| `users` | id, username(唯一), password(哈希), role, avatar_path, qc_descriptors(JSON文本) | 账号唯一真相源 |
| `trash` | id, username, role, original_path, trash_folder, db_snapshot(整行快照), trashed_at | 软删除用户的恢复凭据 |

> 设计哲学：**数据库只管账号与班级关系等管理侧数据；内容资产一律走文件系统**（见 C-21）。

#### 文件系统（`data/`，不进 git）

```
data/
├── qingcheng.db                  # SQLite（+ WAL 文件）
├── users/
│   ├── admins/{id}/              # info.json
│   ├── teachers/{id}/            # info.json, classes.json, settings.json,
│   │                             # notes/{笔记本}/*.md, sessions/{班级}/{YYYY-MM}.json
│   └── students/{id}/            # info.json, settings.json, avatar.jpg(自定义),
│                                 # notes/, messages/{文件夹}/*.json
├── courses/{teacher}_course/{course_id}/   # 文件夹名 = 课程 id（真相源）
│   ├── course.json               # 课程元数据（见下）
│   ├── _order.json               # 文件树自定义顺序
│   └── {课件目录}/*.sb3|*.md|...
├── resources/{teacher}_resource/{resource_id}/
│   ├── resource.json             # 资料元数据（见下）
│   ├── _order.json
│   └── ...（任意层级文件树）
└── trash/
    ├── {用户垃圾桶目录}/          # move_to_trash 移入（含原目录）
    └── {回收站外壳}/.trash.json    # 班级/课程/资料文件回收站（type/owner/original_path）
```

#### 元数据 JSON 字段（从代码提取，重构时字段只许增不许删改）

**`course.json`**：
| 字段 | 类型 | 说明 |
|---|---|---|
| id | str | 与文件夹名一致（新课程为 c+时间戳；存量课程文件夹名=旧名） |
| name | str | 显示名（改名只改这里，不动文件夹） |
| type | str | 课程类型（scratch / python / cpp / ebook…） |
| format | str | 打开方式，由 `_resolve_course_format(type, format)` 归一化 |
| description / tags | str / list | 展示用 |
| teacher | str | 归属老师用户名（URL 不含老师层，归属记在这里） |
| publish | dict | `{status: idle/queued/building/done/error, ...}` |
| shelf | str | `books`=书架收纳 / `courses`=正在教 |
| featured | bool | **旧字段保留**，与 shelf 双写兼容旧前端 |
| created_at | str | `YYYY-MM-DD HH:MM:SS` |

**`resource.json`**：
| 字段 | 类型 | 说明 |
|---|---|---|
| id | str | 与文件夹名一致 |
| name / description / tags | str / str / list | 展示用（同名校验以 name 比较） |
| category | str | `reference` / `download` |
| teacher | str | 归属老师 |
| format | str | `folder`（默认）等，决定打开方式 |
| created_at | str | 创建时间 |

**打开方式映射**（"根据 json 决定如何打开"，从 `format` 字段读取）：`scratch`→Scratch 编辑器 / `ebook`→vitepress 渲染页 / `folder`→内置文件树 / 其他→文件直链下载。具体枚举值以 `_resolve_course_format` 与前端实际消费为准（**【待定】：要不要把枚举值补全成表，建议重构时与前端联审一次**）。

### 2.5 横切能力盘点（重构后必须逐项存活）

| 能力 | 实现位置 | 说明 |
|---|---|---|
| Token 鉴权 | utils: generate/verify_token + 内存 `ACTIVE_TOKENS` | 服务重启即失效（现状，无需改） |
| 登录梯度限流 | auth.py | 5次→30s / 10次→5min / 15次→30min / 20次→2h；按真实 IP（ProxyHeaders 仅信 127.0.0.1，防"全校共用一把锁"） |
| 路径穿越防护 | utils: `_safe_path` / `_valid_new_name` + file_tree: `split_rel` | 所有来自 URL/表单的路径段必过 |
| 原子写 + 文件锁 | utils: `_atomic_save` / `_atomic_write_file` / `_file_lock` | 读-改-写整体持锁，防并发覆盖与断电写半截 |
| 软删除/回收站 | utils: `move_to_trash` / `_move_to_trash` / `restore_from_trash` | 两套：用户=DB 表版，班级/课程/资料=文件版 |
| 每日自动备份 | backup.py | 服务常驻期间每天备份 data/，保留 N 份，启动时初始化 |
| 启动自检 | utils: `self_check_user_dirs` | DB 有用户但目录缺失时补建（防断电半状态） |
| admin 种子账号 | main: `_ensure_admin` | 不存在则创建 admin/111111（提示改密） |
| 统一信封 | 全部路由 | 成功 `{"status":"success", ...}`；失败 `HTTPException` → `{"detail"}` |
| 文件大小限制 | 上传类接口 | 课件/树上传 ≤50MB，头像 ≤2MB |
| 共享文件树 | file_tree.py | 课程/资料 walk+reorder 单一事实来源（历史分叉教训，见文件头注释） |

### 2.6 测试资产盘点

| 文件 | 行数 | 覆盖 |
|---|---|---|
| `test_security.py` | 538 | 鉴权、限流、越权、路径穿越 |
| `test_business.py` | 817 | 班级/学生/课堂记录/站内信/并发/边界 |
| `test_file_tree.py` | 287 | 文件树 walk/reorder/拖拽全链路（课程+资料参数化） |
| `test_resource_api.py` | 190 | 资料 CRUD 全链路 |
| `test_unit.py` | 156 | 纯函数单元 |
| `test_e2e.py` | 473 | 浏览器端到端（Playwright，**慢**） |
| `conftest.py` | 105 | 夹具、测试数据清理 |

- **快测**（部署门禁）：`pytest tests/ --ignore=tests/test_e2e.py` → 199 个，约 6.7 秒
- **全量**：`pytest tests/` → 215 个，约 164 秒
- 服务器看门狗 `CodeNova-CI`（schtasks 每 5 分钟）跑同样的快测

### 2.7 按端功能全景（功能视角 · 与 2.2 接口视角交叉引用）

> 来源：前端 `qx-dashboard-ts/src` 全量扫描（组件导航 + `/api/` 调用提取）+ 后端 2.2 对照。
> 状态标记：✅=在用 🟡=占位/未开放 ⚪=纯前端无后端接口 ❓=后端有接口、看板未调用

#### ① 门户首页（HomePage，游客可用）

| 功能 | 说明 | 状态 | 后端接口 |
|---|---|---|---|
| 登录 / 登出 / token 校验 | LoginPanel + useAuth（fetch 劫持带 Bearer） | ✅ | `POST /api/login`、`POST /api/logout`、`GET /api/user/profile` |
| 开始页（最近打开） | 磁贴导航、后端在线状态 | ✅ | `POST /api/user/recent-opens` |
| 书架 | 老师标"书架"的课程列表 | ✅ | `GET /api/courses`（shelf=books） |
| 课程广场 | 课程列表 + 课程详情 + 课件打开 | ✅ | `GET /api/courses`、`GET /api/courses/{name}/materials` |
| 书架移动（登录老师） | 课程卡上移入/移出书架 | ✅ | `PUT /api/courses/{id}/shelf` |
| 资料浏览 | 资料列表 + 文件树 + 下载 | ✅ | `GET /api/resources`、`/tree`、`/download` |
| 软件下载 | category=download 的资料 | ✅ | `GET /api/resources?category=download` + `/download` |
| 学习文档 | iframe 嵌 VitePress `/docs/` | ⚪ | 无（Caddy 直出） |
| Scratch 4.0-QC | 外链 `/Scratch/` 编辑器 | ⚪ | 无（Scratch 二开侧） |
| 在线编程 / HTML 演示 | CodePlayground / HtmlSandbox，浏览器内运行 | ⚪ | 无 |
| 头像 / 侧栏设置同步 | 登录后读写设置 | ✅ | `GET/PUT /api/user/settings`、`POST /api/update-avatar` |
| 老师列表展示 | 门户展示教师 | ✅ | `GET /api/users`（公开，C-02） |

#### ② 教师后台（TeacherDashboard，7 个 tab）

| Tab | 功能 | 状态 | 后端接口 |
|---|---|---|---|
| 数据看板 | 学生总数/班级数/属性维度三卡 | ✅ | `GET /api/classes`、`GET /api/users`（前端聚合，无专用统计接口） |
| 学生管理 | 班级创建/改名/删除、加学生、移出班级、重名查找、学生属性/描述符、课表展示 | ✅ | `POST/PUT/DELETE /api/classes*`、`POST .../students`、`DELETE .../students/{id}`、`GET /api/classes/find-student` |
| 发送消息 | 教师→学生单发 | ✅ | `POST /api/messages/send` |
| 课堂记录 | 按班级按月查看、当日保存覆盖、删除单日、跨老师搜学生 | ✅ | `GET/POST/DELETE /api/sessions/*`、`GET /api/sessions/student/{name}` |
| 文件管理 | 课程 CRUD、改名、书架、删除（进回收站）、永久删除、文件树（上传/建夹/改名/拖拽）、电子书发布 | ✅ | `/api/courses*` 全组 + `/tree/*` + `/publish*` + `permanent` |
| 资料管理 | reference 类资料 CRUD + 文件树 + 回收站恢复 | ✅ | `/api/resources*`、`/api/trash/items*` |
| 软件管理 | 同一组件，category=download | ✅ | 同上（`?category=download` 过滤） |

#### ③ 学生后台（StudentDashboard，FloatingDock 8 项）

| 模块 | 功能 | 状态 | 后端接口 |
|---|---|---|---|
| 首页 / 文档 | 返回门户 / 打开 VitePress | ⚪ | 无 |
| 笔记本 | 笔记本建删改名 + 笔记增删改查（.md） | ✅ | `/api/notebooks*`、`/api/notes*` |
| 邮箱 | 收件箱、文件夹建删改、已读、移动、删除、未读角标（5s 轮询） | ✅ | `/api/messages/*`（folders/inbox/read/move/unread-count） |
| 设置 | 主题/色相/字号/侧栏、改密码、最近打开 | ✅ | `GET/PUT /api/user/settings`、`POST /api/user/change-password` |
| 自定义头像 | 学生专属，≤2MB 上传 | ✅ | `POST /api/update-avatar` |
| 作品仓 | "展示你的所有 Scratch 作品" | 🟡 | **无接口（占位）** |
| 勋章墙 | 成就徽章 | 🟡 | **无接口（占位）** |
| 课程广场组件 | `student/CoursePlaza.tsx` **定义了但无任何 import（死代码）** | ❓ | 未接 |

#### ④ 管理后台（AdminDashboard，2 个 tab）

| Tab | 功能 | 状态 | 后端接口 |
|---|---|---|---|
| 用户管理 | 创建老师/学生、列表、删除用户（软删进垃圾桶，admin 自己除外） | ✅ | `GET /api/users`、`POST /api/register`、`DELETE /api/users/{name}` |
| 垃圾桶 | 用户恢复/永久删除/清空 | ✅ | `GET/DELETE /api/trash`、`POST /api/trash/restore/{id}`、`DELETE /api/trash/{id}` |
| 班级管理页 | — | ❓ | **`/api/admin/classes*` 接口存在，admin UI 未接**（教师用的是 `/api/classes`） |

#### ⑤ 看板外消费方（重构不许碰）

Scratch 二开（外链 + 本地存储 sb3）、VitePress 文档站、Caddy 静态（share/docs）、控制面板（启停）、FastAPI `/docs`。

#### ⑥ 后端有、看板未调用的接口（联审清单，重构期别当死代码删）

| 接口 | 推测调用方 | 处置 |
|---|---|---|
| `POST /api/courses/{name}/materials/{lesson}/save-sb3` | 疑似 Scratch 侧/备用（scratch-gui 源码也未搜到） | **待联审**，默认保留（C-06） |
| `POST /api/courses/{name}/materials/{lesson}/upload` | 看板上传全走 `/tree/upload`，此为旧入口 | 待联审，默认保留 |
| `PUT /api/courses/{course_id}/featured` | 旧书架语义兼容 | 保留（C-06） |
| `/api/admin/classes*`（6 个） | admin 无班级页 | 保留，待产品决定是否补 admin UI |

### 2.8 功能域分类（F-xx · 设计实现的输入）

> 把 91 个接口横向切成 **10 个功能域 + 1 个地基**。功能域 = 未来后端模块的天然边界（→ 第 5 章）。

| 编号 | 功能域 | 覆盖功能（按端） | 接口数 | 现有载体 |
|---|---|---|---|---|
| F-01 | 认证与会话 | 登录/登出/限流/token/profile/改密（全端） | 6 | auth.py |
| F-02 | 账号与角色 | 创建老师/学生、列表、软删、头像、描述符（admin+本人） | 7 | users.py |
| F-03 | 班级与学生 | 班级 CRUD、进出班、重名查找、课表（教师后台+admin 接口） | 9 | classes.py |
| F-04 | 课程与课件 | 课程 CRUD、书架、发布、文件树、课件、下载（门户+教师后台） | 24 | courses.py + file_tree.py |
| F-05 | 资料与软件 | 资料 CRUD、文件树、打包下载（门户+教师后台） | 11 | resources.py + file_tree.py |
| F-06 | 站内信 | 教师→学生收发、文件夹、已读、角标（教师+学生后台） | 10 | messages.py |
| F-07 | 笔记 | 笔记本/笔记 .md CRUD（学生后台） | 9 | notes.py |
| F-08 | 课堂记录 | 按月保存/查看/删除、跨老师搜（教师后台） | 5 | sessions.py |
| F-09 | 回收站 | 用户版(DB) + 班级/课程/资料版(文件) 两套（admin+教师后台） | 7 | trash.py |
| F-10 | 用户设置 | 主题/字号/侧栏/最近打开（全端） | 3 | settings.py |
| **F-00** | **地基（横切）** | 鉴权、路径安全、原子写+文件锁、DB、备份、自检、信封 | 0（库函数） | utils.py / main.py |
| | 合计 | | **91** | |

**分类观察（设计时要回答的三个问题）**：
1. F-04 与 F-05 共用 file_tree.py 一套树逻辑 → 历史分叉教训，**共用基座不许复制第二份**。
2. F-09 两套回收站并存（DB 版管用户、文件版管内容）→ 第 5 章要定：合并还是维持分立（→ D-05）。
3. F-00 散落在 utils.py 631 行里，与 F-01~F-10 的业务函数混放 → 拆分方向：F-00 进 `core/`，业务回各功能域（P-01 的解法）。

---

## 第 3 章 · 约束与红线（C-xx）【起草，请逐条确认】

### 3.1 产品定论（历史上拍过板的，重构不得翻案）

| 编号 | 红线 |
|---|---|
| C-01 | **URL 风格债不修**：动词 URL、单复数混用、course_name/course_id 定位混用 → 一律保留。改 = 100+ 接口联动 + 前端全动，零收益（2026-09-26 定论） |
| C-02 | **公开只读接口是故意的产品设计**（低龄学生登录困难 → 不要求登录）：`GET /api/users`、`GET /api/courses`（含 materials）、`GET /api/resources`（含 tree/download）、`POST /api/login`。不是安全漏洞，以后评审不许当漏洞修 |
| C-03 | **成功信封统一** `{"status":"success", ...}`；失败走 `HTTPException` → `{"detail"}`。接受成功/失败格式不对称，不改 |
| C-04 | **不支持自主注册**：教师账号 admin 创建，学生账号 admin/teacher 创建 |
| C-05 | **course_name 定位在重命名瞬间会 404** → 判定无所谓（刷新即恢复），不修 |
| C-06 | 兼容字段不许删：`featured`（→shelf）、旧 `schedule` list 格式、`material_count` 等旧前端缓存依赖 |

### 3.2 兼容性约束

| 编号 | 红线 |
|---|---|
| C-10 | **前端零改动**：React 看板/门户/Scratch 不因本次重构改一行；接口路径、入参、出参保持不变 |
| C-11 | **Caddy 零改动**：路由配置、反代规则不动 |
| C-12 | **数据零迁移**：`qingcheng.db` 与 `data/` 目录布局原样；重构只动 `backend/` 代码 |

### 3.3 工程红线

| 编号 | 红线 |
|---|---|
| C-20 | **鉴权模式不变**：middleware 只解析不拦截；每个 handler 第一行 `require_role`/`get_current_user`；重构不得把鉴权挪进中间件统一拦截（会误伤 C-02 公开接口） |
| C-21 | **文件优先、数据库轻量**：账号/管理侧 → SQLite；内容资产（sb3/课件/笔记/资源）→ 文件系统。不许把大文件塞进 DB |
| C-22 | **权限与数据安全零妥协**（用户最焦虑点）：写操作必须带鉴权调用；路径输入必过 `_safe_path`/`_valid_new_name`；AI 生成的路径/权限代码必须人工审查 |
| C-23 | **新接口纪律**：写操作必须带鉴权；成功必须带 `status:"success"`；出参按需写 `response_model`（存量不动） |
| C-24 | **并发写安全**：读-改-写 JSON 必须 `_file_lock` 整体持锁 + 原子写；重构不得引入"先读后写不加锁" |
| C-25 | **面向 LLM 的设计约束**（对应 D-02）：写操作接口保持**原子化**（一次一件事）、尽量**幂等**（AI 重试无副作用）、返回**结构化 JSON**、鉴权对 AI 调用方同样生效（**不为 AI 开后门**） |

---

## 第 4 章 · 范围与阶段计划 【框架已搭，待第 1 章定稿后切分】

### 4.1 开工前置（P0 · 关两道门）

| 步骤 | 操作 | 出口标准 |
|---|---|---|
| 1 | 本地 `git config --unset core.hooksPath`（恢复默认前先确认 pre-push 快测会拦截重构期 push） | 旧钩子不再拦截 |
| 2 | SSH 服务器删除 `CodeNova-CI` 计划任务（`schtasks /delete /tn CodeNova-CI /f`） | 看门狗不再跑旧测试 |
| 3 | 记录基线：本档 2.2 的 91 接口表 + 当前 commit `c1e6031d` | 基线已归档 |

> 完成重构 → **按新设计重建测试** → 重新打开两道门（钩子 + 看门狗）。

### 4.2 阶段模板

每个阶段必须按同一模板填写：

```
#### P<n> · 阶段名
- 范围：动哪些文件/模块（引用 2.2 模块编号）
- 入口标准：上一阶段的出口标准
- 工作内容：
- 出口标准（可执行）：如「快测 199 全过」「91 接口对表完成」
```

### 4.3 阶段划分 【待填 —— 等 1.2 目标定稿后切】

| 阶段 | 范围（占位） | 出口标准 |
|---|---|---|
| P0 | 关两道门（4.1） | 两道门已关，基线已归档 |
| P1 | （待定：候选=拆 utils.py，接口/数据零变动） | 快测 199 全过 |
| P2 | （待定） | |
| Pn | 重建测试、重开两道门 | 全量 215 全过 + 第 6 章全部 V-xx 达成 |

**阶段纪律**：出口标准只允许写可执行语句（测试 X 全过 / 对表完成）；没到出口标准不许开下一阶段。

---

## 第 5 章 · 目标架构 【草案已出，待确认】

> 本章是重构的施工蓝图。分组直接继承 2.8 的功能域（F-00~F-10），保证 91 个接口一一对应、URL 零变动（C-01/C-10）。

### 5.1 模块划分（基于功能域，每个模块：职责 / 接口范围 / 依赖）

**三层规则：router（薄）→ service（业务）→ core（地基）。**

| 模块 | 职责 | 接口范围 | 依赖 |
|---|---|---|---|
| `core/security.py` | 密码哈希、token 签发/校验、`get_current_user`、`require_role`、DB 连接 | F-00 | 无 |
| `core/files.py` | `_safe_path`、`_valid_new_name`、原子写、文件锁、自然排序 | F-00 | 无 |
| `core/envelope.py` | 成功信封/错误信封助手（可选，先评估 199 测试的响应断言） | F-00 | 无 |
| `modules/auth.py` | 登录、登出、梯度限流、profile、改密 | F-01 | core/security |
| `modules/accounts.py` | 用户列表/创建/软删/头像/描述符 | F-02 | core/security、core/files |
| `modules/classes.py` | 班级与学生（classes.json）、admin/classes、重名查找 | F-03 | core/files |
| `modules/courses.py` | 课程 CRUD、书架、发布、课件、save-sb3 | F-04 | core/files、file_tree |
| `modules/resources.py` | 资料/软件 CRUD、打包下载 | F-05 | core/files、file_tree |
| `modules/file_tree.py` | 树 walk/reorder/upload/rename/delete（**F-04/F-05 唯一共享件**，现状保留单文件） | F-04/F-05 | core/files |
| `modules/messages.py` | 站内信收发、文件夹、未读 | F-06 | core/files |
| `modules/notes.py` | 笔记本/笔记 | F-07 | core/files |
| `modules/sessions.py` | 课堂记录按月存取 | F-08 | core/files |
| `modules/trash.py` | 用户版(DB)+文件版 两套回收站 | F-09 | core/files |
| `modules/settings.py` | 用户设置/最近打开 | F-10 | core/files |
| `routers/*.py`（现 10 个） | **只做四件事**：解析参数 → 第一行鉴权 → 调 service → 返回信封 | 91 接口 | modules |
| `main.py` | 装配、middleware 解析、启动自检、admin 种子、备份调度 | — | 全部 |

### 5.2 分层规则（路由层做什么、服务层做什么、绝不做什么）

- **router 层**：❌ 不写业务逻辑、不直接拼路径、不直接读写文件；✅ 解析参数、鉴权（C-20：handler 第一行，不进中间件）、调 service、包信封（C-03）。
- **service 层**：✅ 业务规则、落点决策、锁与原子写的调用点；❌ 不 import FastAPI 的 Request（保持可单测）。
- **core 层**：✅ 全局基座纯函数；❌ 不 import 任何 modules（禁止反向依赖）。
- **绝不做**：中间件统一拦截鉴权（会误伤 C-02）、在 service 里绕过 `_safe_path`、"先读后写"不加锁（C-24）、为省事复制第二份 file_tree。

### 5.3 目标目录结构（tree 图）

```
backend/
├── main.py                  # 装配 + middleware + 启动自检
├── models.py                # Pydantic 请求/响应模型（暂不动，或随各模块就近迁）
├── backup.py                # 每日备份（保留）
├── core/
│   ├── security.py          # ← utils: 密码/token/鉴权/DB
│   └── files.py             # ← utils: 路径安全/原子写/文件锁/杂项工具
├── modules/                 # ← utils 中各业务函数归位（5.4 映射表逐条迁移）
│   ├── auth.py  accounts.py  classes.py  courses.py  resources.py
│   ├── messages.py  notes.py  sessions.py  trash.py  settings.py
│   └── file_tree.py         # 从现 file_tree.py 平移（分组归属 F-04/F-05）
└── routers/                 # 现 10 个 router，变薄，URL 全部不动
    └── auth/users/classes/courses/resources/messages/notes/sessions/trash/settings.py
```

> 迁移纪律：utils.py 只减不增；每迁一批函数 → 快测 199 全过 → 再迁下一批（P-01 的解药，见第 4 章）。

### 5.4 utils.py 函数去向映射表 ★ 施工图

> 631 行、40+ 函数逐条映射到新家。**这张表是重构执行的核心，缺它不许动工。**

| utils.py 函数 | 现有职责 | 新去处（待定） |
|---|---|---|
| hash_password / verify_password / generate_token / verify_token / get_db / get_current_user / require_role | 认证鉴权 | |
| _safe_path / _valid_new_name / _atomic_save / _atomic_write_file / _file_lock | 路径与写安全（全局基座） | |
| get_user_dir / _resolve_user_dir / write_info_json / ensure_user_dirs / self_check_user_dirs | 用户目录 | |
| move_to_trash / restore_from_trash / _move_to_trash / _list_trash_items | 回收站 | |
| _load_classes / _save_classes / _get_classes_path | 班级 | |
| _load_sessions / _save_sessions / _get_session_path | 课堂记录 | |
| _get_teacher_course_dir / _gen_course_id / _resolve_course_format / _read/write_course_meta / _read/write_resource_meta / _natural_sort_key / _read_order_file | 课程与资源 | |
| _get_msgs_dir / _write_msg / _find_msg_file | 站内信 | |
| 各 Pydantic Form | 请求模型 | |

（对照 6.1 验收：映射表填完 = 重构可开始）

---

## 第 6 章 · 验收标准（V-xx）【起草，请确认】

| 编号 | 验收项 | 执行方式 |
|---|---|---|
| V-01 | 快测 199 个全过 | `pytest tests/ --ignore=tests/test_e2e.py` |
| V-02 | 全量 215 个全过（收尾时跑一次） | `pytest tests/` |
| V-03 | **91 接口逐条对表**：2.2 每行的路径/方法/鉴权/数据落点与重构后一致 | 对照 2.2 人工+脚本核对 |
| V-04 | 前端真实流程手测通过：登录 → 课程增删改/文件树拖拽 → 资料浏览下载 → 站内信 → 回收站恢复 → 笔记 → 课堂记录 | dev 环境走一遍 |
| V-05 | 痛点闭环：1.1 每条 P-xx 在 5 章有对应解法 | 交叉引用检查 |
| V-06 | 仓库零 diff：`qx-dashboard-ts`、Caddyfile、`frontend/` 不因重构产生改动 | `git status` 各仓库干净 |
| V-07 | 横切能力清单（2.5）逐项存活：限流/路径防护/文件锁/备份/自检 | 逐项核对（快测已覆盖大部分） |
| V-08 | 红线（第 3 章）零违反 | 评审过一遍 C-01~C-25 |

---

## 第 7 章 · 迁移、回滚与 Out of Scope

### 7.1 迁移与回滚

- **迁移策略**：数据零迁移（C-12）。只动 `backend/` 代码，SQLite 与 `data/` 原样。
- **回滚方案**：出问题 `git revert` / checkout 回基线 `c1e6031d` → 重启服务即恢复，无数据转换风险。
- **每阶段回滚**：阶段出口标准未过 → 不入下一阶段，回退该阶段提交。

### 7.2 Out of Scope（明确不做）

| 编号 | 项目 | 说明 |
|---|---|---|
| O-01 | LLM 实际接入 | 本期只保留 C-25 设计约束，不做接入（对应 D-02 提案） |
| O-02 | 存量 URL 改造 | C-01 决定；新接口才按 RESTful（D-01） |
| O-03 | 换数据库 / 引入 ORM 重封装 | SQLite + 现有 SQLAlchemy 用法保持 |
| O-04 | 前端/Caddy/Scratch 改动 | C-10/C-11 |
| O-05 | （自填） | |

---

## 附录 A · 设计决策记录（D-xx）

| 编号 | 议题 | 状态 | 结论 / 待办 |
|---|---|---|---|
| D-01 | 是否采用 RESTful API 命名规范 | **待定** | 建议：**新增**接口按 RESTful；**存量 91 个一律不动**（依据 C-01）。拍板人：用户 |
| D-02 | 接入本地 LLM，网页 AI 直接执行任务 | **提案** | 本期不接入（O-01），但设计约束已落 C-25（原子化/幂等/结构化/鉴权不豁免）。本地 LLM 已可访问接入，未来单独立项 |
| D-03 | 全计划分阶段实现、分阶段测试 | **已定** | 见第 4 章 |
| D-04 | （后续决策随手记这里） | | |
| D-05 | F-09 两套回收站（用户=DB 版 / 内容=文件版）合并还是维持分立 | **待定** | 建议：**本期维持分立**（合并要动数据布局，违反 C-12 零迁移）。拍板人：用户 |

> 决策纪律：状态只有 `已定 / 待定 / 提案` 三种；「待定」必须写拍板人；不许用"后续考虑"糊弄。

## 附录 B · 术语表（同物异名防埋雷）

| 术语 | 定义 |
|---|---|
| 课程 | `data/courses/{teacher}_course/` 下的一个文件夹（=course_id），有 course.json |
| 课件 | 课程内的子目录（lesson），Scratch 课程里一个课件是一个作品文件夹 |
| 资料 / 资源 | `data/resources/` 下的顶层条目（resource.json），含软件下载（category=download）。**界面文案"资料"，代码/接口用 resource，同一个东西** |
| 书架 shelf | 课程的收纳位置：`books`=书架（不用），`courses`=课程（正在教）；旧名 featured |
| 两套回收站 | DB 版（`/api/trash`，删掉的**用户**，仅 admin）；文件版（`/api/trash/items`，班级/课程/资料） |
| 描述符 qc_descriptors | users 表 JSON 文本列，存 `{teacher: ...}` 等扩展属性 |
| teacher_key | 磁盘目录名化的老师标识（URL/路径用），与显示名分离 |
| 快测 / 全量 | 快测=199 个（跳过 e2e）；全量=215 个（含 e2e） |

---

*文档生成：2026-10-04 · 基线 `c1e6031d` · 下一步：填写第 1 章（1.1 痛点确认 → 1.2 目标一句话 → 1.3 范围），然后切分 4.3 阶段。*
