# 后端 V2 · M5 切流施工清单（前端平移到 V2）

> **状态**：📋 待确认 —— 用户拍板 §7 的 7 条后开工
> **依据**：[backend-v2-api-spec.md](./backend-v2-api-spec.md) §3 接口清单 / §6 阶段表 / §7 前端对接约定 / §8.2 L3·L4 · D12/D14/D15/D17 附则
> **前置**：M0~M4 全部 ✅（`backend_v2` 231 用例绿，2026-10-10 实跑确认；README 原文「后端业务全部封板，剩余仅 M5 切流」）
> **代码位置**：前端 `labs_dev/qx-dashboard-ts/`（React 19 + Vite + Tailwind v4）· 魔改 Scratch `labs_dev/scratch-gui.4.0.43/` · 启动器 `labs_dev/qx-launcher/` · 后端 `qc-pocket-space/backend_v2/`（本阶段预期**零改**）

---

## 0. 为什么是 M5

| 证据 | 内容 |
|---|---|
| 阶段表 | M0 地基 → M4 收口 全部 ✅；**M5 切流** ⏸ 未开始 |
| M4 结论 | 「后端业务全部封板。剩余 **仅 M5 切流**（前端改造 + e2e + 停 V1 起 V2）」 |
| 实跑 | `backend_v2` pytest → **231 passed**（2026-10-10） |
| 前端现状 | 仍说 **V1 协议**：`vite.config.ts` 里 `/api` → `localhost:8000`；响应读 `{status:'success', detail}`；身份用 `?username=` 查参；笔记/消息用**名字**当身份（`/api/notes/{笔记本}/{文件名}`） |

**M5 出口标准**（规范 §6）：前端全流程手测通过，V1 下线。

---

## 1. M5 五件事

| # | 事项 | 产出 |
|---|---|---|
| 1 | **API 层收口** | `src/lib/api.ts` 重写为唯一出口：信封拦截 + `ApiError` + token 自动带 + `2002/2003` 跳登录 |
| 2 | **逐域平移** | 17 个前端文件里 110 处 `/api/` 调用 → V2 路径与形状（§3 对照表） |
| 3 | **删除纪律** | 新增 `confirmDelete()` 统一确认框；软删文案含「回收站」、硬删含「不可恢复」 |
| 4 | **外设同步** | 魔改 Scratch 存取、启动器、Caddy、e2e、CI 一并指 V2 |
| 5 | **切流** | L3 手测全流程 → L4 双跑对照 → 停 V1 起 V2 → 文档收尾 |

---

## 2. 页面对应关系（前端 ↔ 后端域）

| 前端入口 | 组件 | 后端域 | 备注 |
|---|---|---|---|
| `/` 卡片主页 | [HomePage.tsx](../../labs_dev/qx-dashboard-ts/src/components/home/HomePage.tsx) + `SideBar` `Overview` `CourseCatalog` `CourseDetail` `Resources` `Downloads` `ResourceFileTree` `LoginPanel` `CodePlayground` | `auth` · `assets`（公开读） · `settings` | 游客可看；登录后带 token |
| `/` 星空主页 | [star-home/](../../labs_dev/qx-dashboard-ts/src/components/star-home/) | **无后端调用** | 纯展示，不动 |
| `/dashboard` → student | [StudentDashboard.tsx](../../labs_dev/qx-dashboard-ts/src/components/student/StudentDashboard.tsx)（Dock：`CoursePlaza` / `Notebook` / `Messages` / `Settings`） | `assets`（读） · `notebooks`+`notes` · `messages` · `settings` · `class-records`（读自己班） | 笔记/消息 = **ID 制重写** |
| `/dashboard` → teacher | [TeacherDashboardWithSidebar.tsx](../../labs_dev/qx-dashboard-ts/src/components/teacher/TeacherDashboardWithSidebar.tsx)（`CourseManager` / `ResourcesManager` / `AttendancePanel` / `StudentListPanel` / `SendMessagePanel`） | `assets` · `classes` · `students` · `class-records` · `messages`（只发） · `trash` | 文件树改**逐层加载**（P2） |
| `/dashboard` → admin | [AdminDashboard.tsx](../../labs_dev/qx-dashboard-ts/src/components/admin/AdminDashboard.tsx) | `accounts` · `classes` · `trash` | **不发消息**（§3.7 拍板：管理员有微信） |
| `/Scratch` 魔改客户端 | `labs_dev/scratch-gui.4.0.43/` | `assets/files`（读写） · `accounts` · `assets` | D14 五条清单 |
| 启动器「批量注册」 | `labs_dev/qx-launcher/app/register.py` | `auth` · `accounts/batch` · `classes` | 当前打的是 V1 的 `/api/admin/*` |

