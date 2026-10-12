# 前端迁移方案 · M5b 按域换血

> 后端 `backend-v2-m5-frontend-plan.md` 的执行图纸。M5b = 前端逐域切至 V2（8001）。
> 生成 2026-10-10 · 依据 m5 plan §3 接口对照表 + 后端 routers 源码（真实返回以源码/测试为准）

## 📌 进行中（开着的活都写这里；干完清空并入 §4 进度记录）

（**register.py 批量注册工具**：仍是 V1 方言〔/api/admin/batch-register 等〕，切流后失效，待单独迁 V2；另魔改 Scratch 菜单栏课程列表〔§5.2 残余〕、CI 恢复均待后续）


## 0. 决策（接 spec D 表续号）

| # | 决策 | 日期 |
|---|---|---|
| **D21** | 切块方式 = **按后端域切**（每块一个域的全部前端调用点），块编号 M5b-1…N | 2026-10-10 |
| **D22** | 第一块 = **设置 + 改密 + 头像**；账号管理（列表/注册/删除）另排一块 | 2026-10-10 |
| **D23** | 本方案文档独立于 m5 plan（本文） | 2026-10-10 |
| **D24** | **前台 UI 不擅改（改前询问）；后台**（学生/教师/管理员 dashboard 内）**可自由发挥**——后端有功能缺按钮可自行加；后台 UI 后续统一重设计 | 2026-10-10 |
| **P3** | 笔记标题**不保留** `.md` 后缀：V2 零文件（D6），标题纯文本，新建/改名不拼、列表与顶栏不显示 | 2026-10-11 |
| （D20） | 主题四值 `''/green/blue/custom`（设置域配套，见 spec §0） | 2026-10-10 |
| **P1** | M5b-6 教师端 CourseManager + ResourcesManager **合并成一个「资产管理」页**（bucket 四选一，重写一页而非改两页） | 2026-10-12 |
| **D27** | 课堂记录 `content` = **JSON.stringify(学生记录数组)**，前端序列化/解析（先例 recent_opens）；出勤三态/备注/计划全保留，后端零改 | 2026-10-12 |
| **D28** | **终验合并**：每块 e2e 自跑自验 + 扫端点，**全部块完成后用户一次性终验**（中途不请用户手点） | 2026-10-12 |
| **P4** | 资产卡片「N 个课件」计数**不显示**（V2 资产行无计数字段，最省方案，后端零改） | 2026-10-12 |
| **D29** | Scratch 课件加载**零改动**：`folder=/api/assets/{id}/files/download?path={lesson}/`（以 / 结尾，Scratch 自拼 main.sb3）；保存/菜单栏仍归 §5.2 后续 | 2026-10-12 |
| **D30** | 管理面文件排序改**上移/下移按钮**（触屏友好，去 dnd-kit 拖拽）；V2 order 接口 names+position 语义不变 | 2026-10-12 |
| **D31** | **数据不迁移**：V1 `data/`+`qingcheng.db` 归档即备份，V2 `data_v2/` 从零开始（资产均为用户自有且有备份，仅 1 条学生测试笔记） | 2026-10-12 |
| **D32** | 切流第一批：Caddy `/api` 反代 + 启动器起服务均指 **8001**（8000 退役；8001 赋“升级”语义）；Scratch 课件保存走 `files/upload`（§5.2 存储侧完成，菜单栏课程列表同步切 V2） | 2026-10-12 |

## 1. 块划分与状态

