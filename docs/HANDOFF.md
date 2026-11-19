# 会话交接（HANDOFF）

> 给"下一个会话"的入口文档。**先读它，再读 `AGENTS.md` 与 `docs/PROGRESS.md`**。
> 最后更新：2026-11-19（S2-5 完成）

## 1. 一句话现状

**S0 + S1 + S2-1（16 表）+ S2-2（JWT）+ S2-3（隔离）+ S2-4（分页）+ S2-5（票据 CRUD）全部完成**，自动化验收全绿；
**下一步是 S2 阶段的第六张卡：S2-6 文件上传 + MinIO + sha256 去重**。

## 2. 仓库与环境

| 项 | 值 |
|---|---|
| 仓库 | `E:\BiSheXZY\study-OCR`（分支 `main`，**本地领先 origin 17 个 commit，待用户 push**） |
| Python | 3.11.9（`E:\BiSheXZY\study-OCR\.venv`，**所有脚本都用 `.venv/Scripts/python.exe`**） |
| 关键版本 | numpy **1.26.4**、opencv-python **4.6.0.66**（含 contrib 同版本）、paddlepaddle **2.6.2**、paddleocr **2.7.3**、Pillow 12.3.0、PyMuPDF 2.1、SQLAlchemy **2.0.58**、Alembic **1.20.1**、PyJWT **2.15.1**、passlib **1.7.4**（Argon2id） |
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
backend/app/api/v1/invoices.py + schemas/invoice.py  ← S2-5：票据 4 条 HTTP 接口与出入参
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
(cd backend && ../.venv/Scripts/python.exe -m pytest)                        # 310 passed（默认跳过集成）
(cd backend && ../.venv/Scripts/python.exe -m pytest -m integration)         # 118 passed（需 Docker）
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

## 7. 下一步：S2-6 文件上传 + MinIO + sha256 去重

- 契约：`docs/API.md` §2 `POST /invoices/upload`（202 + `invoice_id/file_id/job_id/sha256`；`sha256` 去重 409/40901；
  ≤20MB；MIME + 魔数双校验；PDF ≤5 页；响应**不得出现**对象名/路径）；
- 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 的 S2-6；
- 流程（AGENTS §11）：**先 spec（`docs/spec/s2-6-upload.md`）→ plan → 用户确认 → 写码**；
- 底座：`invoice_service.create_draft()`（建票）、`api/deps.py`、`Settings`（minio/MAX_UPLOAD_MB）、`core/redis.py`、
  `ocr/intake.py`（S1 的 PDF/照片分流）；
- 需要先想清楚的一点：契约要求"内存中信封加密"再写 MinIO，但信封/ SM4 在 S4-3/S4-4 才实现。
  S2-6 的 spec 必须先定：**是先把密文链路做出来（提前 S4-3/4 的一部分），还是按 SCHEMA 允许的
  `is_cipher=false` 本地调试路径先落明文并在 S4 补加密**（SCHEMA §3.1 明确 `false` 只允许出现在本地调试配置下）。
  这个取舍要在 S2-6 的 spec 里报告并让你拍板，**不要默默落明文**。

## 8. 未闭合 / 需要用户参与的点

1. **push**：本地 17 个 commit 需要用户执行 `git push`（AI 不推）；
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