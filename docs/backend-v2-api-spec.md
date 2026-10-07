# CodeNova 后端 V2 · 接口规范

> **状态**：📐 草案 v0.1（待用户确认）
> **前置输入**：[backend-refactor-requirements.md](./backend-refactor-requirements.md) 第 2 章现状盘点（91 接口 / 10 功能域 / 权限矩阵）
> **代码位置**：`qc-pocket-space/backend_v2/`（与旧后端并排，旧服务照常运行不动）
> **切换方式**：本月并行开发，前后端一起改，完成一次性切换（无版本号共存期）

---

## 0. 已定决策

| # | 决策 | 结论 |
|---|---|---|
| D1 | 技术栈 | FastAPI + SQLAlchemy（SQLite） |
| D2 | 范围 | 后端 + 前端一起改，本月完成，一次性切换 |
| D3 | 数据迁移 | 不做工程化迁移，必要时一次性小导入（单人数据，有备份） |
| D4 | 接口风格 | RESTful 资源路径 + 统一信封 `{code, msg, data}` |
| D5 | 鉴权 | 服务端会话表（可注销、可踢下线，重启不丢） |
| D6 | 数据组织 | 结构化数据全部入库；大文件（sb3/课件/图片）留磁盘 |
| D7 | Schema 演进 | **上线前允许推翻重来，上线后只增不改** |
| D8 | 代码位置 | `qc-pocket-space/backend_v2/` |
| D9 | URL 前缀 | `/api/...`，不带版本号 |
| D10 | 回收站 | V1 的"用户=DB 版 + 内容=文件版"两套**合并**为一张 `trash` 表 + 磁盘目录 |
| D11 | 全新功能域（作品分享 / 统计 / AI 等） | **不预留、不提前设计**（待定，拍板人：用户）。要做时按 §8.1 循环新增：§3 追加接口行 → 新表 → 新 router → 先写测试 → main.py 挂一行 → 更新 README。地基不含业务假设，天然可扩 |
| D12 | 运行时 | **复用 V1 的 `qc-pocket-space/runtime/`（python/caddy/node/vitepress-template），不复制**。生产用 `runtime/python`（已实测含 V2 全部依赖），测试用 `tests/venv`（同为 Python 3.12.13，无漂移）。Caddy 在 M0~M4 完全不碰（V2 跑 8001 不进反代），M5 切流时用 `runtime/caddy/generate_caddyfile.py` 重新生成配置。vitepress 原样复用（M4 电子书发布调用同一模板） |
| D13 | 课程与资料的关系（2026-10-07 拍板） | **词汇统一为"资产家族"，表分开**（`courses` / `resources` 不合表）。共享层已合一（文件树同一套 service、`file_order`、`trash`、打包下载、权限、路径安全）；各自独有列/接口留在各域（课程：publish/ebook/shelf/课件；资料：category/zip）。不做 `assets` 总表；前端如需"资产总览"用**读侧拼接**（查两表合并返回），不合并存储。 |
| D14 | 课件代码保密（2026-10-07 拍板） | **服务端不按文件名拦截**（学生可下 `main.sb3`，维持 V1 现状），代码保密由魔改 Scratch 客户端按角色选择文件实现（学生→student.sb3/回退 main，教师→main）。后端保持格式不可知。附魔改 Scratch 集成事实（M5 改造清单）：① 保存 = 两次 upload（`save-sb3?file_type=` 砍掉后改 `files/upload` + `path=/课件/main.sb3`）；② 加载 = `fetch(folder+文件名)` 改走 `files/download?path=`；③ **F3 上传须自动创建路径中间层目录**（Scratch 只给完整路径不先建夹）；④ 双 sb3 约定仅存在于课程课件目录，资料域无此概念。 |

---

## 1. 通用约定

### 1.1 统一信封

```jsonc
// 成功（HTTP 200）
{ "code": 0, "msg": "ok", "data": { ... } }

// 失败（HTTP 4xx/5xx）
{ "code": 40001, "msg": "班级名称已存在", "data": null }
```

- `code`：`0` = 成功，非 0 = 业务错误码（见 1.3）
- `msg`：面向人的中文说明，前端可直接展示
- `data`：业务数据；列表统一 `{ "items": [...], "total": n }`；无数据时 `null`
- **HTTP 状态码与 `code` 大类同步**（前端先看 HTTP，再看 `code`）

