# S2-1 模型与首批迁移（16 张表）— 设计（spec）

> 任务卡：S2-1 ｜ 契约：`docs/SCHEMA.md`（唯一依据）｜ 上游：S0-3 定稿的 16 表清单 ｜ 下游：S2-2 认证、S2-5 CRUD、S3 识别落库
> 状态：**已实现（2026-11-19）**；实测结论见 §8，取舍见 `DECISIONS.md` D-036 / D-037。

## 1. 目标与范围

把 `docs/SCHEMA.md` 的 16 张表变成 **可往返（upgrade / downgrade）的 Alembic 迁移**，并提供 SQLAlchemy 2.0 模型作为后续所有业务代码的唯一表定义来源。

**做**：模型（一表一文件）、枚举常量、Alembic 配置与 `0001_init`、数据库方言映射与引擎/会话工厂（供迁移与 S2-2 起复用）、单元测试 + 集成测试、文档收口。

**不做**（明确划出范围）：
- 不写任何 API / service / 种子数据（S2-2 起才有）；
- 不加契约外列（`created_by`、`tenant_id`、通用 `deleted_at` 一律不加）；
- 不建 `updated_at` 触发器（SCHEMA §0.2：应用层 `onupdate`）；
- 不做审计表"只增不改"的库级拦截（S4-7 审计链负责）；
- 不上 PostgreSQL RLS（D-016：应用层过滤，RLS 列为展望）；
- 不建 async ORM（见 §3.1）。

## 2. 契约落地口径（硬约束，逐条可核对）

| 项 | 落地方式 |
|---|---|
| 表/列/类型/可空/默认/索引 | **100% 按 SCHEMA**；字段表的"默认值 —"＝不设默认，不自作主张补 |
| 主键 | `BIGINT GENERATED ALWAYS AS IDENTITY`（`sa.Identity(always=True)`） |
| 时间 | `TIMESTAMPTZ`（`DateTime(timezone=True)`）+ `server_default=now()`；`updated_at` 另加 Python 侧 `onupdate=func.now()` |
| 金额/置信度 | `NUMERIC(12,2)`、`NUMERIC(4,3)`；SQLAlchemy 侧仍返回 `Decimal` |
| 业务日期 | `invoices.invoice_date` 用 `DATE`（明确例外，不改 TIMESTAMPTZ） |
| 枚举 | 只定义在 `models/enums.py`（`StrEnum`）；每张表的枚举列各生成一条 `ck_<表>_<列>`，CHECK 文本由同一份常量拼接（不手抄） |
| 半结构化 | `postgresql.JSONB`；`report_forms.canonical_json` **必须是 `TEXT`**（JSONB 会重排键序，验签必失败） |
| 唯一/部分唯一 | 严格按 SCHEMA §7.1 的 12 条（含 5 条 `WHERE` 部分索引），索引名 `uq_` / `ix_` |
| 普通索引 | SCHEMA §7.2 的 8 条 + 各字段表"索引"列 `idx ⚠` 的占位项；统一 `ix_` 前缀 |
| 外键 | 全部显式命名 `fk_<表>_<列>`，删除行为 `NO ACTION`（不级联） |
| 软删 | 不设 `deleted_at`（D-018：票据 `void`、用户 `disabled`、签名 `void`） |

**契约与任务卡提示词冲突时以 SCHEMA 为准**（AGENTS §9），本卡发现的唯一差异见 §6。

## 3. 技术决策

### 3.1 同步 SQLAlchemy 2.0 + psycopg3（不引入 async ORM）

- **选择**：`create_engine` + `Session`（同步），驱动用已装的 `psycopg==3.3.9`，URL 方言 `postgresql+psycopg://`。
- **理由**：① S1 全链路（OCR/评测脚本）是同步代码，ARQ worker 也是同步 handler，异步 ORM 会逼出两套会话；② FastAPI 的 `def` 路由自动走线程池，同步会话不会阻塞事件循环；③ 少一层 greenlet，测试与调试简单。
- **代价**：高并发下受线程池上限约束；若将来要上 `async`，模型层（本卡产出）完全复用，只改 `core/database.py` 与路由风格。