---

## 3. 接口对照表（V1 → V2）

> 「⚠」= 形状或语义**变了**，前端不能只改路径。

### 3.1 认证与会话

| V1（现在） | V2（改成） | 形状变化 |
|---|---|---|
| `POST /api/login` → `{status, user, token}` | `POST /api/auth/login` | ⚠ `data = {token, expires_in, account:{id,username,role,avatar_path,descriptors,status,created_at}}`；失败 401/`2001`，限流 429/`2029` |
| `POST /api/logout` | `POST /api/auth/logout` | 需带 Bearer |
| `GET /api/user/profile?username=` | `GET /api/auth/me` | ⚠ 不再传 `username`（token 即身份） |
| `POST /api/user/change-password` | `PATCH /api/auth/password` | ⚠ body `{old_password, new_password}`；改后**其他会话全失效**，当前会话保留 |

### 3.2 账号

| V1 | V2 | 形状变化 |
|---|---|---|
| `GET /api/users?t=` → `{users:[…]}` | `GET /api/accounts?role=teacher\|student` | ⚠ `data.items`；公开访问自动裁剪为 `{id,username,role,avatar_path}`，本人/admin 得全集 |
| `POST /api/register` | `POST /api/accounts` | ⚠ 权限矩阵：建老师仅 admin、建学生 admin/teacher；建 admin 一律 403 |
| `POST /api/admin/batch-register`（启动器在用） | `POST /api/accounts/batch` | ⚠ body `{role, password?, usernames[]}`，返回 created/skipped 明细；缺省密码 `123456` |
| `DELETE /api/users/{username}` | `DELETE /api/accounts/{id}` | ⚠ **停用（软删进回收站）**，msg 含「可在回收站恢复」；admin 不能删自己 |
| `POST /api/update-avatar` | `POST /api/accounts/{id}/avatar`（≤5MB，**仅学生本人**）<br>`PUT /api/accounts/{id}/avatar/preset` `{preset:"avatar_07"}` | ⚠ 老师/admin **不能传自定义图片**，只能用 emoji 预设标记；`GET …/avatar` 取自定义图（无图/预设 → 404，前端画默认 emoji） |

### 3.3 班级与学生

| V1 | V2 | 形状变化 |
|---|---|---|
| `GET /api/classes?username=` | `GET /api/classes` | ⚠ `data.items[{id,name,owner_id,owner_name,schedule,created_at,student_count}]`（admin 全部 / teacher 自己 / student 所在） |
| `POST /api/classes` | `POST /api/classes` | body 去 `teacher`（owner 取 token）；重名 409/`4001`（**按老师隔离**） |
| `PUT /api/classes/{name}` | `PATCH /api/classes/{id}` | ⚠ 身份从**班级名**改 **class_id** |
| `DELETE /api/classes/{name}` | `DELETE /api/classes/{id}` | ⚠ 软删进回收站 |
| `GET /api/classes/{name}`（详情） | `GET /api/classes/{id}` | ⚠ `data.students[{id,username,role,avatar_path,joined_at}]` |
| `GET /api/classes/find-student?name=` | `GET /api/students?q=` | ⚠ 返回 `items[{…account_public, classes:[{id,name}]}]`（带所跨班级） |
| `POST /api/classes/{name}/students` | `POST /api/classes/{id}/students` | body `{account_id}` 挂接 或 `{username,password}` 现场建号；重复入班 409/`4009` |
| `DELETE /api/classes/{name}/students/{id}` | `DELETE /api/classes/{id}/students/{accountId}` | 只断关系、**不删号** |
| ~~学生个人上课日 `schedule_override`~~ | ❌ 已砍（2026-10-07） | 前端若还在读该字段 → 删 |

### 3.4 资产与文件树（**合并最狠的一族**）

