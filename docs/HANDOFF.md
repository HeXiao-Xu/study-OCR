# 会话交接（HANDOFF）

> 给"下一个会话"的入口文档。**先读它，再读 `AGENTS.md` 与 `docs/PROGRESS.md`**。
> 最后更新：2026-11-20（S3-2 置信度聚合与确认队列上线）

## 1. 一句话现状

**阶段 3 已通两张关键卡**：S3-1（识别全链路）+ S3-2（置信度三档与 reviewer 队列）都已上线；
**下一步 S3-3 局部二次识别（crop 放大重识别）**。

当前浏览器可走通：登录（三角色）→ 按角色落地 → 上传（"识别中… N%"）→ 票据列表/详情
（字段带 `review_level`，<90% 黄、<60% 红）→ reviewer 的"待核对"= `extract_status=need_confirm` 列表
→ 管理端模板查看/升版本/试跑；`GET /invoices/{id}/ocr-status` 可轮询阶段与进度。

## 2. 仓库与环境

| 项 | 值 |
|---|---|
| 仓库 | `E:\BiSheXZY\study-OCR`（分支 `main`；本地领先 origin 若干 commit，**push 由用户做**） |
| Python | 3.11.9（`E:\BiSheXZY\study-OCR\.venv`，**所有脚本都用 `.venv/Scripts/python.exe`**） |
| 关键版本 | numpy **1.26.4**、opencv-python **4.6.0.66**（含 contrib 同版本）、paddlepaddle **2.6.2**、paddleocr **2.7.3**、Pillow 12.3.0、PyMuPDF 2.1、SQLAlchemy **2.0.58**、Alembic **1.20.1**、PyJWT **2.15.1**、passlib **1.7.4**、python-multipart **0.0.34**、ARQ **0.28.0**、redis-py **5.3.1**（ARQ 要求 `redis<6`，D-045）、APScheduler **3.11.1** + tzlocal **5.4.5**（S2-12） |
| pip 源 | 已配阿里云全局镜像（`%APPDATA%\pip\pip.ini`），装依赖不用再加 `-i` |
| OCR 模型 | 已缓存在 `~/.paddleocr/whl/{det,rec,cls}`（约 18MB），离线可用 |
| Docker | `study-ocr-{postgres,redis,minio}` 三个容器（5433 / 6380 / 9002，控制台 9003）；**库已迁到 `0004`（16 业务表 + alembic_version）** |
| 依赖锁 | `backend/requirements.txt`（手写锁）+ `requirements.lock.txt`（pip freeze） |

> ⚠️ `gmssl` **还没装**——那是 S4 密码链路才需要的，别提前装（会打乱 numpy 版本）。

### 2.1 本地开发库现状（今天交接时）

- 账号：`alice`（submitter）、`bob`（reviewer）、`root`（admin）；口令是本地开发口令，未写进仓库。
  新会话做登录冒烟时，若不知道口令，用 `scripts/create_user.py` 新建一个用户并用自己的口令；reviewer/admin 只能脚本建。
- 数据：**5 个 seed 模板**（vat_general / vat_digital / train / flight / taxi）、**1 张测试票据**（之前本地上传的合成样本）、0 条审计行；
  如需干净演示，先清理测试用户/票据/MinIO 对象再跑 seed（不要直接删库）。
- 端口：后端 8000、Vite 5173、MinIO 控制台 9003；无头冒烟 Edge 用 9222（临时 profile）。

## 3. 目录与关键文件（认路用）