### 1.2 HTTP 状态码 ↔ 信封 code 大类

| HTTP | 含义 | code 段 |
|---|---|---|
| 200 | 成功 | `0` |
| 400 | 参数校验失败 | `10xx` |
| 401 | 未登录 / token 失效 | `20xx` |
| 403 | 权限不足 | `20xx` / `30xx` |
| 404 | 资源不存在 | 各域 `xx04` |
| 409 | 重名冲突 | 各域 `xx09` |
| 413 | 文件超限 | `5113` |
| 429 | 登录限流 | `2029` |
| 500 | 服务端异常 | `9000`（msg 固定"服务繁忙"，细节只进日志） |

### 1.3 错误码分域（4 位：域×域×序号）

| 段 | 域 | 示例 |
|---|---|---|
| `0` | 成功 | `0` |
| `1xxx` | 通用（参数、格式、文件） | `1001` 参数缺失 · `1002` 格式错误 · `1004` 不存在 · `1009` 冲突 · `1113` 文件超限 |
| `2xxx` | 认证与会话 | `2001` 账号或密码错误 · `2002` 未登录 · `2003` token 过期 · `2004` 账号已停用 · `2029` 登录过于频繁 |
| `3xxx` | 账号 | `3001` 用户名已存在 · `3003` 无权创建该角色 · `3004` 目标账号不存在 · `3010` 不能删除自己 |
| `4xxx` | 班级与学生 | `4001` 班级名已存在 · `4004` 班级不存在 · `4009` 学生已在班内 |
| `5xxx` | 课程与课件 | `5001` 课程名已存在 · `5004` 课程不存在 · `5010` 路径非法 · `5011` 文件已存在 · `5113` 文件超限 |
| `6xxx` | 资料与软件 | `6001` 资料名已存在 · `6004` 资料不存在 · `6010` 路径非法 |
| `7xxx` | 站内信 | `7004` 消息不存在 · `7003` 无权发送（仅教师→学生）· `7010` 文件夹受保护 |
| `8xxx` | 笔记 | `8004` 笔记不存在 · `8009` 笔记本名重复 |
| `9xxx` | 服务端 / 未分类 | `9000` 服务繁忙 |

> 纪律：**新错误码随手加进上表**；msg 必须可直接展示给用户（不出现英文堆栈）。

### 1.4 鉴权

- 请求头：`Authorization: Bearer <token>`
- 登录签发 token → 写入 `auth_sessions` 表；登出删行；**重启不丢**（V1 是内存表，重启全员掉线）
- token 有效期 **7 天**，过期后接口返回 `2003`，前端跳登录页
- **游客身份不再由中间件伪造**：未登录访问受保护接口直接 `401`；公开接口白名单见各域标注
- 登录梯度限流保留（按真实 IP）：5 次→30s / 10 次→5min / 15 次→30min / 20 次→2h

### 1.5 公开接口（产品定论，前端改版后仍保留）

未登录可访问（只读）：

| 接口 | 用途 |
|---|---|
| `POST /api/auth/login` | 登录 |
| `GET /api/accounts` | 门户教师列表（**只返回 id/账号名/头像/角色**，不泄漏描述符等） |
| `GET /api/courses`、`GET /api/courses/{id}`、`GET /api/courses/{id}/lessons` | 课程广场 |
| `GET /api/resources`、`GET /api/resources/{id}`、文件树只读、`GET /api/resources/{id}/download` | 资料浏览与软件下载 |

### 1.6 其他约定