| V1 | V2 | 形状变化 |
|---|---|---|
| `GET /api/courses?teacher=` | `GET /api/assets?bucket=courses`（或 `?owner={accountId}`） | ⚠ **`bucket` 四值**：`courses\|books\|reference\|download`；`data.items[{id,owner_id,owner_name,bucket,name,description,tags,type,format,created_at}]`；**不再有 `teacher`/`shelf`/`featured`/`material_count`** |
| `GET /api/resources?category=` | `GET /api/assets?bucket=reference\|download` | ⚠ 原 `category` 词汇废除，全域只叫 `bucket` |
| `POST /api/courses` | `POST /api/assets` `{bucket,name,type,format,description,tags}` | 课程族 `type` 必填（scratch/python/cpp），`format` 缺省按 type 兜底；重名 owner 全局 → `5001` 课程族 / `6001` 资料族 |
| `POST /api/resources` | 同上，`bucket:'reference'\|'download'` | — |
| `GET /api/courses/{id}` · `/api/resources/{id}` | `GET /api/assets/{id}` | owner/admin 额外得 `publish` |
| `PUT /api/courses/{id}` · `/api/resources/{id}` | `PATCH /api/assets/{id}` | ⚠ **`PUT /shelf` 也并进来**：挪书架/挪分类 = `PATCH {bucket}`（族内族间都可，幂等） |
| `DELETE /api/courses/{id}` · `/api/resources/{id}` | `DELETE /api/assets/{id}` | ⚠ 软删**四联动**（行→trash · 磁盘搬回收站 · 发布产物清理 · file_order 保留）；msg 含「回收站」 |
| `GET /api/courses/{id}/materials` | `GET /api/assets/{id}/lessons` | ⚠ `items[{name,has_main,has_student,sb3_count}]`；**仅课程族**（其余 404） |
| `GET /api/courses/{id}/tree?path=` | `GET /api/assets/{id}/files?path=` | ⚠ **整棵递归树 → 单层平铺**：`{path, entries:[{name,type:'dir'\|'file',size?,mtime?}], total}`（逐层加载，P2） |
| `POST /api/courses/{id}/tree/upload?path=` | `POST /api/assets/{id}/files/upload` | multipart `file` + `path`（同名参数，**中间层目录后端自动建**，D14③） |
| `POST /api/courses/{id}/tree/folder?path=` | `POST /api/assets/{id}/files/mkdir` `{path}` | ⚠ 从查参改 JSON body |
| `POST /api/courses/{id}/tree/rename?path=&new_name=` | `PATCH /api/assets/{id}/files/rename` `{path,new_name}` | ⚠ 方法 + body |
| `DELETE /api/courses/{id}/tree?path=` | `DELETE /api/assets/{id}/files?path=` | 物理删、**不可恢复** |
| `POST /api/courses/{id}/tree/reorder`（swap 两个） | `POST /api/assets/{id}/files/order` `{path,names[],position}` | ⚠ 语义从「两两交换」变「**整段顺序一次重写**」（幂等） |
| `POST /api/courses/{id}/publish` | `POST /api/assets/{id}/publish` | ⚠ 返回 `data = publish 对象`；已排队时幂等 |
| `GET /api/courses/{id}/publish/status` | `GET /api/assets/{id}/publish-status` | ⚠ **状态词变了**：`{state:'none'\|'queued'\|'building'\|'done'\|'failed', message?, queued_at?, started_at?, finished_at?}`；前端原来读 `st.publish.status === 'success'` → 现在读 `st.state === 'done'` |
| `GET /api/resources/{id}/download?path=` | `GET /api/assets/{id}/download?path=` | ⚠ **仅资料族**（课程/书架 404）；单文件直发 / 目录实时 zip 流 |
| ❌ `POST …/lessons/{lesson}/save-sb3` | 砍掉 → 走 `files/upload` | 魔改 Scratch 要改（§5.2） |

### 3.5 站内信