```
backend/app/models/      ← S2-1：16 张表模型（一表一文件）+ base.py（命名约定/CHECK 助手）+ enums.py
backend/app/core/database.py  ← S2-1：`postgresql://` → `postgresql+psycopg://` 方言映射 + 引擎/会话工厂
backend/alembic/         ← 0001_init / 0002_audit_action_login_logout / 0003_ticket_type_vat_digital.py
backend/app/core/{security,redis}.py  ← S2-2：Argon2id + HS256 JWT；Redis 单例
backend/app/services/{auth_service,token_store}.py  ← S2-2：注册/认证/签发/轮换/吊销；refresh 白名单
backend/app/api/{deps.py,v1/auth.py}  ← S2-2：Bearer 解析、get_current_user、require_roles、5 条 auth 路由
backend/app/services/repository.py  ← S2-3：行级隔离（策略表 + filter_visible + get_visible_or_404，deny-by-default）
backend/app/services/pagination.py  ← S2-4：分页/排序协议（PageParams/PageResult、白名单排序、同一 stmt 求 total）
backend/app/services/invoice_service.py  ← S2-5：票据建草稿/状态机/列表/详情/软删/字段修改
backend/app/services/{storage,upload_service}.py  ← S2-6：MinIO 封装 + 上传校验/去重/补偿
backend/app/services/job_service.py  ← S2-7：作业状态机/幂等锁/重试/僵尸恢复
backend/app/tasks/scheduler.py  ← S2-12：APScheduler + 按时段 Redis 锁（每日锚定）
backend/app/services/{template_service,template_registry,template_seed}.py  ← S2-8：模板 CRUD/升版本/60s 缓存/seed
backend/app/tasks/{worker,ocr_task,queue}.py  ← S2-7：ARQ WorkerSettings、任务函数、入队协议（S3-1 起接真实流水线，max_jobs 默认 1）
backend/app/services/ocr_pipeline.py  ← S3-1：流水线唯一编排（下载→渲染/预处理→识别→票种→抽取→校验→落库）
backend/app/services/ocr_status_service.py  ← S3-1：GET /invoices/{id}/ocr-status 读模型（stages/progress）
backend/app/api/v1/{invoices,files,templates}.py + schemas/{invoice,template}.py  ← 票据/上传/模板接口
web/src/api/{http,auth,files,invoices,templates}.ts + stores/user.ts + router + layout/MainLayout.vue  ← 会话/刷新/上传/轮询
web/src/views/{Login,Forbidden,Placeholder,Upload,InvoiceList,InvoiceDetail}.vue  ← 登录/403/占位/上传/列表/详情
web/src/utils/format.ts + types/invoice.ts  ← 金额/时间/标签 + 完整票据类型
web/src/styles/theme.css + utils/landing.ts  ← 全局视觉基线 + 登录后角色落地
web/src/views/TemplateAdmin.vue + api/templates.ts + types/template.ts  ← 管理端模板/规则/升版本/试跑
scripts/bench_list.py               ← S2-4：5 万行 p50/p95 + EXPLAIN ANALYZE（临时库，跑完自删）
backend/app/ocr/          ← S1 的全部产出
  preprocess.py             预处理（EXIF→定位→透视/降级→去斜→照度→印章）+ needs_retake
  intake.py                 输入分流：prepare_pages（PDF 前 3 页）/ prepare_input（首页）
  detector.py / recognizer.py / types.py   PaddleOCR 封装 + OcrLine/OcrResult 契约
  layout.py + templates/*.yaml              票种识别（5 类）+ 区域划分
  extractor.py + templates/field_rules.yaml 字段抽取（锚点规则）
  validators.py             校验器（纯函数 + Decimal）
backend/eval/
  metrics.py                全部指标（纯函数，唯一实现）
  configs/baseline.yaml     基线配置
  datasets/annotations_synth_v1.jsonl   60 张标注
  results/baseline_report.md            ← 论文可直接用的 Markdown 表（已入库）
scripts/                  ← 所有跑批脚本（make_samples / verify_samples / ocr_demo /
                              layout_eval / field_eval / ocr_baseline / preprocess_demo …）
docs/{spec,plan}/         ← 每张卡一份 spec + plan（S1-1 ~ S1-6、S2-1 ~ S2-12 齐全；S3 起新建）
docs/{SCHEMA,API,PROGRESS,DECISIONS,EVAL}.md  ← 契约与决策，改动前必读
```


### 3.1 前端已实现路由（浏览器可直接走）

| 路由 | 页面 | 角色 |
|---|---|---|
| `/login` | 登录（分栏品牌页） | 公开 |
| `/invoices` | 票据列表（筛选同步 URL） | submitter / reviewer |
| `/invoices/:id` | 票据详情 + 字段编辑 | submitter / reviewer |
| `/upload` | 上传（拖拽/多选/去重/轮询） | submitter |
| `/templates` | 管理端模板/规则/升版本/试跑 | admin |
| `/rules` | 重定向到 `/templates` | admin |
| `/403` | 无权限 | 登录用户 |
| `/debug` | 联调自检（不在菜单） | 登录用户 |

前端没有 Vitest/组件单测；验证手段是 `npm run typecheck` + `npm run build` + `scripts/ui_smoke.mjs`。
冒烟支持 `--fill/--clear/--view/--shot/--steps/--upload`；无头 Edge 必须显式 `--view`，否则默认视口只有 500px，
登录页品牌区会被响应式规则隐藏（曾因此误判）。

## 4. 当前指标（60 张合成集，CPU）

| 指标 | 值 | 出处 |
|---|---|---|
| 检测 Hmean | **0.951**（P 0.967 / R 0.930） | `scripts/ocr_baseline.py` |
| 字符准确率 | **0.819** | 同上 |
| 字段级准确率 | **0.895**（385/430） | 同上 / `field_eval.py` |
| 整单正确率 | **0.650** | 同上 |
| 票种判对率 | **100%**（60/60） | `layout_eval.py` |
| 预处理 | P50 75ms / P95 201ms；正视角 17 / 透视 21 / 兜底 22 / 失败 0 | `preprocess_demo.py` |
| OCR 单张推理 | P50 ≈1.4s（P95 ≈2.0s） | baseline 报告 |
| S3-1 真实链路 20 张 | **20/20 成功**、P50 1012ms、P95 1680ms、max 4995ms | `scripts/ocr_acceptance.py`（`backend/eval/results/ocr_acceptance_*.json`） |
| S3-2 置信度分布（40 张 / 213 字段） | auto 101 / review 110 / manual 2；阈值 0.90 时 40/40 张进待核对（合成集偏难） | `scripts/confidence_eval.py`（`confidence_eval_*.json`） |

**最弱环节**：`taxi_ticket` 整单只有 0.200（纸面约 300px 高、字太小）、`total_amount` 80%；
`vat_general` 的 amount/tax 靠"合计约束反解"补到 83–86%（见 D-033）。

## 5. 一条命令复现全部验收

```bash
cd /e/BiSheXZY/study-OCR
(cd backend && ../.venv/Scripts/python.exe -m alembic upgrade head)          # 16 表，head=0004
(cd backend && ../.venv/Scripts/python.exe -m alembic check)                 # 模型↔库零差异
(cd backend && ../.venv/Scripts/python.exe -m pytest)                        # 365 passed（默认跳过集成）
(cd backend && ../.venv/Scripts/python.exe -m pytest -m integration)         # 169 passed（需 Docker）
# 模板 seed（幂等；部署时调用一次）
(cd backend && ../.venv/Scripts/python.exe ../scripts/seed_templates.py)
# ARQ worker（另开一个终端；与 API 共用同一份 backend/.env）
(cd backend && ../.venv/Scripts/python.exe -m arq app.tasks.worker.WorkerSettings)
# 上传（后端起）：先登录拿 token，再 multipart 上传；同文件二次上传应 409
curl -s -F "file=@票据.jpg" localhost:8000/api/v1/invoices/upload -H "Authorization: Bearer <token>"
# S3-1 链路验收（API + worker 都在跑；先建一个自己的 submitter 账号）
.venv/Scripts/python.exe scripts/ocr_acceptance.py --samples 20 --username <账号> --password <口令>
# S3-2 置信度验收（离线，不写库；40 张约 2 分钟）：阈值扫描单调性 + 校验封顶 + auto/review 口径
.venv/Scripts/python.exe scripts/confidence_eval.py --samples 40
# 轮询识别进度（stage/progress/起止时间）
curl -s localhost:8000/api/v1/invoices/1/ocr-status -H "Authorization: Bearer <token>"
# 前端（Vite 5173 + 无头 Edge 9222）：类型检查/构建 + 真实登录冒烟（需要先起后端）
(cd web && npm run typecheck && npm run build)
# 无头 Edge（另起一个进程；bash 路径，PowerShell 用引号包住 exe）
"/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe" --headless=new --window-size=1440,900 \
  --remote-debugging-port=9222 --user-data-dir="C:\\Users\\HEXIAO\\AppData\\Local\\Temp\\dsh-edge" about:blank
node scripts/ui_smoke.mjs "http://127.0.0.1:5173/login" --clear --view 1440x900 \
  --fill 'input[autocomplete="username"]' <用户名> \
  --fill 'input[autocomplete="current-password"]' <口令> \
  --click 登录 --expect 我的票据 --shot ui.png
# 需要登录冒烟但不知道现有账号口令时：先 create_user.py 新建一个 submitter，再用它登录
# 上传页冒烟（先登录，再直接打开 /upload；--upload 走 CDP 塞文件）
node scripts/ui_smoke.mjs "http://127.0.0.1:5173/upload" \
  --upload 'input[type=file]' 'E:\BiSheXZY\study-OCR\samples\synth_v1\vat_digital_landscape_001.jpg' \
  --expect 识别失败
# 详情编辑冒烟（有序动作：点编辑 → 填字段 → 保存）
node scripts/ui_smoke.mjs "http://127.0.0.1:5173/invoices/1" \
  --steps '[{"type":"click","value":"编辑字段"},{"type":"fill","selector":"input[data-field=\"buyer_name\"]","value":"新购买方"},{"type":"click","value":"保存修改"}]' \
  --expect 新购买方
# 定时任务双进程验证（两个终端各起一个；TTL 必须小于周期）
SCHEDULER_ENABLED=true ANCHOR_CRON_HOUR='*' ANCHOR_CRON_MINUTE='*' ANCHOR_CRON_SECOND='*/10' \
  ANCHOR_LOCK_TTL_SECONDS=5 ../.venv/Scripts/python.exe -m uvicorn app.main:app --port 8000