### 3.2 数据库 URL 方言映射

`backend/.env` 与 `conftest.py` 里的 `DATABASE_URL` 是 `postgresql://`（psycopg 直连用，`/health` 依赖它）。SQLAlchemy 会把它解释成 psycopg2（未安装）。

- **做法**：`core/database.py::to_sqlalchemy_url()` 把 `postgresql://` / `postgres://` 映射为 `postgresql+psycopg://`，其它 scheme 原样返回；`database_url` 本身**不改**，避免动 `.env`、`.env.example` 与既有健康检查。
- **验证**：纯函数单测（三种输入）。

### 3.3 迁移生成策略：autogenerate 起草 → 人工核对 → `alembic check` 归零

部分索引（`postgresql_where`）、GIN、`DESC` 表达式、CHECK 表达式都不是 autogenerate 的强项，直接信任生成器会漏约束。

1. 先写模型（约束/索引全部在模型里声明，单一来源）；
2. `alembic revision --autogenerate -m init` 起草；
3. **逐条对照 SCHEMA §2~§7 人工核对**并手工补/改（尤其部分索引谓词、CHECK 文本、`DESC`、GIN）；
4. `alembic check` 必须报 **"No new upgrade operations detected"**（模型与库零差异）；
5. `upgrade → downgrade base → upgrade` 往返成功。

> 已提交的迁移文件从此不可改（AGENTS §7）；以后改表 = 改模型 + 新迁移。

### 3.4 模型只做表映射，不写 relationship

避免懒加载语义、循环导入与"ORM 隐式 JOIN 猜错"；关联查询由 service 用 `select(...).join(...)` 显式写（S2-4 再定 `selectinload` 规范）。

## 4. 文件清单

**新增**

```
backend/app/models/base.py            DeclarativeBase + 命名约定 + CHECK 文本助手
backend/app/models/enums.py           §9 全部枚举（StrEnum）+ values() / enum_check()
backend/app/models/{users,certificates,key_records,invoice_files,invoices,
                    invoice_fields,ocr_lines,ocr_jobs,ticket_templates,
                    field_rules,report_forms,report_items,signatures,
                    envelopes,review_logs,audit_logs}.py   一表一文件（16）
backend/app/models/__init__.py        汇总导入（Alembic 靠它拿到完整 metadata）
backend/app/core/database.py          to_sqlalchemy_url + make_engine + 会话工厂（暂不接路由）
backend/alembic.ini                   脚本位置、文件模板、日志；URL 留空由 env.py 注入
backend/alembic/env.py                offline/online；URL 优先取 main option，其次 Settings
backend/alembic/script.py.mako        迁移模板
backend/alembic/versions/0001_init.py 首个迁移（16 表 + 全部约束/索引）
backend/tests/test_models.py          元数据单测（不需要数据库）
backend/tests/test_database.py        URL 映射单测
backend/tests/test_migration.py       集成测试（临时库：往返、CHECK、部分唯一索引实际拦截）
docs/spec/s2-1-db-models.md           本文件
docs/plan/s2-1-db-models.md           实施计划
```

**修改**

```
backend/requirements.txt              新增 SQLAlchemy==2.0.58、alembic==1.20.1（S2-1 引入）
backend/requirements.lock.txt         pip freeze 重新生成后核对 diff（只应多出 ORM 相关）
backend/pyproject.toml                addopts 默认排除 integration；`-m integration` 单独跑
docs/PROGRESS.md / docs/DECISIONS.md / docs/HANDOFF.md / AGENTS.md §10.8 锚点表
```

## 5. 约束与索引落地要点（实现时逐条对照）