| V1 | V2 | 形状变化 |
|---|---|---|
| `GET /api/messages/folders?username=` | `GET /api/message-folders` | ⚠ `{items:[{id,name,builtin,count,unread}], total_unread}`；**内置「收件箱」懒创建**、`builtin:true` 不可改名改删 |
| `GET /api/messages/inbox?username=&folder=` | `GET /api/messages?box=inbox&folder={folderId}&page=&page_size=` | ⚠ `items[{id,title,is_read,created_at,folder_id,peer}]`（原来是 `subject`），**分页**（默认 20/页，上限 100）；`?box=sent` 查已发送（同表按 sender） |
| `GET /api/messages/{id}?username=&folder=` | `GET /api/messages/{id}` | ⚠ **读即已读**（仅收件人触发）；返回含 `sender_username/receiver_username/content/folder_id` |
| `POST /api/messages/{id}/read` | 同上（读即已读）或 `PATCH {is_read:true}` | ⚠ 少了独立端点 |
| `POST /api/messages/{id}/move?folder=` | `PATCH /api/messages/{id}` `{folder_id}` | ⚠ **仅收件人** |
| `DELETE /api/messages/{id}` | `DELETE /api/messages/{id}` | ⚠ 真删、**不可恢复**、仅收件人；单份语义 → 发件人的已发送里同步消失 |
| `POST /api/messages/folders` | `POST /api/message-folders` `{name}` | 同名 409 |
| `PUT /api/messages/folders/{name}?new_name=` | `PATCH /api/message-folders/{id}` `{name}` | ⚠ 名字 → **folder_id**；内置夹 403/`7010` |
| `DELETE /api/messages/folders/{name}` | `DELETE /api/message-folders/{id}` | 信**退回收件箱**（不丢信） |
| `POST /api/messages/send` | `POST /api/messages` `{receiver_id,title,content}` | ⚠ **仅 teacher**（admin 也 403/`7003`）；收件人必须是**在世学生** |
| `GET /api/messages/unread-count?username=` | `GET /api/messages/unread-count` | `{count}` |

### 3.6 笔记

| V1 | V2 | 形状变化 |
|---|---|---|
| `GET /api/notebooks?username=` | `GET /api/notebooks` | ⚠ `items[{id,name,created_at,note_count}]` |
| `POST /api/notebooks` | `POST /api/notebooks` `{name}` | 重名 409/`8009` |
| `PUT /api/notebooks/{name}?new_name=` | `PATCH /api/notebooks/{id}` `{name}` | ⚠ 名字 → **id** |
| `DELETE /api/notebooks/{name}` | `DELETE /api/notebooks/{id}` | ⚠ **级联删光全部笔记**，msg 含「不可恢复，含全部笔记」 |
| `GET /api/notes?username=&notebook=` | `GET /api/notes?notebook={notebookId}&page=&page_size=` | ⚠ `items[{id,notebook_id,title,updated_at,created_at}]`（原来是 `filename`）；分页 |
| `GET /api/notes/{notebook}/{filename}` | `GET /api/notes/{noteId}` | ⚠ 返回 `content`（md 正文**进库**，不再读文件） |
| `POST /api/notes?username=&notebook=` `{filename,content}` | `POST /api/notes` `{notebook_id,title,content}` | ⚠ `filename` → `title`（**不再强制 `.md` 后缀**） |
| 保存 = 再 POST 一次 | `PATCH /api/notes/{noteId}` `{title?,content?}` | ⚠ 明确 PATCH 语义，`updated_at` 更新 |
| `PUT /api/notes/{nb}/{file}?new_name=` | `PATCH /api/notes/{noteId}` `{title}` | ⚠ 改名与保存合并 |
| `DELETE /api/notes/{nb}/{file}` | `DELETE /api/notes/{noteId}` | 真删、不可恢复 |

### 3.7 课堂记录

| V1 | V2 | 形状变化 |
|---|---|---|
| `GET /api/sessions/{className}?month=` | `GET /api/class-records?class_id={id}&month=YYYY-MM` | ⚠ 班级名 → **class_id**；`items[{class_id,date,content,updated_at}]` 倒序 |
| `GET /api/sessions/{className}/{date}` | `GET /api/class-records/{classId}/{date}` | — |
| `POST /api/sessions/{className}` | `PUT /api/class-records/{classId}/{date}` `{content}` | ⚠ **同日覆盖、幂等** |
| `DELETE /api/sessions/{className}/{date}` | `DELETE /api/class-records/{classId}/{date}` | 不可恢复 |
| `GET /api/sessions/student/{name}` | `GET /api/class-records?student={accountId}` | ⚠ 名字 → **account_id**；仅老师/admin |

### 3.8 设置

