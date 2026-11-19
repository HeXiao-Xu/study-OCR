# 会话交接（HANDOFF）

> 给"下一个会话"的入口文档。**先读它，再读 `AGENTS.md` 与 `docs/PROGRESS.md`**。
> 最后更新：2026-11-19（S2 收口 + UI 整理 + 管理端模板页）

## 1. 一句话现状

**阶段 2（基础框架）全部完成**（S0 + S1 + S2-1 ~ S2-12），自动化验收全绿；
**下一步进入阶段 3：S3-1 OCR Worker 串起全链路**。

## 2. 仓库与环境

| 项 | 值 |
|---|---|
| 仓库 | `E:\BiSheXZY\study-OCR`（分支 `main`，**本地领先 origin 30 个 commit，待用户 push**） |
| Python | 3.11.9（`E:\BiSheXZY\study-OCR\.venv`，**所有脚本都用 `.venv/Scripts/python.exe`**） |
| 关键版本 | numpy **1.26.4**、opencv-python **4.6.0.66**（含 contrib 同版本）、paddlepaddle **2.6.2**、paddleocr **2.7.3**、Pillow 12.3.0、PyMuPDF 2.1、SQLAlchemy **2.0.58**、Alembic **1.20.1**、PyJWT **2.15.1**、passlib **1.7.4**、python-multipart **0.0.34**、ARQ **0.28.0**、redis-py **5.3.1**（ARQ 要求 `redis<6`，D-045） |
| pip 源 | 已配阿里云全局镜像（`%APPDATA%\pip\pip.ini`），装依赖不用再加 `-i` |
| OCR 模型 | 已缓存在 `~/.paddleocr/whl/{det,rec,cls}`（约 18MB），离线可用 |
| Docker | `study-ocr-{postgres,redis,minio}` 三个容器 **healthy**（5433 / 6380 / 9002，控制台 9003）；**库已迁到 `0002`（16 业务表 + alembic_version）** |
| 依赖锁 | `backend/requirements.txt`（手写锁）+ `requirements.lock.txt`（pip freeze） |

> ⚠️ `gmssl` **还没装**——那是 S4 密码链路才需要的，别提前装（会打乱 numpy 版本）。

## 3. 目录与关键文件（认路用）

```
backend/app/models/      ← S2-1：16 张表模型（一表一文件）+ base.py（命名约定/CHECK 助手）+ enums.py
backend/app/core/database.py  ← S2-1：`postgresql://` → `postgresql+psycopg://` 方言映射 + 引擎/会话工厂
backend/alembic/         ← S2-1/S2-2：alembic.ini / env.py / versions/{0001_init,0002_audit_action_login_logout}.py
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
backend/app/tasks/{worker,ocr_task,queue}.py  ← S2-7：ARQ WorkerSettings、任务函数、入队协议
backend/app/api/v1/{invoices,files,templates}.py + schemas/{invoice,template}.py  ← 票据/上传/模板接口
web/src/api/{http,auth,files,invoices}.ts + stores/user.ts + router + layout/MainLayout.vue  ← 会话/刷新/上传/轮询
web/src/views/{Login,Forbidden,Placeholder,Upload,InvoiceList,InvoiceDetail}.vue  ← 登录/403/占位/上传/列表/详情
web/src/utils/format.ts + types/invoice.ts  ← 金额/时间/标签 + 完整票据类型
web/src/styles/theme.css + utils/landing.ts  ← 全局视觉基线 + 登录后角色落地
web/src/views/TemplateAdmin.vue + api/templates.ts + types/template.ts  ← 管理端模板/规则/升版本/试跑
scripts/bench_list.py               ← S2-4：5 万行 p50/p95 + EXPLAIN ANALYZE（临时库，跑完自删）
backend/app/ocr/          ← S1 的全部产出
  preprocess.py             预处理（EXIF→定位→透视/降级→去斜→照度→印章）+ needs_retake
  intake.py                 输入分流：PDF 只渲染、照片走预处理 + 重拍闸门   ← S3 会用
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
docs/{spec,plan}/         ← 每张卡一份 spec + plan（S1-1 ~ S1-6、S2-1 齐全）
docs/{SCHEMA,API,PROGRESS,DECISIONS,EVAL}.md  ← 契约与决策，改动前必读
```

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

**最弱环节**：`taxi_ticket` 整单只有 0.200（纸面约 300px 高、字太小）、`total_amount` 80%；
`vat_general` 的 amount/tax 靠"合计约束反解"补到 83–86%（见 D-033）。

## 5. 一条命令复现全部验收

```bash
cd /e/BiSheXZY/study-OCR
(cd backend && ../.venv/Scripts/python.exe -m alembic upgrade head)          # 16 表，head=0002
(cd backend && ../.venv/Scripts/python.exe -m alembic check)                 # 模型↔库零差异
(cd backend && ../.venv/Scripts/python.exe -m pytest)                        # 332 passed（默认跳过集成）
(cd backend && ../.venv/Scripts/python.exe -m pytest -m integration)         # 146 passed（需 Docker）
# 模板 seed（幂等；部署时调用一次）
(cd backend && ../.venv/Scripts/python.exe ../scripts/seed_templates.py)
# ARQ worker（另开一个终端；与 API 共用同一份 backend/.env）
(cd backend && ../.venv/Scripts/python.exe -m arq app.tasks.worker.WorkerSettings)
# 上传（后端起）：先登录拿 token，再 multipart 上传；同文件二次上传应 409
curl -s -F "file=@票据.jpg" localhost:8000/api/v1/invoices/upload -H "Authorization: Bearer <token>"
# 前端（Vite 5173 + 无头 Edge 9222）：类型检查/构建 + 真实登录冒烟（需要先起后端）
(cd web && npm run typecheck && npm run build)
node scripts/ui_smoke.mjs "http://127.0.0.1:5173/login" --clear --view 1440x900 \
  --fill 'input[autocomplete="username"]' <用户名> \
  --fill 'input[autocomplete="current-password"]' <口令> \
  --click 登录 --expect 我的票据 --shot ui.png
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
.venv/Scripts/python.exe scripts/smoke.py                                   # 8 OK / 0 FAIL
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