SCHEDULER_ENABLED=true ANCHOR_CRON_HOUR='*' ANCHOR_CRON_MINUTE='*' ANCHOR_CRON_SECOND='*/10' \
  ANCHOR_LOCK_TTL_SECONDS=5 ../.venv/Scripts/python.exe -m uvicorn app.main:app --port 8001
.venv/Scripts/python.exe scripts/bench_list.py --rows 50000 --iterations 50  # 分页基准（临时库）
.venv/Scripts/python.exe scripts/smoke.py                                   # 9 OK / 0 FAIL（Vite 在跑时）
# 建 reviewer / admin 账号（注册接口只产生 submitter）
(cd backend && ../.venv/Scripts/python.exe ../scripts/create_user.py --username reviewer1 --role reviewer --real-name 审核员)
.venv/Scripts/python.exe scripts/check_annotations.py --strict
.venv/Scripts/python.exe scripts/verify_samples.py                 # 数据集 5 项
.venv/Scripts/python.exe scripts/preprocess_demo.py --limit 0      # 预处理分布 + 重拍数
.venv/Scripts/python.exe scripts/layout_eval.py                    # 票种判对率
.venv/Scripts/python.exe scripts/field_eval.py                     # 字段级表
.venv/Scripts/python.exe scripts/ocr_baseline.py --compare-preprocess   # 出论文报告
```

## 6. S1 期间做的**重要取舍**（都记在 `docs/DECISIONS.md`，别重复踩）

| 决策 | 要点 |
|---|---|
| D-023 | 票种以**数电票**为主流（旧版增值税电子普通发票为历史形态） |
| D-027 | OCR 依赖**整组锁死**：paddleocr 2.7.3 + paddle 2.6.2 + numpy 1.26 + opencv 4.6（会自动顶 numpy 2，必须压住） |
| D-028 | 票种识别用**关键字规则**，不上分类器（60 张必过拟合） |
| D-029 | 四角点四项校验（自交/退化/对边均衡/**单边裁切**）——"warp 成功 ≠ warp 正确" |
| D-031 | 检测指标用**覆盖匹配**（标注是元素级、引擎出行级）；论文必须写明口径 |
| D-032 | **排除超市购物小票**（非合规报销凭证）；范围=五类合规票据 |
| D-033 | amount/tax 用**合计约束反解**（唯一解才采用，带 `paired:sum:*` 标记） |
| D-034 | 去斜阈值**不收紧**（1.0° 会让字段准确率掉 3–5 点） |
| D-035 | **PDF 不做预处理**；照片走"拍前引导 + 拍后重拍判断"；透视加 2% 安全边距 |
| D-036 | ORM 用**同步 SQLAlchemy 2.0 + psycopg3**；`postgresql://` 在 `core/database.py` 映射成 `postgresql+psycopg://`，不改 `.env` 与健康检查 |
| D-037 | 迁移以**模型为唯一来源**，`alembic check` 零差异为门槛；**JSONB 不设 server_default**（契约未定义），`canonical_json` 保持 TEXT |
| D-038 | 认证用 **PyJWT 2.15.1 + passlib Argon2id**（m=64MiB/t=3/p=4，实测 hash 59.4ms / verify 45.7ms）；HS256 显式白名单 + `typ` 声明 |
| D-039 | **登出只吊销 refresh**：access 在剩余 30min 内仍可用（无状态 JWT 的固有代价），论文"不足"如实写 |
| D-040 | `audit_logs.action` 补 `login_failed` / `logout` / `forbidden`（0002 迁移）；**审计行留 S4-7**，S2-2 只写脱敏应用日志 |
| D-041 | 行级隔离用**应用层统一过滤**（`services/repository.py`），不用 RLS：连接池 + `SET LOCAL` 会串身份、policy 复杂度高、可测试性差 |
| D-042 | 分页层：**显式 join 而非 relationship**、count 与数据共用同一 `Select`、`id asc` 稳定兜底；keyset 分页暂不做（5 万行深页 p95 25.4ms） |
| D-043 | 票据状态机按 **D-011 的 5 值**（任务卡图不采用）；建票无 HTTP 口（S2-6 upload 调 `create_draft`）；字段修改按契约 `PATCH /{id}/fields` 在 S2-5 提前实现 |
| D-044 | S4 前上传走**本地明文调试**（`is_cipher=false`，prod 启动拒绝）；对象名按 SCHEMA `invoices/yyyy/mm/<hex>.bin`；ARQ 入队留 S2-7 |
| D-045 | 作业框架：ARQ 0.28 强制 redis-py **8.1→5.3.1**；幂等=终态跳过 + `SET NX EX 600`；重试 5s/30s（120 预留）；僵尸恢复用 `created_at` 近似（无 `started_at` 列） |
| D-046 | 模板：DB 用 SCHEMA 的 6 列（锚定规格进 `field_specs_json`）、PUT 升版本、active 互斥、60s 缓存、**seed 用显式脚本**；补 `vat_digital`（SCHEMA + 0003） |
| D-047 | 前端会话：401 **单飞 refresh**（并发复用同一 Promise）+ 守卫 `/me` 水合 + `meta.roles`；未实现页用占位；DEV `__httpDebug` 仅开发态 |
| D-048 | 上传页：轮询用 `GET /invoices/{id}`（`ocr-status` 待 S3-1）；串行上传；409 绿色去重；上限 20MB；批量不自动跳详情 |
| D-049 | 列表筛选以 `route.query` 为唯一状态源；详情影像用占位"待 S3-5"；字段编辑权限/金额确认/乐观锁；补 `vat_digital` 筛选漏项 |
| D-050 | APScheduler 3.11.1 进程内调度；每 tick 抢 Redis 锁，**成功后锁留到 TTL**（1h < 每日）做时段去重，失败释放；文档对账测试 |
| D-051 | 第一版 UI：分栏登录、深色侧栏、移除概览/联调菜单、按角色落地；不改业务逻辑，详情工作区仍等 S3-5 |
| D-052 | 管理端模板页：`/templates` 列表+详情抽屉+升版本编辑+试跑；规则随模板维护，`/rules` 重定向；无删除接口 |
| D-053 | 内容页 `el-space` 补 width:100%、去掉 1280 上限，撑满右侧；登录表单仍保持 400px |
| D-054 | 票种范围锁定 5 类 + unknown；管理端新建入口已移除，日常只升版本；新增票种需先扩 SCHEMA/模板/样本 |
| D-055 | `ocr_jobs` **一 run 一行**（stage=当前/最后阶段，逐阶段明细进 `metrics.stages`）；中途提交豁免扩到 `invoices.ocr_status='running'` |
| D-056 | `ocr_jobs` 加 `metrics/started_at/finished_at`（0004），推翻 S2-7 的"不加列"——`ocr-status` 契约要真时间 |
| D-057 | PDF 复用 PyMuPDF（不引 pypdfium2，前 3 页）；抽取规格 DB 优先/YAML 回退；worker 并发 1；协作式 deadline 165s 不硬杀线程 |
| D-058 | 置信度 = (0.35×锚点行 + 0.65×值行) × agreement × rule_factor；三档 0.90/0.60；校验失败封顶 0.45；整单分只展示不判定 |
| D-059 | 队列用 `extract_status=need_confirm`（不加 `needs_review` 状态、不加 `low_confidence` 作业态）；auto ≠ confirmed；`/review` 重定向 |