| V1 | V2 | 形状变化 |
|---|---|---|
| `GET /api/user/settings` | `GET /api/settings` | ⚠ **字段名全改**：`{theme, hue, font_size, sidebar, recent_opens}`（原 `theme, customHue, fontSize, sidebarExpanded, recentOpens`） |
| `PUT /api/user/settings` | `PUT /api/settings` | ⚠ `font_size` 限 12–28；缺省字段补默认 |
| `POST /api/user/recent-opens` `{kind,id,label,teacher,type,ts}` | `POST /api/settings/recent-opens` `{item}` | ⚠ V2 只存**一个不透明字符串**（≤200，去重置顶、上限 6）。结构化对象由前端自己 `JSON.stringify` 进 `item`，读时 `JSON.parse`（后端永不解析） |

### 3.9 回收站

| V1 | V2 | 形状变化 |
|---|---|---|
| `GET /api/trash?username=admin` | `GET /api/trash?type=account\|class\|asset` | ⚠ `items[{id,type,name,owner_id,disk_path,deleted_at}]`；admin 全部 / teacher 只见自己名下 |
| `GET /api/trash/items?username=` | `GET /api/trash` | ⚠ **两套合一**（原「用户=DB 版 + 内容=文件版」）→ 一张表 |
| `POST /api/trash/restore/{id}` · `/api/trash/items/{folder}/restore` | `POST /api/trash/{id}/restore` | ⚠ 按 type 三分支（账号翻状态 / 班级插行 / 资产插行+磁盘搬回） |
| `DELETE /api/trash/{id}` · `/api/trash/items/{folder}` | `DELETE /api/trash/{id}` | ⚠ **仅 admin + 仅内容类**；账号条目 403（只可恢复）；msg 含「不可恢复」 |
| `DELETE /api/trash` | `DELETE /api/trash` | 清空；账号条目**保留**；返回 `{purged, kept}` |

---

## 4. 语义/形状变化清单（前端最容易踩的 10 处）

1. **信封**：`{status:'success'|'error', detail}` → `{code, msg, data}`；成功 `code===0`。全前端 110 处读取点统一走 `api()`，页面里 `data.status === 'success'` 全部删掉。
2. **身份从名字改 ID**：班级、资产、笔记本、笔记、消息夹 —— 全部 `id` 制。凡是用「名字当 key/路径参数」的地方都要重写（笔记与消息最重）。
3. **文件树从「整棵」改「逐层」**：`/files?path=` 只返一层；前端要按点击展开 + 缓存（资源管理器两条、课件树、公开资料浏览）。
4. **`bucket` 取代 `shelf`/`category`**：全域一个词；`Course.shelf`、`featured`、`shelfOf()` 全删。
5. **`material_count` 消失**：V2 资产行不含计数。卡片上要显示计数就得逐资产查 `/lessons`（N+1）或首屏不显示 → **见 §7 P4**。
6. **发布状态词与承载位置**：`st.publish.status==='success'` → `st.state==='done'`；轮询端点也换名。
7. **设置字段改名**：`customHue→hue`、`fontSize→font_size`、`sidebarExpanded→sidebar`、`recentOpens→recent_opens`；`recent_opens` 元素变成不透明字符串。
8. **头像**：V1 谁都能传图片；V2 **只有学生能传**，老师/admin 只能选 emoji 预设标记（清单在前端常量，后端只存 `avatar_XX`）。
9. **发信仅 teacher**：admin 端的发信入口要摘掉；收件人必须是**在世学生**。
10. **删除文案必须说真话**：见 §5.1 新增的 `confirmDelete()`；后端软删 msg 必含「回收站」、硬删必含「不可恢复」—— toast 直接转述后端 msg 即不撒谎。

---

## 5. 逐文件改动清单

### 5.1 前端 `labs_dev/qx-dashboard-ts/src/`