- **ID**：所有资源用不可猜测短 id（12 位字母数字），**不拿显示名当主键**（V1 的 course_name 定位 404 问题从根上消除）
- **时间**：`YYYY-MM-DD HH:MM:SS`（本地时间）
- **删除**：进回收站（软删）；"彻底删除"是独立动作 —— **账号类例外：只停用、永不彻底删（2026-10-07 拍板，A6）**
- **写接口幂等**：同参数重复提交不产生重复数据（覆盖式保存用 `PUT`）
- **文件上传**：`multipart/form-data`，字段 `file` + `path`（相对路径）；课件/树上传 ≤50MB，头像 ≤2MB
- **分页**：消息、笔记支持 `?page=&page_size=`（默认 20）；其余列表数据量小，默认全量返回
- **健康检查**：`GET /api/health` → 信封返回 `{status:"up", version}`，供前端/部署探活（无鉴权）
- **鉴权位置**：每个 handler 第一行做权限判断，**不进中间件统一拦截**（避免误伤公开接口）
- **命名约定**（2026-10-06 定稿，此后所有接口必须遵守）：
  - 路径参数 → **camelCase**：`/api/class-records/{classId}/{date}`
  - 查询参数 → **snake_case**：`?class_id=&page_size=`
  - 资源名 → **全复数名词**；多词资源用连字符：`/api/message-folders`、`/api/class-records`
  - 一个资源一个前缀，不混用动词式 URL（动作型例外见 §1.7）

### 1.7 动作型端点（有意保留 · 评审时不必当"不规范"改）

RESTful 的目的是**可预测**，不是纯度。以下 16 类接口使用动词/动作子路径，是权衡后的行业通行做法：

| 接口 | 保留理由 |
|---|---|
| `POST /api/auth/login`、`/logout`、`GET /api/auth/me` | 全行业惯例（GitHub 用 `/user`）；改成 `DELETE /api/sessions` 反而难懂 |
| `POST .../files/upload`、`/mkdir`、`/rename`、`/order` | 纯 REST 要 `PUT /files/{path}`，但路径含 `/` 需 URL 转义，**前端会踩坑** |
| `POST /api/courses/{id}/publish` | 发布是一次性动作，不是资源 |
| `POST /api/trash/{id}/restore` | 恢复是一次性动作 |
| `GET /api/resources/{id}/download`、`GET .../files/download` | 必须是 GET：浏览器 `<a href>` 直链下载、公开免登录 |
| `GET /api/messages/unread-count` | 聚合值，业界通行 |
| `POST /api/settings/recent-opens` | 记录一条历史，POST 创建语义成立 |
| `POST /api/accounts/batch` | 对集合的**批量**创建，单条创建已有 `POST /api/accounts` |
| `PUT /api/accounts/{id}/avatar/preset` | 幂等"放到某个预设"，PUT 放置语义 |

**新接口纪律**：先想"这是资源还是动作"——是资源就用 CRUD（§1.6 命名约定），确属一次性动作才开动作端点，并在本表登记理由。

---

## 2. 数据表设计（草案 · D7：上线前可推翻）

大文件（`.sb3`、课件、视频、头像、压缩包）永远在 `data/` 磁盘目录，库里只存路径。

```sql
-- 账号与会话
accounts(
  id PK, username UNIQUE, password_hash, role(admin|teacher|student),
  avatar_path, descriptors JSON,                 -- 描述符（原 qc_descriptors）
  status(active|deleted), created_at
)
auth_sessions(
  token PK, account_id FK, ip, user_agent, created_at, expires_at
)
login_attempts(                                      -- 梯度限流计数
  pk, ip, username, fail_count, window_start
)

-- 班级与学生
classes(id PK, owner_id FK, name, schedule JSON, created_at)   -- schedule: 每周排课
class_members(
  class_id FK, account_id FK, joined_at,
  UNIQUE(class_id, account_id)
)

-- 课程与资料（元信息入库，文件本体在磁盘）
courses(
  id PK, owner_id FK, name, type, format, description,
  tags JSON, shelf(books|courses), publish JSON, created_at
)
resources(
  id PK, owner_id FK, name, description, tags JSON,
  category(reference|download), format, created_at
)
file_order(                                          -- 文件树自定义顺序（原 _order.json）
  scope(course|resource), scope_id, parent_path, name, position
)

-- 站内信
message_folders(id PK, owner_id FK, name, builtin BOOL, UNIQUE(owner_id,name))
messages(
  id PK, sender_id FK, receiver_id FK, folder_id FK,
  title, content, is_read, created_at
)

-- 笔记
notebooks(id PK, owner_id FK, name, created_at, UNIQUE(owner_id,name))
notes(id PK, notebook_id FK, title, content TEXT, updated_at)

-- 课堂记录（原按月 JSON 文件）
class_records(
  class_id FK, date(YYYY-MM-DD), content TEXT, updated_at,
  UNIQUE(class_id, date)                             -- 同日覆盖天然幂等
)

-- 设置
settings(
  account_id PK, theme, hue, font_size, sidebar,
  recent_opens JSON                                  -- 上限 6 条
)

-- 统一回收站（原"用户 DB 版 + 内容文件版"两套合一，D10）
trash(
  id PK, type(account|class|course|resource),
  owner_id FK, name, payload JSON,                   -- 恢复所需快照
  disk_path,                                         -- 内容类：被移动的目录
  deleted_at
)
```