## 7. 下一步：S3-3 局部二次识别（crop 放大重识别）

- 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 的 S3-3；
- 流程（AGENTS §11）：spec（`docs/spec/s3-3-*.md`）→ plan → 用户确认 → 写码；
- 目标：按 `invoice_fields.source_line_no` 取 `ocr_lines.box_json`（四点）→ 四周扩 10% 并 clamp →
  INTER_CUBIC 放大 2–3 倍 → `recognizer.recognize_line(crop)` → 返回候选 `[{old, new, score}]` 由人选择，
  **禁止自动覆盖已人工确认值**；整块区域重识别接受 bbox；
- **落地前必须先对账**：
  1. `audit_logs.action` 枚举**没有** `field_reread`（现只有 upload/confirm_field/sign/...，0002 补过三值）；
     要么加枚举 + 迁移，要么先只写应用日志——按 §9 先报告再定；
  2. 新接口 `POST /invoices/{id}/fields/{field}/reread` 与 `POST /invoices/{id}/reread-region` 在 API.md 未定义，
     需先补契约（权限、入参、候选返回结构、422 边界）；
  3. 重识别后是否更新 `invoice_fields.confidence/need_review`：应重新走 `services/confidence.py`
     （`source=ocr_retry`，与首次识别可区分，SCHEMA §9.8），不要手改分数；
  4. `ocr_lines` 的 box 是**校正图坐标**、`invoice_fields.bbox_json` 是原图外接框——裁剪必须回到识别图坐标系，
     否则会裁错位置（S3-5 画框同理会踩）；