| # | 文件 | 改什么 | 量 |
|---|---|---|---|
| 1 | `lib/api.ts` | **整体重写**：`api()` 统一封装（信封、`ApiError`、Bearer、2002/2003 跳登录、`msg` toast）+ 各域薄函数（`fetchAccounts/fetchAssets/fetchFiles/fetchMessages/...`） | 大 |
| 2 | `lib/useAuth.ts` | 删掉全局 fetch 劫持里的 401/403 分支（收进 `api.ts`）；token 校验改 `GET /api/auth/me`；`logout` 走 `/api/auth/logout`；`PortalUser` 补 `id` | 中 |
| 3 | `lib/useTeacherData.ts` | `/api/classes?username=` → `GET /api/classes` | 小 |
| 4 | `types/course.ts` | `Course`：删 `teacher/teacher_key/shelf/featured/material_count`，加 `bucket/owner_id/owner_name`；`Material` → `Lesson`；新增 `FileEntry`（单层条目）；`TreeNode` 改为逐层缓存结构 | 中 |
| 5 | **新增** `lib/confirmDelete.tsx` | 唯一删除确认封装 `confirmDelete({recoverable, what})`；文案表照抄规范 §7「删除语义总表」 | 新 |
| 6 | **新增** `lib/toast.tsx` | 轻量提示（替代散落的 `alert()`，`msg` 直接展示） | 新 |
| 7 | `components/home/HomePage.tsx` | 改调新 `api.ts`；头像、最近打开、设置字段 | 中 |
| 8 | `components/home/CourseCatalog.tsx` | 一次 `GET /api/assets?bucket=courses` 本地筛老师，删掉「按老师逐个并发聚合」的 N+1 | 中 |
| 9 | `components/home/CourseDetail.tsx` | `fetchMaterials` → `/lessons`；课件树逐层 | 中 |
| 10 | `components/home/Resources.tsx` | `fetchResources` → `bucket=reference` | 小 |
| 11 | `components/home/Downloads.tsx` | → `bucket=download` | 小 |
| 12 | `components/home/ResourceFileTree.tsx` | 下载链 → `/api/assets/{id}/download`；树逐层 | 中 |
| 13 | `components/home/Overview.tsx` | 设置字段改名 + `recent_opens` 序列化 | 小 |
| 14 | `components/home/SideBar.tsx` | 同上 | 小 |
| 15 | `components/home/CodePlayground.tsx` | 同上 | 小 |
| 16 | `components/home/LoginPanel.tsx` | 新登录响应 `{token, expires_in, account}` | 小 |
| 17 | `components/student/StudentDashboard.tsx` | `unread-count`、`/api/auth/me` | 小 |
| 18 | `components/student/CoursePlaza.tsx` | accounts + assets + lessons | 中 |
| 19 | `components/student/Notebook.tsx` | **ID 制重写**：笔记本 id / 笔记 id、PATCH 保存、`title` 去 `.md`、级联删除文案 | **大** |
| 20 | `components/student/Messages.tsx` | **ID 制重写**：夹 id、`title`、读即已读、PATCH 移夹、未读角标、5 秒轮询改分页 20/页 | **大** |
| 21 | `components/student/Settings.tsx` | 改密 → `PATCH /api/auth/password` | 小 |
| 22 | `components/teacher/CourseManager.tsx` | `bucket=courses` + 逐层文件树 + `publish-state` + `confirmDelete`；课件删除二次确认 | **大** |
| 23 | `components/teacher/ResourcesManager.tsx` | 同上 + 打包下载；**是否与 22 合并成一个「资产管理」页 → §7 P1** | **大** |
| 24 | `components/teacher/AttendancePanel.tsx` | classes（id 制）/ class-records / students?q / trash 全面改；学生上课日字段删除 | **大** |
| 25 | `components/teacher/SendMessagePanel.tsx` | `POST /api/messages` + `receiver_id`（先从 `/api/students?q=` 取） | 中 |
| 26 | `components/teacher/StudentListPanel.tsx` | `/api/class-records?student={id}` | 中 |
| 27 | `components/admin/AdminDashboard.tsx` | accounts 列表/建号/批量/停用 + trash（id 制、仅内容类可彻底删）；**摘掉发信入口** | **大** |
| 28 | `components/ui/AvatarPicker.tsx` | 按角色分支：学生→上传 + 预设；老师/admin→仅预设（`PUT …/avatar/preset`） | 中 |
| 29 | `components/ui/FolderTreePanel.tsx` | 基件 key 从**名字**改 **id**（笔记本/消息共用，改这里省一半工） | 中 |
| 30 | `vite.config.ts` | `/api` 代理 → `http://localhost:8001` | 小 |

### 5.2 魔改 Scratch `labs_dev/scratch-gui.4.0.43/src/`