1. **部分唯一索引（5 条 `WHERE`）**：`uq_invoices_code_no`（`status <> 'void'`）、`uq_signatures_form_active`（`status = 'active'`）、`uq_ticket_templates_active_type`（`active`）、`uq_envelopes_file`（`file_id IS NOT NULL`）、`uq_envelopes_form`（`form_id IS NOT NULL`）。**必须真的建，并各配一条"重复插入被拦"的集成测试**（D-002 的验证口径）。
2. **普通唯一约束**：`uq_users_username`、`uq_invoice_files_sha256`、`uq_invoice_files_storage_key`、`uq_invoices_file_id`、`uq_certificates_fingerprint`、`uq_certificates_issuer_serial`、`uq_key_records_storage_path`、`uq_ticket_templates_type_version`、`uq_signatures_form_version`、`uq_report_forms_report_no`、`uq_report_forms_idempotency_key`、`uq_invoice_fields_invoice_field`、`uq_ocr_lines_invoice_page_line`、`uq_report_items_form_invoice`、`uq_audit_logs_entry_hash`。
3. **CHECK**：SCHEMA §7.4 全表 + §9 每列枚举；另加一致性 CHECK：`signatures (verified = (verify_result = 'ok'))`、`certificates ((owner_type='server' AND owner_id IS NULL) OR (owner_type='user' AND owner_id IS NOT NULL))`、`key_records (user_id IS NOT NULL OR key_type='envelope')`、`envelopes (wrapped_key_b64 <> '')`、`envelopes (ciphertext_b64 IS NOT NULL OR ciphertext_storage_key IS NOT NULL)`、`invoice_files.sha256` 与 `envelopes.payload_sha256` 的 64 位小写十六进制正则（§0.3 全局约定）。
4. **JSONB**：`field_specs_json` 为 `NOT NULL`；其余 JSONB 可空；`canonical_json` 是 `TEXT`。
5. **`DESC` 索引**：`ix_invoices_uploader_created (uploader_id, created_at DESC)`、`ix_report_forms_applicant_created (applicant_id, created_at DESC)`、`ix_audit_logs_created (created_at DESC)`；迁移里用 `sa.text('created_at DESC')` 表达，靠 `alembic check` 验证与模型一致。
6. **GIN**：`ix_templates_specs USING GIN (field_specs_json)`。
7. **自引用外键**：`invoices.duplicate_of → invoices.id`（`fk_invoices_duplicate_of`，`NO ACTION`）。
8. **`ocr_jobs` 中途提交豁免**（SCHEMA §3.5）：本卡只建表，提交语义在 S2-7 实现，迁移不做任何特殊处理。

## 6. 与任务卡提示词的差异（先报告，不自行改契约）

任务卡提示词写"JSONB 用 `postgresql.JSONB` 且 `server_default=text("'{}'::jsonb")`"。
**SCHEMA 的字段表对 JSONB 列没有定义默认值（默认值列均为 `—`）**，因此本卡**不设 server_default**：

- 可空 JSONB 列（`bbox_json` / `box_json` / `params_json` / `detail_json`）缺省就是 `NULL`，补 `'{}'` 反而让"没有值"与"空对象"混淆；
- `NOT NULL` 的 `field_specs_json` 插入时必须显式给值，这是契约要求的语义（模板规格不能为空）；
- 任务卡该提示的合理内核是"默认值必须写 server_default，不能在 Python 侧"，本卡不产生任何 JSONB 默认值，因此没有该风险。

若你希望 JSONB 列带 `'{}'::jsonb` 默认值，**先改 `SCHEMA.md`**，我再补迁移（本卡不擅自加）。

## 7. 测试设计

**单元（不需要数据库，默认 `pytest` 就跑）**

- `test_models.py`
  - 恰好 16 张表，表名与 SCHEMA §0.4 完全一致，无多余表；
  - 每表 PK 为 `BigInteger + Identity(always=True)`；无 `created_by`/`tenant_id`/`deleted_at` 等契约外列；
  - 枚举列 CHECK 文本的取值集合与 `enums.py` 完全一致（不是"包含"，是相等）；
  - 5 条部分唯一索引的 `postgresql_where` 谓词逐字断言；
  - `uq_invoice_files_sha256`、`uq_signatures_form_version` 等普通唯一约束存在；
  - `canonical_json` 类型为 `TEXT`；`invoice_date` 为 `DATE`；时间列全部 `timezone=True`；金额/置信度 precision/scale；
  - 关键 `server_default` 断言（`now()`、`false`、`true`、`0`、`1`、`100`、`'draft'`…）；
  - 外键目标表与 `ondelete` 行为。