**关键改动 vs V1**：
1. 班级、笔记正文、站内信、课堂记录、设置 —— 全部从磁盘 JSON 收进 SQLite，**文件锁/原子写/读改写竞态整类问题消失**
2. 显示名与 id 分离（V1 用名字当键，改名瞬间 404）
3. 两套回收站合并为一张 `trash` 表 + 磁盘目录（D10）
4. 会话、限流计数从内存进库，重启不丢

---

## 3. 接口清单

> 「权限」列：`公开` = 无需登录；`本人` = 仅本人数据；`teacher` / `admin` = 角色白名单。
> V1 的 91 个接口 → V2 **76 个**（合并动词式冗余、搜索并入列表过滤、去掉纯兼容接口，见 §5；2026-10-06 规范整改 + 账号域补 3 个功能后——原清单接口因头像走前端 emoji 方案已删）。

### 3.1 认证与会话（auth）

| 方法 | 路径 | 功能 | 权限 |
|---|---|---|---|
| POST | `/api/auth/login` | 登录，签发 token（梯度限流） | 公开 |
| POST | `/api/auth/logout` | 注销当前 token | 持 token |
| GET | `/api/auth/me` | 当前账号资料（含设置摘要） | 登录 |
| PATCH | `/api/auth/password` | 改密（验原密码，改后**其他会话全部失效**） | 登录 |

### 3.2 账号（accounts）

| 方法 | 路径 | 功能 | 权限 |
|---|---|---|---|
| GET | `/api/accounts` | 账号列表（`?role=teacher\|student`）；公开访问时字段裁剪 | 公开（裁剪）/ admin |
| POST | `/api/accounts` | 创建账号（role=admin 判定：建老师需 admin，建学生需 admin/teacher） | admin / teacher |
| POST | `/api/accounts/batch` | **批量注册**：`{role, password?, usernames[]}`，已存在的跳过不报错，返回 created/skipped 明细；`password` 缺省用默认密码 | admin |
| GET | `/api/accounts/{accountId}` | 账号详情 | 登录（他人仅基本字段） |
| PATCH | `/api/accounts/{accountId}` | 改**登录名**（2026-10-07 拍板：可改；本人限 teacher/admin，**学生一律不可改**，admin 可改任何人；重名 → 3001）/ 描述符 / 状态（**无昵称：显示名即账号名，2026-10-07 砍掉 display_name**） | 本人 / admin |
| DELETE | `/api/accounts/{accountId}` | 停用账号（软删 → 回收站，admin 不可删自己） | admin |
| POST | `/api/accounts/{accountId}/password` | **管理员重置密码**（≥6 位；重置后该账号全部会话失效，需重新登录） | admin |
| POST | `/api/accounts/{accountId}/avatar` | 上传头像（**≤5MB**，仅学生；磁盘写入失败 → `507/9001`） | 本人 |
| PUT | `/api/accounts/{accountId}/avatar/preset` | **选 emoji 预设头像** `{preset:"avatar_07"}` → **只存标记串**；任意角色，仅限本人；切换时清掉自定义文件 | 本人 |
| GET | `/api/accounts/{accountId}/avatar` | 取**自定义上传**的图片；**标记（预设）或文件丢失 → 404/1004**，前端据此画默认 emoji | 公开 |

> **头像设计（2026-10-06 方案一）**：emoji 头像库活在**前端常量**（V1 的 `AVATARS`，20+ 个），后端只存 `avatar_XX` 标记——**不存预设图片、不设清单接口**（两个真相源必坏其一）。原 `GET /api/avatars/presets` 与 8 个 SVG 矢量图已删除。

