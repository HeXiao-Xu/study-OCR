# S2-1 实现计划（plan）

> 对应 spec：`docs/spec/s2-1-db-models.md` ｜ 契约：`docs/SCHEMA.md` ｜ 状态：**已实现（2026-11-19）**，计划与实现同步落盘
> 约定：所有命令在 `E:\BiSheXZY\study-OCR` 下执行；Python 一律 `.venv/Scripts/python.exe`（3.11）。

## Step 0 — 依赖：SQLAlchemy 2.0 + Alembic

```bash
.venv/Scripts/python.exe -m pip install "SQLAlchemy==2.0.58" "alembic==1.20.1"
```

- `backend/requirements.txt` 新段：`SQLAlchemy==2.0.58`、`alembic==1.20.1`（写死版本，AGENTS §2.2）；
- `pip freeze > backend/requirements.lock.txt` 后 `git diff` 核对：**只允许新增 SQLAlchemy / alembic / Mako / MarkupSafe 等 ORM 相关行**，任何既有版本变化都要停下报告；
- 不装 `psycopg2`（驱动用已装的 `psycopg==3.3.9`）。

**验证**：`python -c "import sqlalchemy, alembic; print(sqlalchemy.__version__, alembic.__version__)"` → `2.0.58 1.20.1`；`git diff --stat backend/requirements*.txt` 只多这几行。

## Step 1 — 模型底座：`base.py` + `enums.py`

- `base.py`：`DeclarativeBase` + `MetaData(naming_convention={pk/ix/uq/fk})`；三个 CHECK 文本助手
  `enum_check(col, enum_cls)`、`hex64_check(col)`、`probability_check(col)`（0–1，NULL 放行）；
- `enums.py`：SCHEMA §9 全部枚举（`StrEnum`）：`UserRole`、`UserStatus`、`CertOwnerType`、`KeyAlgo`、
  `KeyType`、`OcrStatus`、`ExtractStatus`、`InvoiceStatus`、`QualityFlag`、`TicketType`、`FieldSource`、
  `OcrStage`、`FieldValidator`、`ReportStatus`、`DigestAlgo`、`SignatureAlgo`、`SignatureStatus`、
  `VerifyResult`、`EnvelopeKeyAlgo`、`CipherAlgo`、`ReviewDecision`、`AuditActorRole`、`AuditAction`、
  `AuditTargetType`；`values()` 返回有序取值，`enum_check()` 用它拼 `IN (...)`。

**验证**：`pytest -q backend/tests/test_models.py -k enums`（先写枚举断言，再进 Step 2）。

## Step 2 — 16 张表模型（一表一文件）

按 SCHEMA §0.4 顺序落：`users` → `certificates` → `key_records` → `invoice_files` → `ticket_templates` →
`field_rules` → `invoices` → `invoice_fields` → `ocr_jobs` → `ocr_lines` → `report_forms` → `report_items` →
`signatures` → `envelopes` → `review_logs` → `audit_logs`。

- 列：`Mapped[...]` + `mapped_column(...)`，显式 `Text` / `BigInteger` / `Numeric` / `DateTime(timezone=True)` /
  `Date` / `Boolean` / `postgresql.JSONB` / `postgresql.ARRAY(Text)`；可空性逐列对照 SCHEMA 字段表；
- 主键：`mapped_column(BigInteger, Identity(always=True), primary_key=True)`；
- 约束：`__table_args__` 里放 `UniqueConstraint` / `CheckConstraint` / `Index`（含 5 条部分唯一索引、
  GIN、DESC 表达式索引），名字全部显式（`uq_` / `ck_` / `ix_` / `fk_`）；
- 不写 `relationship`（spec §3.4）；`models/__init__.py` 汇总导入 16 个模块。

**验证**：`test_models.py` 全绿（表数、列、类型、枚举 CHECK、部分索引谓词、唯一约束、默认值、外键）。

## Step 3 — `core/database.py`

- `to_sqlalchemy_url(raw)`：`postgresql://` / `postgres://` → `postgresql+psycopg://`，其余原样；
- `make_engine(raw_url=None, **kwargs)`：`pool_pre_ping=True`，不在此处连库；
- `get_engine()`（`lru_cache`）+ `get_session_factory()` + `session_scope()`（`try/finally` 关闭）；
- **不接任何路由**（S2-2 起用）。

**验证**：`pytest -q backend/tests/test_database.py`。

## Step 4 — Alembic 骨架

- `backend/alembic.ini`：`script_location = alembic`、`prepend_sys_path = .`、
  `file_template = %%(rev)s_%%(slug)s`、`sqlalchemy.url =`（留空，防凭据入库）；标准 logging 段；