- `test_database.py`
  - `postgresql://` / `postgres://` → `postgresql+psycopg://`；已带方言的原样返回。

**集成（需要 Docker PG，`pytest -m integration` 单独跑）**

- 临时库 `study_ocr_mig_<8位hex>`（建库 / 删库，不污染 `studyocr`），跑 `alembic upgrade head`；
- `\dt` 口径：16 张业务表 + `alembic_version`；
- **往返**：`downgrade base` → 业务表为 0 → `upgrade head` 再次成功；
- `alembic check` 零差异；
- 从 `pg_constraint` 读回枚举 CHECK，断言取值与 `enums.py` 相等（防止迁移手抄漂移）；
- 行为用例（每类一条）：重复发票号 vs `void` 后可重录、重复 `sha256`、同单两条 `active` 签名、同票种两个 `active` 模板、同 `file_id` 两个信封、`(invoice_id, field_name)` / `(invoice_id, page_no, line_no)` / `(form_id, invoice_id)` 重复、`verified=false + verify_result='ok'`、非法枚举、空 `wrapped_key_b64`、两个密文字段都为空、负数金额、`canonical_json` 原样存取、`Decimal` 往返、identity 自增、外键不存在。

## 8. 验收（可复现命令与预期）

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m alembic upgrade head      # 成功
../.venv/Scripts/python.exe -m alembic downgrade base    # 成功，16 表消失
../.venv/Scripts/python.exe -m alembic upgrade head      # 成功
../.venv/Scripts/python.exe -m alembic check             # No new upgrade operations detected
../.venv/Scripts/python.exe -m pytest                    # 全绿（含既有 164 条 + 新增单元）
../.venv/Scripts/python.exe -m pytest -m integration -q  # 集成全绿（需 Docker）
cd .. && .venv/Scripts/python.exe scripts/smoke.py       # "数据库迁移" 项 OK，无 FAIL
docker exec study-ocr-postgres psql -U studyocr -d studyocr -c '\dt'   # 16 业务表 + alembic_version
```

## 9. 风险与回滚

| 风险 | 处理 |
|---|---|
| autogenerate 漏部分索引 / GIN / DESC / CHECK | §3.3 的人工核对 + `alembic check` 归零；集成测试从 `pg_indexes`/`pg_constraint` 读回断言 |
| `Identity(always=True)` 拒绝显式 id | 测试与后续代码都不传 id；迁移不需要改 |
| SQLAlchemy/Alambic 安装顶掉现有依赖 | 装完立即 `pip freeze > requirements.lock.txt` 并 `git diff` 核对，只允许新增 ORM 相关行 |
| 临时库残留 | 测试 `try/finally` 删库；删不掉只告警不阻塞（下次同名不存在） |
| 已提交迁移被误改 | 迁移提交后只追加新文件；本卡只产生 `0001_init.py` |

**回滚**：S2-1 阶段库内无业务数据，`alembic downgrade base` 即可完全回退；进入 S2-5 后禁止 `downgrade base`（会删业务表），改为只向前迁移。

## 10. 收口清单

- `PROGRESS.md`：勾选 S2-1，记录实测输出（迁移往返 / 约束用例数 / pytest 数）；
- `DECISIONS.md`：D-036（同步 SQLAlchemy + psycopg3 方言映射）、D-037（迁移生成与 `alembic check` 门槛 + JSONB 不设默认值）；
- `AGENTS.md` §10.8 回填"数据模型与迁移"锚点（`models/base.py`、`enums.py`、`alembic/env.py`、`0001_init.py`）；
- `HANDOFF.md`：现状改为 S2-1 完成、下一步 S2-2；
- 提交：`feat(s2-1): 16 张表模型与首个 Alembic 迁移（往返 + 约束测试）`；**push 由用户执行**。