| # | 文件 | 改什么 | 量 |
|---|---|---|---|
| 31 | `containers/save-to-course.jsx` | 保存 = **两次** `POST /api/assets/{id}/files/upload`（`path=课件/main.sb3`、`path=课件/student.sb3`），砍掉 `save-sb3?file_type=` | 中 |
| 32 | `containers/gui.jsx` | 加载：静态路径 `?folder=/courses/{teacher}/{course}/{lesson}/` → `GET /api/assets/{id}/files/download?path=…`（学生 `student.sb3` 回退 `main.sb3`，老师 `main.sb3`）；`?folder=` 参数改成「assetId + path」 | 中 |
| 33 | `components/menu-bar/menu-bar.jsx` | `/api/users` → `/api/accounts?role=teacher`；`/api/courses?teacher=` → `/api/assets?bucket=courses&owner=` | 中 |
| 34 | 构建产物 | `npm run build` → `qc-pocket-space/frontend/scratch_dist/`（与现状一致，只需重跑） | — |

### 5.3 启动器 `labs_dev/qx-launcher/app/`

| # | 文件 | 改什么 |
|---|---|---|
| 35 | `config.py` | `backend_dir` → `<website_root>/backend_v2` |
| 36 | `gui.py` | `FASTAPI_PORT` `8000` → `8001`（切流后可与 Caddy 一起改回 8000，见 §7 P6） |
| 37 | `register.py` | `/api/login`→`/api/auth/login`；`/api/admin/batch-register`→`POST /api/accounts/batch`；`/api/users`→`/api/accounts`；`/api/admin/classes/{t}`→`/api/classes`（按 owner 筛） |

### 5.4 后端 `qc-pocket-space/backend_v2/`

**预期零改**。若 §7 P4（`material_count`）拍板要补字段，则属「响应加字段」，不动 schema、不动既有用例，另加 1 条验收单。

### 5.5 部署 / CI / e2e / 文档

| # | 文件 | 改什么 |
|---|---|---|
| 38 | `qc-pocket-space/tests/e2e_helpers.py` · `test_e2e.py` | API 基址与全部路径 → V2；**新增「点删除必弹确认框」断言**（规范 §7 闸门） |
| 39 | `qc-pocket-space/runtime/caddy/generate_caddyfile.py` · `Caddyfile` | `/api/*` → `localhost:8001`；`/courses/*`、`/resources/*` 静态挂载点按 V2 决定去留（D14 后加载走 `files/download`，理论上**可撤**） |
| 40 | `qc-pocket-space/deploy/ci/ci_run.py` | 目前只跑 V1 `tests/`；追加 `backend_v2` 全量（仍不跑 e2e） |
| 41 | 文档 | 规范 §6 M5 打 ✅；`backend_v2/README.md` 阶段表；本文件随进展更新 |

---

## 6. 施工顺序（每步独立可验收）

> **✅ 拍板（2026-10-10）：方案 B —— e2e 提前为"每域一测"**。节奏改为：每替换一小块 API →
> 写集成测试（Playwright，真前端+真后端）→ AI 跑绿 → AI 浏览器验收单 → **用户人工终验** → 提交进下一块。
> 原 §6 的"e2e 集中在 S6"作废；S6 只保留切流本身（双跑对照/停 V1/文档）。
> 首批用例 = 登录链 4 条（M5a 已验收，待固化）；Playwright 基建在 M5b 第一块（设置域）搭。
>
> **✅ 追加拍板（2026-10-10 午）：V1 测试体系即日退役，新门同步重建。**
> 理由：V1 零行动价值（测出问题也不修、线上期间不更新），继续跑=白跑。
> 动作：① 删 `tests/`（199 快测+215 老"e2e"+venv）、`hooks/pre-push`、`deploy/ci/` 两脚本——部署看门狗任务 `CodeNova-CI` 已于当日删除；
> ② 新建 `backend_v2/.venv` 作为唯一 pytest 环境（原 `tests/venv` 随删）；
> ③ 新 `pre-push` 门 = 跑 backend_v2 231 条，`core.hooksPath` 重新启用；e2e 按方案 B 域内跑、不进门；
> ④ `hooks/pre-commit`（V2 文档闸门）原样保留。原"S6 前重建门"计划提前至此。