## 7. 下一步：S3-1 OCR Worker 串起全链路（阶段 3 开始）

- 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 的 S3-1（★关键）；
- 流程（AGENTS §11）：**先 spec（`docs/spec/s3-1-ocr-pipeline.md`）→ plan → 用户确认 → 写码**；
- 目标：`ocr_task` 取 `job_id` → `invoice_files.storage_key` → MinIO 下载到临时目录（用后必删）→
  PDF 转图（限前 3 页）→ `preprocess` → detector/recognizer → `layout.classify_ticket` →
  `extractor.extract` → `validators.validate`；任何一步异常写 `ocr_jobs.error` 并置 `failed`；
  单任务 180s 超时；PaddleOCR 用 worker 级单例。
- **必须先对账的契约/实现缺口**（不要静默选边）：
  1. 任务卡要求把 `timings` 写 `ocr_jobs.metrics(JSONB)`，但 SCHEMA/模型 **没有 `metrics` 列**
     （只有 `params_json`/`elapsed_ms`）。要么改 SCHEMA 加列 + 迁移，要么用现有列/另表，spec 里拍板；
  2. `GET /invoices/{id}/ocr-status`（stages/progress）**尚未实现**，且 `ocr_jobs` 没有
     `started_at/finished_at`（契约响应里有这两个字段）——S3-1 要么补列，要么改契约；
  3. worker 目前只改 `ocr_jobs`，**没有更新 `invoices.ocr_status`**；S3-1 必须把票据级状态一起推进；
  4. `GET /invoices/{id}/fields`（S3-2/S3-4）未实现；详情接口已返回 fields，可先复用；
  5. 任务卡写 PDF 用 `pypdfium2`，但 S1 现有 PDF 处理走 PyMuPDF（见关键版本）；**先看 `ocr/intake.py`，
     不要为了任务卡一句话重复引入 PDF 引擎**；
  6. 上传落 MinIO 仍是明文（`is_cipher=false`，D-044），S3-1 直接按 `storage_key` 下载；
     S4 接信封后这里要改成"解密后进内存"，spec 里留好接缝。
- 验收：上传 20 张成功 ≥18，失败项 `error` 能定位原因；单张 P95 < 30s（CPU）；临时目录无残留；
  上传 → worker → `invoice_fields` 落库 → 详情可见。
- 底座：`app/tasks/ocr_task.py::_run_pipeline` 就是占位入口（现在抛 `TaskNotReadyError`）；
  `job_service` 的幂等/重试/僵尸恢复已就绪；`ocr/templates/*.yaml` 与 S2-8 的 DB 模板都可用。
- 注意：S2-8 只做了**模板/规则的后端**；管理端页面已在用户反馈后补齐（`/templates`，D-052），
  `/rules` 重定向到模板页，规则在模板详情里维护。票种范围固定 5 类 + unknown（D-054），新建入口已移除。

## 8. 未闭合 / 需要用户参与的点

1. **push**：本地 30 个 commit 需要用户执行 `git push`（AI 不推）；
2. **人工抽查**（AI 看不了图）：`backend/eval/results/preprocess/*_compare.jpg`、`layout/*_layout.jpg`；
3. `taxi_ticket` 整单 0.200：计划在 **S3-3（低置信二次识别）** 解决，不要在 S1 继续调；
4. `total_amount` 80%：下轮可用 `amount+tax` 与候选交叉校验择优（已在 PROGRESS 遗留）；
5. `intake.prepare_input` 还没接进 Worker —— **S3-1 串流水线时必须接上**（接口已定好）；
6. 真实样本只剩 1 份（数电票 PDF，仅本地）：论文需如实写"合成集为主 + 1 份真实件定性校验"；
7. 审计动作：`login_failed`/`logout`/`forbidden` 枚举已加（0002），但 S2-2 **未写审计行**（哈希链在 S4-7）；
   `template_create`/`report_create`/`issue_cert` 三个值仍缺，对应任务（S2-8 / S5-1 / S4-6）开始前补。

## 9. 工作方式约定（用户偏好，新会话请沿用）

- 用户是"放手型"：希望**替他做决定并给理由**，事后复核，不要每步都问；
- 但**功能级任务卡要走 spec → plan → 确认**（AGENTS §11），小修/bugfix 可只在对话里给设计；
- 汇报风格：先给**实测数字与命令输出**，再解释"为什么这样做"，**不要粉饰**（做不到就直说）；
- 每次收口都要更新 `docs/PROGRESS.md`，新增取舍写 `docs/DECISIONS.md`；
- 提交信息用 Conventional Commits（含任务号，如 `feat(s2-1): ...`）；**push 永远由用户做**。