> V1 的 `POST /api/register`、`/api/update-avatar`、`/api/update-descriptors`、`/api/user/change-password` 收敛到上面 4 个资源动作。

### 3.3 班级与学生（classes）

| 方法 | 路径 | 功能 | 权限 |
|---|---|---|---|
| GET | `/api/classes` | 我的班级（admin 看全部） | 登录 |
| POST | `/api/classes` | 建班（重名校验） | teacher |
| GET | `/api/classes/{id}` | 班级详情（含学生、课表） | 成员 / admin |
| PATCH | `/api/classes/{id}` | 改名 / 改排课 | teacher（本人班） |
| DELETE | `/api/classes/{id}` | 删班（进回收站） | teacher（本人班） |
| POST | `/api/classes/{id}/students` | 加学生（已有账号挂接 / 新建） | teacher |
| ~~PATCH~~ | ~~`/api/classes/{id}/students/{accountId}`~~ | ❌ **已砍**（2026-10-07：学生个人上课日暂不用，等真需要再加） | — |
| DELETE | `/api/classes/{id}/students/{accountId}` | 移出班级（**不删账号**） | teacher |
| GET | `/api/students?q=` | 全体学生列表；带 `q` 时跨班模糊搜索（搜索=列表的过滤条件，不设独立端点） | teacher |
| ~~GET~~ | ~~`/api/students/search`~~ | ❌ 已并入上一行（2026-10-06 规范整改） | — |

### 3.4 课程（courses）

| 方法 | 路径 | 功能 | 权限 |
|---|---|---|---|
| GET | `/api/courses` | 课程列表（`?shelf=books\|courses`、`?owner=`） | 公开 |
| POST | `/api/courses` | 建课程（生成唯一 id） | teacher / admin |
| GET | `/api/courses/{id}` | 课程详情 | 公开 |
| PATCH | `/api/courses/{id}` | 改名称 / 描述 / 标签 / 类型 / 打开方式 | owner / admin |
| PUT | `/api/courses/{id}/shelf` | 移动书架（幂等放置：body `{shelf}`） | owner / admin |
| DELETE | `/api/courses/{id}` | 删课程（进回收站，含发布产物清理） | owner / admin |
| GET | `/api/courses/{id}/lessons` | 课件列表 | 公开 |
| DELETE | `/api/courses/{id}/lessons/{lesson}` | 删课件文件夹 | owner / admin |
| ~~POST~~ | ~~`.../lessons/{lesson}/save-sb3`~~ | ❌ 砍掉（V1 联审清单：看板未调用的旧入口）→ 作品保存统一走 `files/upload`（2026-10-06） | — |
| POST | `/api/courses/{id}/publish` | 电子书发布入队 | owner / admin |
| GET | `/api/courses/{id}/publish-status` | 发布状态 | owner / admin |

### 3.5 文件树（files · 课程与资料**共用一套**，单一事实来源）

> 同样的 6 个动作分别挂在 `/api/courses/{id}/files` 与 `/api/resources/{id}/files` 下，实现共用同一 service。

| 方法 | 路径 | 功能 | 权限 |
|---|---|---|---|
| GET | `.../files?path=` | 列目录（含自定义排序、文件大小/时间） | 同资源读权限 |
| GET | `.../files/download?path=` | 下载单文件 | 同上 |
| POST | `.../files/upload` | 上传（multipart：`file` + `path`，≤50MB） | 写权限 |
| POST | `.../files/mkdir` | 建文件夹 | 写权限 |
| PATCH | `.../files/rename` | 重命名（body: `path`,`new_name`） | 写权限 |
| DELETE | `.../files?path=` | 物理删除（树内删除，不经回收站） | 写权限 |
| POST | `.../files/order` | 拖拽排序（body: `path`,`names[]`,`position`） | 写权限 |

### 3.6 资料与软件（resources）