- 已就绪：`recognizer.recognize_line(crop)`（S1-3，空裁剪返回 `("",0.0)` 不抛异常）、`ocr_lines` 全量四点坐标、
  `confidence.resolve_field`、`invoice_fields.bbox_json`；
- 验收（任务卡）：对一张低置信金额重识别后置信度明显上升；候选列表正确；越界 bbox 返回 422 而非 500。

## 8. 未闭合 / 需要用户参与的点

1. **push**：S3-1/S3-2 的提交仍在本地（AI 不推），由用户执行 `git push`（数量以 `git rev-list --count origin/main..HEAD` 为准）；
2. **人工抽查**（AI 看不了图）：`backend/eval/results/preprocess/*_compare.jpg`、`layout/*_layout.jpg`；
3. `taxi_ticket` 整单 0.200：计划在 **S3-3（低置信二次识别）** 解决，不要在 S1 继续调；
4. `total_amount` 80%：下轮可用 `amount+tax` 与候选交叉校验择优（已在 PROGRESS 遗留）；
5. ✅ S3-1 已把 `intake.prepare_pages` 接进 Worker（PDF 前 3 页 + 照片重拍提示）；
6. 真实样本只剩 1 份（数电票 PDF，仅本地）：论文需如实写"合成集为主 + 1 份真实件定性校验"；
7. 审计动作：`login_failed`/`logout`/`forbidden` 枚举已加（0002），但 S2-2 **未写审计行**（哈希链在 S4-7）；
   `template_create` 仍缺（S2-8 已完成但未写审计），`report_create`/`issue_cert` 也未加；S4-7 前一起补。
8. `GET /invoices/{id}/image` 未实现（依赖 S4 信封，API 标 S3-5/S5-4）：详情页目前是占位，不做明文后门。
9. 管理端模板页已补（D-052），但**新建票种入口已按用户要求移除**（D-054）：票种固定 5 类 + unknown；
   新增票种必须改 SCHEMA/迁移/模板/样本并发版，不能从管理端新建。

## 9. 工作方式约定（用户偏好，新会话请沿用）

- 用户是"放手型"：希望**替他做决定并给理由**，事后复核，不要每步都问；
- 但**功能级任务卡要走 spec → plan → 确认**（AGENTS §11），小修/bugfix 可只在对话里给设计；
- 汇报风格：先给**实测数字与命令输出**，再解释"为什么这样做"，**不要粉饰**（做不到就直说）；
- 每次收口都要更新 `docs/PROGRESS.md`，新增取舍写 `docs/DECISIONS.md`；
- 提交信息用 Conventional Commits（含任务号，如 `feat(s2-1): ...`）；**push 永远由用户做**。