- `backend/alembic/env.py`：
  - 把 `backend/` 插入 `sys.path`（不依赖调用时的 cwd）；
  - `target_metadata = Base.metadata`（import `app.models` 触发全量注册）；
  - URL：`config.get_main_option("sqlalchemy.url")` 非空则用（集成测试注入临时库），否则
    `to_sqlalchemy_url(get_settings().database_url)`；
  - offline / online 两个分支；`compare_type=True`；**不打印 URL**；
- `backend/alembic/script.py.mako`：标准模板。

**验证**：`cd backend && python -m alembic current`（空库 → 无版本，不报错）。

## Step 5 — 起草并核对 `0001_init.py`

```bash
cd backend
../.venv/Scripts/python.exe -m alembic revision --autogenerate -m init   # 生成 0001_init.py
```

人工核对（逐条对照 SCHEMA §2~§7，spec §5 的清单）：

1. 16 张表 + 列/类型/可空/默认全对；
2. 5 条部分唯一索引的 `postgresql_where` 谓词逐字正确（`<> 'void'` / `= 'active'` / `active` / `IS NOT NULL`）；
3. 所有 CHECK：枚举列、金额非负、置信度 0–1、64 位十六进制、`verified = (verify_result='ok')`、
   `owner_type/owner_id` 配对、`key_records`、`envelopes` 两条一致性与两条非空；
4. `ix_*_created` 的 `DESC`、`ix_templates_specs` 的 `GIN`；
5. `canonical_json` 是 `sa.Text()`（**不是 JSONB**）；`invoice_date` 是 `sa.Date()`；
6. `downgrade()` 逆序 `drop_table`，无遗漏。

**验证**：`alembic upgrade head` 成功 → `alembic check` 输出 `No new upgrade operations detected`。

## Step 6 — 测试

- `test_models.py`（单元，spec §7 第一组）；
- `test_database.py`（单元）；
- `test_migration.py`（集成，`pytest.mark.integration`）：临时库建/删、往返、`alembic check`、
  枚举 CHECK 回读比对、以及 spec §7 第二组的每类拦截用例（重复发票 vs void、重复 sha256、
  两条 active 签名、两个 active 模板、同 file_id 信封、三组复合唯一、`verified/verify_result`、
  非法枚举、空 wrapped_key、密文双空、负金额、`canonical_json` 原样、Decimal、identity、外键）。
- `pyproject.toml`：`addopts = "-q -m 'not integration'"`，集成测试显式 `-m integration` 跑
  （与现有 markers 注释的"默认跳过"一致；实施时确认 CLI `-m` 能覆盖 addopts）。

**验证**：`pytest` 全绿；`pytest -m integration -q` 全绿（Docker 三件套在跑）。

## Step 7 — 全量验收与收口

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m alembic upgrade head
../.venv/Scripts/python.exe -m alembic downgrade base
../.venv/Scripts/python.exe -m alembic upgrade head
../.venv/Scripts/python.exe -m alembic check
../.venv/Scripts/python.exe -m pytest
../.venv/Scripts/python.exe -m pytest -m integration -q
cd .. && .venv/Scripts/python.exe scripts/smoke.py
docker exec study-ocr-postgres psql -U studyocr -d studyocr -c '\dt'
```

通过标准：往返成功、`alembic check` 零差异、pytest 全绿、smoke 无 FAIL、`\dt` = 16 业务表 + `alembic_version`。

文档与提交：

- `PROGRESS.md` 勾 S2-1 + 实测数字；
- `DECISIONS.md` 追加 D-036（同步 SQLAlchemy + psycopg3 映射）、D-037（迁移策略 + `alembic check` + JSONB 默认值口径）；
- `AGENTS.md` §10.8 回填"数据模型与迁移"锚点；
- `HANDOFF.md` 更新现状与下一步（S2-2）；
- 提交 `feat(s2-1): ...`（不 push）。

## 回滚与降级

| 情况 | 处理 |
|---|---|
| 迁移往返失败 | 先 `alembic downgrade base` 清空，修 `0001_init.py` 后重跑；库为空无数据损失 |
| `alembic check` 有差异 | 差异就是"模型与迁移不一致"，按差异改迁移（模型是唯一来源），不要反向改模型迁就迁移 |
| 约束用例拦不住 | 先看是否写错谓词（`status <> 'void'` vs `!=`），再看测试是否在事务内被回滚吞掉 |
| 装依赖顶掉 numpy/paddle | 立刻 `pip install -r backend/requirements.lock.txt` 回滚，并在 `DECISIONS.md` 记冲突原因 |