| 步 | 内容 | 验收 |
|---|---|---|
| **S1 地基** | `api.ts` 重写 + `toast.tsx` + `confirmDelete.tsx` + `useAuth` 走 `/api/auth/*` + `vite.config` 指 8001 | 登录/登出/改密/头像在 8001 上真跑通；信封错误（密码错 `2001`、限流 `2029`）能正确 toast |
| **S2 公开面** | 主页一圈：`HomePage/CourseCatalog/CourseDetail/Resources/Downloads/ResourceFileTree/Overview/SideBar` | 游客不带 token 全流程可看；登录后「最近打开」去重置顶正确 |
| **S3 学生端** | `Notebook`（id 制）+ `Messages`（id 制）+ `Settings` + `CoursePlaza` | L3 手点：建本→建笔记→改名→删（级联文案）→收发信→移夹→已读→未读角标 |
| **S4 教师端** | `CourseManager`/`ResourcesManager`（或合并页）+ `AttendancePanel` + `StudentListPanel` + `SendMessagePanel` | 建资产→逐层建夹传文件→排序→改名→发布→看状态；建班→加生→课堂记录；删资产进回收站→恢复 |
| **S5 管理端 + 外设** | `AdminDashboard` + 魔改 Scratch（保存/加载）+ 启动器 + Caddy | 批量注册→停用→回收站恢复；Scratch 存两次 upload 后能原样加载；`/Scratch` 走 V2 全通 |
| **S6 切流** | **L4 双跑对照**（同请求打 8000/8001 比等价）→ e2e 全绿 → 停 V1 起 V2 → 文档收尾 | 出口标准：前端全流程手测通过，V1 下线 |

---

## 7. 待拍板（P1~P7，开工前逐条确认）

| # | 问题 | 备选 | 影响面 |
|---|---|---|---|
| **P1** | 教师端 `CourseManager` + `ResourcesManager`（各 ~50KB）是否按 D15/D17 合**一个「资产管理」页**（bucket 四选一）？ | ① 合并（规范倾向）② 保持两页、同族接口不同 `bucket` | 决定 S4 是重写一页还是改两页（相差约 1 倍工作量） |
| **P2** | 文件树 **逐层加载** 是否接受？V2 `/files` 只返一层（V1 返整棵递归树） | ① 接受（规范 §7 已拍「大数据域按需取」）② 前端递归自拼（树深时请求爆炸，不建议） | 资源管理器/课件树/公开资料的加载与缓存模型 |
| **P3** | 笔记 `title` 是否**保留 `.md` 后缀**？V2 只存 `title`，不认文件名 | ① 不保留（推荐，库内不是文件）② 前端自行补/削后缀 | Notebook 的显示与重命名交互 |
| **P4** | `material_count`（卡片上的「N 个课件」）怎么办？V2 资产行**不含计数** | ① 就不显示（最省）② 逐资产懒查 `/lessons`（N+1）③ 后端给 `/api/assets` 加只读计数字段（需 1 条新验收单） | 主页/课程广场卡片文案；③ 会动后端（唯一可能破「后端零改」的点） |
| **P5** | V1 静态路径 `/courses/*`、`/resources/*`（Caddy 直读 `data/`）切流后是否撤掉？ | ① 撤（加载全走 `files/download`，D14 方向）② 保留指向 `data_v2/` | Caddy 配置 + 魔改 Scratch 的 `?folder=` 参数形态 |
| **P6** | 切流后 V2 端口：继续 8001 让 Caddy 转发，还是**把 V2 直接跑在 8000**（V1 停）？ | ① V2 跑 8001 + Caddy 改反代（改动最小）② V2 占 8000（前端/启动器无感，但 `backend_v2` 默认端口要动） | 部署脚本、启动器、e2e 基址 |
| **P7** | 双跑对照 **L4** 要不要真做（同请求打 8000/8001 比字段）？ | ① 做（规范 §8.2 列为「切流前最后验收」）② 跳过，直接 L3 手测 | S6 工期与置信度 |

---

## 8. 范围外（M5 不做）

- 不改 V1 代码（V1 只被「停」，不被改）
- 不做数据工程化迁移（D3：必要时一次性小导入）
- 不做新功能域（D11：作品分享/统计/AI 等一律不预留）
- 不做单层平铺之外的层级嵌套（D18：邮件夹/笔记本要嵌套 = 将来加 `parent_id`）
- Caddy 的 HTTPS/域名与 vitepress 渲染逻辑（M4 已验证，切流当天只改反代目标）

---

*生成：2026-10-10 · 依据 `backend_v2` 231 用例绿（M0~M4 封板）与规范 §3/§6/§7 逐条对照*