| 块 | 范围 | 调用点 | 前置拍板 | 状态 |
|---|---|---|---|---|
| **M5b-1** | 设置 S + 改密 + 头像 | ~5 | D20 D22 | ✅ **完成**（e2e 6 条 + 用户终验 2026-10-10） |
| **M5b-2** | 账号管理 A（列表/注册/停用启用）+ 垃圾桶页切 V2 + D25/D26 | ~9 | D25 D26 | ✅ **完成**（e2e 10 条 + 用户终验 2026-10-11 凌晨） |
| M5b-3 | 站内信 L（含 unread-count 残留 + 群发灰按钮 A 方案 + 教师端浅色） | 12 | — | ✅ **完成**（e2e 18/18 + 用户终验 2026-011 下午） |
| M5b-4 | 笔记 N | 10 | P3 | ✅ **完成**（e2e 4 条 + 用户终验 2026-10-11） |
| M5b-5 | 班级学生 B | 11 | — | ✅ **完成**（e2e E19~E22 + 后端 231 绿；UI 拟重设计免手测拍板 2026-10-11） |
| M5b-6 | 资产族 F/C/E（拆三亚块：公开读面→管理面→文件树） | 41+2※ | P2 P4（P1 已拍：合并一页） | ✅ **完成**（e2e E28~E32 + 全量 36/36；2026-10-12）※挂账兑现：fetchTeacherNames N+1 链改一次全量取 |
| M5b-7 | 课堂记录 R | 4（+1 未接线） | D27 | ✅ **完成**（e2e E25~E27 先红后绿 + 全量 31/31；2026-10-12） |
| M5b-8 | 回收站 | 3（收口） | — | ✅ **完成**（e2e E33 + 全量 37/37；2026-10-12）※大头早已随块 2/6 切完，本块收 AttendancePanel 尾 |

## 2. 每块五步仪式（对齐后端 M1/M2）

1. **图纸**：调用点清单（文件:行 → V1 → V2 → 形状变化，标注类型①移植/②删/③新增/④改后端）+ e2e 用例名 → **用户点头才动代码**
2. **先红**：e2e 写好跑红（证明用例真测得到）
3. **变绿**：改码 → e2e 绿 + 后端 231 绿 + tsc/eslint 零新增
4. **自验**：e2e 全量绿 + tsc/eslint 零新增 + 顺手扫端点；**全部块完成后用户一次性终验**（D28 拍板 2026-10-12，取代逐块手点）
5. **记档**：一笔提交（trailer + 改动文件清单）+ 本文档更新状态 → 下一块

## 3. e2e 规格

- 位置：前端仓库 `e2e/`（与前端代码同仓同提交）；Playwright + 系统 Edge（`channel: 'msedge'`，零下载）
- **有头模式**（`headless: false`，用户可看全程）；跑时勿动键鼠
- webServer 自动起停 5173 + 8001；**不进 hooks 门禁**（方案 B：pre-push 只跑后端 231）
- 只测用户可见行为；接口对错归后端 231；改密闭环用独立学生号，admin 密码不动
- 测试账号（开发库，勿入 git 文档）：e2e 专用号由 `ensureStudent` 幂等管理

## 4. 进度记录