| 方法 | 路径 | 功能 | 权限 |
|---|---|---|---|
| GET | `/api/resources` | 列表（`?category=reference\|download`、`?owner=`） | 公开 |
| POST | `/api/resources` | 建资料 | teacher / admin |
| GET | `/api/resources/{id}` | 详情 | 公开 |
| PATCH | `/api/resources/{id}` | 改元信息 | owner / admin |
| DELETE | `/api/resources/{id}` | 删（进回收站） | owner / admin |
| GET | `/api/resources/{id}/download` | 下载（单文件直发 / 多文件实时打包 zip） | 公开 |
| — | `.../files/*` | 文件树 7 个动作（见 3.5） | 读公开 / 写 owner |

### 3.7 站内信（messages）

| 方法 | 路径 | 功能 | 权限 |
|---|---|---|---|
| GET | `/api/messages` | 收件箱（`?folder=`、分页） | 本人 |
| POST | `/api/messages` | 发消息（**仅教师→学生**） | teacher |
| GET | `/api/messages/{id}` | 读一条（自动置已读） | 收件人 |
| PATCH | `/api/messages/{id}` | 移动到文件夹 / 标记已读 | 收件人 |
| DELETE | `/api/messages/{id}` | 删除 | 收件人 |
| GET | `/api/messages/unread-count` | 未读总数（角标轮询） | 本人 |
| GET | `/api/message-folders` | 文件夹 + 未读统计 | 本人 |
| POST | `/api/message-folders` | 新建 | 本人 |
| PATCH | `/api/message-folders/{id}` | 重命名（内置"收件箱"不可改） | 本人 |
| DELETE | `/api/message-folders/{id}` | 删除（内置不可删，消息退回收件箱） | 本人 |

### 3.8 笔记（notebooks / notes）

| 方法 | 路径 | 功能 | 权限 |
|---|---|---|---|
| GET | `/api/notebooks` | 笔记本列表（含笔记计数） | 本人 |
| POST | `/api/notebooks` | 新建 | 本人 |
| PATCH | `/api/notebooks/{id}` | 重命名 | 本人 |
| DELETE | `/api/notebooks/{id}` | 删除（级联删笔记） | 本人 |
| GET | `/api/notes` | 笔记列表（`?notebook=`、分页） | 本人 |
| POST | `/api/notes` | 新建/保存笔记 | 本人 |
| GET | `/api/notes/{id}` | 读内容 | 本人 |
| PATCH | `/api/notes/{id}` | 改标题/内容（`updated_at` 更新） | 本人 |
| DELETE | `/api/notes/{id}` | 删除 | 本人 |

### 3.9 课堂记录（class-records）

> V1 路径 `/api/sessions` 与"登录会话"同名易混，V2 改名 `class-records`。

| 方法 | 路径 | 功能 | 权限 |
|---|---|---|---|
| GET | `/api/class-records?class_id=&month=&student=` | 按月查（倒序）；不带 month 查全部；带 `student` 跨班搜该生记录 | 登录 |
| GET | `/api/class-records/{classId}/{date}` | 单日详情 | 登录 |
| PUT | `/api/class-records/{classId}/{date}` | 保存当日（**同日覆盖，幂等**） | teacher |
| DELETE | `/api/class-records/{classId}/{date}` | 删单日 | teacher |
| ~~GET~~ | ~~`/api/class-records/search?student=`~~ | ❌ 已并入上一行（2026-10-06 规范整改） | — |

### 3.10 设置（settings）

| 方法 | 路径 | 功能 | 权限 |
|---|---|---|---|
| GET | `/api/settings` | 读设置（缺省字段补默认值） | 本人 |
| PUT | `/api/settings` | 改设置（主题/色相/字号 12-28/侧栏） | 本人 |
| POST | `/api/settings/recent-opens` | 记最近打开（去重置顶，上限 6） | 登录 |

### 3.11 回收站（trash · 两套合一，D10）

| 方法 | 路径 | 功能 | 权限 |
|---|---|---|---|
| GET | `/api/trash` | 列表（`?type=`；admin 全部，teacher 只看自己名下） | 登录 |
| POST | `/api/trash/{id}/restore` | 恢复（账号/班级/课程/资料各按 type 还原） | owner / admin |
| DELETE | `/api/trash/{id}` | 彻底删除（含磁盘目录）——**仅内容类（班级/课程/资料）；账号条目只可恢复，不提供彻底删** | admin |
| DELETE | `/api/trash` | 清空 | admin |

---

## 4. 权限矩阵（沿用 V1 角色模型）

角色：`admin > teacher > student > guest（未登录）`

| 操作 | admin | teacher | student | guest |
|---|:---:|:---:|:---:|:---:|
| 建教师账号 / 停用账号 / 清空回收站 / 彻底删除（内容类） | ✅ | ❌ | ❌ | ❌ |
| 建学生账号 / 建删改班级 / 课程与资料写操作 / 发消息 / 存课堂记录 | ✅ | ✅（限本人数据） | ❌ | ❌ |
| 浏览课程/资料/教师列表（公开只读） | ✅ | ✅ | ✅ | ✅ |
| 笔记、设置、头像、收发件箱 | ✅ | ✅ | ✅ | ❌ |
| 上传自定义头像 | ❌ | ❌ | ✅ | ❌ |

> 安全纪律：**每个写接口 handler 第一行必须有权限判断**；所有来自 URL/表单的路径段必过路径安全校验（防穿越）；权限与路径代码必须人工审查。

---

## 5. 相对 V1 的取舍

**砍掉（V2 不做）**：
- `PUT /api/courses/{course_id}/featured` 等旧字段兼容接口（前端同批改，无需双写）
- `POST /api/courses/{name}/materials/{lesson}/upload` 旧上传入口（统一走 `files/upload`）
- `POST .../materials/{lesson}/save-sb3`（V1 联审清单标注"看板未调用的旧入口"，作品保存统一走 `files/upload`）
- `/api/admin/classes*` 6 个 admin 专用班级接口（admin 直接用 `/api/classes`，权限放宽即可）
- `GET /api/students/search`、`GET /api/class-records/search`（搜索并入列表的 query 过滤参数）
- 动词式 URL（`/rename`、`/move`、`/read`…）→ 收敛为标准方法语义

**必须存活的横切能力（V1 → V2 对照）**：
| V1 能力 | V2 落点 |
|---|---|
| token 鉴权 | `auth_sessions` 表 + 7 天有效期 |
| 登录梯度限流 | `login_attempts` 表（重启不丢） |
| 路径穿越防护 | `core/files.py` 统一校验，文件树 service 唯一入口 |
| 原子写 + 文件锁 | **大部分随 JSON 入库消失**；磁盘顺序文件（`file_order`、发布状态）仍原子写 |
| 每日自动备份 | `backup.py` 平移（备份 `data/` + `*.db`） |
| 启动自检 | admin 种子 + 目录自检 |
| 电子书发布队列 | `build.py` 平移（vitepress 渲染） |
| 文件大小限制 | 50MB / 2MB |

---

## 6. 分阶段实施（每阶段可独立验收）

| 阶段 | 范围 | 出口标准 |
|---|---|---|
| **M0 地基** ✅ 已完成 | 项目骨架、信封+错误码、DB engine + 建表、会话表鉴权、限流、路径安全、测试基架、OpenAPI 自动生成 | ✅ 33 用例全过 + 8001 实跑冒烟通过（2026-10-05） |
| **M1 账号与班级** | 3.2 账号 + 3.3 班级（含软删进回收站） | 对应用例全过，权限矩阵逐格验证 |
| **M2 课程与文件** | 3.4 课程 + 3.5 文件树 + 3.6 资料 | 文件树全链路（上传/建夹/改名/排序/下载/打包）用例全过 |
| **M3 内容与沟通** | 3.7 站内信 + 3.8 笔记 + 3.9 课堂记录 + 3.10 设置 | 对应用例全过 |
| **M4 收口** | 3.11 回收站合并、backup/build 平移、V1 横切能力清单逐项核对 | 横切清单 100% 存活 |
| **M5 切流** | 前端按新规范改造（统一 fetch 封装 + 信封拦截）、真实流程手测、停旧起新 | 前端全流程手测通过，V1 下线 |

**阶段纪律**：每阶段先写测试再写实现；未达出口标准不开下一阶段；schema 在 M4 前允许推翻（D7）。- **每完成一个阶段，必须同步更新 `backend_v2/README.md`（阶段进度、文件地图、该阶段做了什么、测试数量）**；`hooks/pre-commit` 在提交 backend_v2 变更时**自动**跑 `tools/sync_readme.py`（改测试数、给新文件补地图占位行）+ 文档/命名闸门（`tests/test_docs_sync.py`、`tests/test_api_naming.py`），闸门不过则提交被拒；职责描述与阶段叙述仍需人工/AI 复查。