- **2026-10-10 M5b-1 完成**：3 接口迁移（`/api/settings` 族、`PATCH /api/auth/password`、`PUT /api/accounts/{id}/avatar/preset`）+ login/me 补存 id + 401 按错误码分流（2001 凭证错不踢人）+ D24 首例（教师/管理端改密入口，共享表单 `ui/ChangePassword.tsx`）。e2e 10/10 绿、231 绿；用户终验管理端/教师端通过。
- **2026-10-11 M5b-2 完成**：账号管理域（列表/创建/删除切 V2）+ **D25** 停用/启用直操（停用不进回收站）+ **D26** 停用登录提示 2004 + 垃圾桶页提前切 V2（4 接口，M5b-8 的 UI 部分）+ 删死代码 CoursePlaza + `ui/confirmDelete.tsx` 统一确认封装（spec §7，含 confirmDisable）+ **geek-green/cyber-bg @theme 桥接修复**（漏桥接致工具类透明隐形，创建老师按钮/选中 tab 长期黑字不可见的根因）+ **管理端翻浅色**（用户拍板后台新风格：浅底深字、看得见优先）。e2e 14/14 绿、231 绿；用户终验停用/启用/2004 提示通过。测试账号 `test-disabled/123456` 留库可复用。
- **2026-10-11 M5b-3 完成**：站内信 12 调用点切 V2（文件夹/消息全 id 制、读即已读、详情单独取、md 沿用现成 marked 渲染零新增）+ 发送面板换 V2 学生源（receiver_id）+ **群发灰按钮（P-a A，M5b-5 恢复）** + 教师端框架/外壳/发送面板翻浅（4 数据面板随各自域块翻，分期有因）+ FolderTreePanel 基件换 confirmDelete 契约弹窗（Notebook 受益）+ 缓存失效修复（移动即见、删夹信回流）+ 详情区 flex-1 布局修正 + workers:1 串行根治跨文件测试竞争。e2e 18/18、231 绿、用户 7 条终验过（当场修复中文编码/横扩/缓存三反馈）。**服务改 Start-Process 独立进程，脱离会话不再被回收**。
- **2026-10-12 切流第一批**：① **Caddy**：`/api` 反代 8000→**8001**（Caddyfile + generate_caddyfile.py 默认值同步；8001=升级语义 D32）② **启动器**：`backend_dir`→`backend_v2`、FASTAPI_PORT→8001、解释器优先 `backend_v2/.venv`→便携→自身③ **Scratch 课件保存切 V2**（§5.2）：`saveToCourse({assetId, lessonName})`，上传走 `POST /api/assets/{id}/files/upload`（multipart file+path + Bearer），菜单栏课程下拉改 `GET /api/assets`（只列自己的课程族）。**全栈真实流程实测通过**：Caddy(:80)→账号菜单→保存到课程→✅→L1/main.sb3+student.sb3 落盘读回。**数据不迁移**（D31）。⚠️ 挂账：register.py 批量注册仍 V1 方言待迁。
- **2026-10-12 M5b-8 完成 · 全站收口**：AttendancePanel 垃圾桶 3 调用点切 V2（`/api/trash` 形状：id/type/name/deleted_at，只显 class 条目；恢复 `POST /trash/{id}/restore`；彻底删 `DELETE /trash/{id}` 换 confirmDelete 不可恢复文案 + **仅 admin 显钮**；顺手删死参 username（V2 token 身份后无调用方））。e2e E33（删班→桶→恢复闭环，先红后绿；测试开头清历史桶——历年软删班全堆桶里曾撑出 83 个同名钮）+ **全量 37/37**、tsc 0、lint 回基线。**🏁 M5b 全部 8 块完成：全站 V1 调用清零**（端点扫描仅剩 1 句注释提及 V1）。
- **2026-10-12 M5b-6 完成**：资产族三面全切 V2。**S1 公开读面**：`api.ts` 资产层重写（`fetchCourses` 一次全量取代逐师 N+1 挂账兑现、删 `fetchTeacherNames`、`fetchFileEntries` 逐层拉取、`fileHref/zipHref/scratchFolderHref` 链接器）+ `types/course.ts`（bucket 四值、删 material_count/teacher_key/shelf）+ 5 组件（CourseCatalog/CourseDetail/ResourceFileTree/Resources/Downloads，P4 计数移除、ebook 状态词改 `publish.state==='done'`、树改逐层懒加载 P2）。**S2 管理面**：新 `AssetManager.tsx`（P1 一页管 bucket 四选一；CRUD+星标挪书架+发布轮询（state: none/queued/building/done/failed）+回收站（V2 形状，永久删仅 admin 显钮）+浅色风格）；**删 CourseManager+ResourcesManager（1900 行孪生）**，TeacherDashboard 三 tab 收敛「资产管理」。**S3 文件树**：逐层钻取 + 上传（文件/文件夹 webkitdirectory）/建目录/改名/物理删（不可恢复二次确认）/上下移排序（D30）/下载。e2e 新增 `assets.spec.ts` E28~E29 + `assets-manage.spec.ts` E30~E32，**全量 36/36**；后端 **235**（+1 守卫测试）；tsc 0、lint 零新增（净减 2）。🐛 修复：① `apiV2` FormData 不塞 json 头（上传报「缺少参数：file」根因）② **SQLite 老库陷阱二咬**（dev 库 file_order 残留 scope/scope_id 旧骨架 → 列表接口 500；`db.py::_ensure_tables` 守卫删表重建 + 测试，上传不碰表所以完好）③ e2e 模糊匹配撞 msg 提示条（改 exact）+ confirmDelete 确认钮实为「确认」。经验：8001 会被起两个 uvicorn（.venv 与 runtime 便携版），跑测前查端口归属；vite 客户端 fetch 的 Content-Type 决定后端能不能收到 multipart。端点扫描：资产族 V1 调用清零，全仓仅剩 AttendancePanel 3 处 trash（M5b-8）。
- **2026-10-12 M5b-7 完成**：课堂记录 4 调用点切 V2（`AttendancePanel.tsx` 单文件；班级名→class_id、`username=` 全删、保存后二次 GET 并入 `loadSessions()`）+ **D27** 落地（`content` = JSON.stringify(学生记录数组)，`parseSessionStudents` 解析失败容错为空）+ **D28** 拍板（终验合并到最后一次性）。e2e 新文件 `class-records.spec.ts` E25~E27 先红（V1 端点对 V2 404）后绿、**全量 31/31**、后端 234 绿（零改动）、tsc/eslint 零新增（3 条同规则同数基线）。学生视角 `?student=` 本期不接线（V2 端点已备好）。端点扫描：课堂记录域零 V1 残留；剩余 V1 = M5b-6 资产族 + M5b-8 trash 族（AttendancePanel/CourseManager/ResourcesManager 各 3 处）。
- **2026-10-11 M5b-5 完成**：班级学生域 11 调用点切 V2（AttendancePanel：列表无学生→逐班详情合并、§455 全量拉取、PATCH 改排课/改名全 id 制、移出学生不删号、find-student→`/api/students?q=` 重名检测、V1 二次 GET 并入 loadClasses）+ **P5** 新建学生默认密码 111111（后端按 username 自动挂接已有号）+ **P6 群发恢复**（班级下拉 + 人数按钮 + 失败计数；M5b-3 灰按钮挂账兑现）+ `types.ts` ApiStudent.id number→string。e2e 26/26 绿、后端 231 绿、eslint 顺手清 7 条老账；用户拍板 UI 拟重设计免手测（功能层以 e2e 为准）。
- **2026-10-12 班级管理重设计 + 新建学生卡片 记档**（非 M5b 块，用户逐条指定）：`ui/NewAccountCard.tsx` 通用卡片（学生/老师：账号/密码/备注/入学·入职时间一列两义/学生可选初始班级，建号即入班，自带成功页）；`ClassManager.tsx` 回归纯班级管理（建班/搜索已有账号添加入班）；管理端「创建老师/新建学生」双按钮统一走卡片（行内表单删除）；教师侧栏「新建学生」直达卡片；删 `NewStudentCard.tsx`/`StudentListPanel.tsx`（死代码）。后端：`accounts` 加 `remark`/`enrolled_at`（`_ensure_columns` 老库补列迁移）、POST/PATCH 带档案字段（remark/enrolled_at 仅 admin 可改）、teacher/admin 学生视图返回；验收单 +3 → **234 绿**。**学生身份定稿**：id（系统唯一）+ 账号名（可改不可重，登录用兼显示名）+ 密码（老师自设 ≥6 位，不预填）；「姓名」砍掉、`name` 死列彻底移除（旧库残留空列无害）；不用先选班就能建学生，账号↔班级解耦。文档：database.md 整体修订、spec §3.2 档案字段定稿。验证：e2e 28/28、后端 234、tsc OK、lint 零新增。⚠️ 教训两条：**改后端必须重启 8001**；**改 UI 入口先同步老用例**（E8 挂旧「确定」按钮 90 秒超时，形似卡死）。**权限备忘（学生管理阶段实现）**：username 编辑权——老师可改学生/自己的用户名（防建号写错），学生一律不可改（防捣乱）；`descriptors` 定位=学生完整备注资料。
- **2026-10-11 M5b-4 完成**：笔记 10 调用点切 V2（`Notebook.tsx` 单文件；笔记本/笔记全 id 制、名字只作面板键请求层互查；保存改真 `PATCH /api/notes/{id}`、重命名 `PATCH {name/title}`；`page_size=100` 翻页拉尽=点击零请求 §455）+ **P3 拍板落地**：标题纯文本不拼 `.md` + 删本确认补「不可恢复，含全部笔记」（基件 folderDeleteNote 预留位兑现）+ **选中样式两轮拍板**（去左侧竖条→再去蓝底，文件夹只留白字加粗；条目高亮原样；邮箱同步）+ `<Notebook />` 去 username。e2e notes E15~E18 先红后绿、**全量 22/22**、后端 231 绿、eslint 零新增（基线 8→6 老账）；用户终验 6 条通过（含自动保存链路：切走即存/保存按钮/Ctrl+S 三通道）。
- **环境备忘（2026-10-11）**：跑 e2e 前必须清 `HTTP_PROXY/HTTPS_PROXY`（Clash 7897 会让 Playwright webServer 健康检查走代理请求 localhost → 502 → 卡满 120s 报 webServer 超时，服务本身其实活着）。