---

## 7. 前端对接约定（本月同步改造）

```ts
// 统一 fetch 封装：一处处理信封，页面零重复错误处理
async function api(path, options) {
  const res = await fetch(path, { headers: { Authorization: `Bearer ${token}` }, ...options })
  const body = await res.json()          // { code, msg, data }
  if (body.code !== 0) throw new ApiError(body.code, body.msg)
  return body.data
}
```

- token 存 localStorage，请求自动带 Bearer；收到 `2002/2003` 跳登录页
- `msg` 直接 toast 展示，不另建错误文案表
- 公开页面（门户/课程广场/资料）不带 token 也能正常工作

## 8. 工作流与测试策略

### 8.1 开发工作流（测试先行）

每个功能域都走同一个循环，**规范 §3 的每一行 = 一个 pytest 用例 = 未来一个前端按钮**：

```
规范定准（图纸） → pytest 用例（验收单） → 实现让它变绿 → 出口标准达成 → 下一域
```

1. **先写测试（红）**：按 §3 该域的接口行展开——正常入参、无权限 403、越权 403、
   边界值、重名 409、错误码是否与 §1.3 一致
2. **写实现（绿）**：只求让这组测试通过，不夹带下一域的东西
3. **收尾**：该域用例全过 = 阶段出口标准达成，才开下一域

**现阶段只做后端**：不写前端、不改 V1、不占 8000 端口。前端在 M5 拿图纸接线。

### 8.2 测试分层

| 层 | 测什么 | 何时跑 | 覆盖阶段 |
|---|---|---|---|
| **L1 接口自动化**（pytest） | 每个接口的正常/越权/边界/错误码 | 每次改完代码立刻跑，**主力** | M0~M4 持续增长 |
| **L2 真实服务冒烟** | uvicorn 8001 实跑 + 真实 HTTP 关键流程 | 每阶段收尾 | M0~M4 |
| **L3 前端对接** | 前端指向 8001 手点页面全流程 | 切流前 | M5 |
| **L4 双跑对照** | 同请求打 V1(8000) 与 V2(8001)，比对等价性 | 切流前最后验收 | M4~M5 |

### 8.3 断言纪律：双断言

L1 用例必须**同时验证返回与落地**，杜绝"接口说成功但没存"：

```python
def test_create_class(client, login, db):
    resp = client.post("/api/classes", json={"name": "三年级一班"},
                       headers=auth_headers(login))
    assert resp.status_code == 200 and resp.json()["code"] == 0
    # 落地断言：直接查库 / 查磁盘
    assert db.query(ClassRoom).filter_by(name="三年级一班").count() == 1
```

- 结构化数据 → 断言 SQLite 行
- 文件类操作（M2 上传/建夹/改名）→ 断言磁盘文件存在、内容一致
- 删除/回收站 → 断言源位置已移除 + 回收站有快照

### 8.4 运行方式

```powershell
cd qc-pocket-space\backend_v2
& "..\tests\venv\Scripts\python.exe" -m pytest tests -q
```

测试基础设施（`tests/conftest.py`）：
- 进程内 `TestClient`，**不启 uvicorn、不占端口**
- 独立临时数据区（`QC_V2_DATA_ROOT` / `QC_V2_DB`），跑完自动删除
- **绝不接触 V1** 的 `qingcheng.db`、`data/` 与 8000 端口
- 限流计数等跨用例状态逐例清空，用例乱序也能过

### 8.5 pytest 覆盖不到的部分

| 盲区 | 补测时机 |
|---|---|
| 真实浏览器交互（按钮、渲染） | M5：改造 V1 现有 Playwright e2e |
| vitepress 电子书渲染队列 | M4 收口跑一次真实发布 |
| Caddy 反代 / 端口 / 静态站 | 切流当天的部署验证 |

---

*生成：2026-10-04 · M0 已完成（33 用例全过，2026-10-05）· 下一步：M1 账号与班级，按 §8.1 循环推进*
