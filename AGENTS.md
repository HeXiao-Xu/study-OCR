# AGENTS.md — 项目开发约束（AI 助手必读）

> 本文件是项目的"宪法"。任何 AI 助手在动手前必须先读它；与任务卡冲突时以本文件的硬约束为准，
> 与 `docs/SCHEMA.md` / `docs/API.md` 冲突时以契约文档为准（见 §9）。

## 1. 项目一句话

基于 OCR 的高校报销票据识别与加密系统：票据影像 → 结构化字段（带坐标与置信度）→ 人工确认 →
SM3 摘要 + SM2 签名 + SM4 数字信封 → 财务端验签解密 → 审核留痕。

论文有两条主线，代码结构必须能体现它们：**识别链路**（预处理 / 检测识别 / 字段抽取校验）与**密码链路**（签名 / 信封 / 密钥证书 / 审计链）。

## 2. 技术栈（不要擅自更换）

- 后端：Python **3.11** + FastAPI + SQLAlchemy 2.0 + Alembic + Pydantic v2 + Uvicorn
- 前端：Vue 3 + TypeScript + Vite + Pinia + Element Plus
- 数据：PostgreSQL 16、Redis 7（缓存 + 队列）、MinIO（票据影像密文）
- 异步：ARQ（队列）+ APScheduler（定时：审计链锚定、临时文件清理）
- OCR：**paddleocr==2.7.3**（PP-OCRv4：DBNet 检测 + SVTR_LCNet 识别，CPU 推理）、
  opencv-python、numpy、pillow；**禁止升级到 3.x**（`ocr()` → `predict()` 返回结构不兼容）
- 密码：**gmssl==3.2.2**（PyPI 的纯 Python 版，SM2/SM3/SM4）、cryptography（AES-GCM / RSA-OAEP / RSA-PSS /
  X.509 构造 / PBKDF2 / PKCS#8 加密私钥）、OpenSSL 3.x 命令行（生成 SM2 密钥对与证书）
- 认证：FastAPI 官方安全示例或 fastapi-users；分页：fastapi-pagination；口令哈希：Argon2id（passlib）
- 测试：pytest + pytest-asyncio + httpx
- 部署：Docker Compose + Nginx

### 2.1 依赖与复用原则

- 新需求先找成熟库/API；**只有下面 8 块业务逻辑自己实现**（论文工作量所在）：
  预处理兜底链、版面分块与票种判定、字段锚定打分与语义兜底、校验规则库与冲突消解、
  置信度聚合与二次识别触发、签名域规范化、信封封装格式与 Encrypt-then-MAC、审计哈希链与评测脚本。
- 擅自引入新依赖前必须说明理由并等确认。
- 任何"手写通用能力"（图像算子、椭圆曲线运算、ASN.1/PEM 编解码、JWT、分页、定时）都要先确认有没有现成库。
- 绝不自研：卷积网络、密码算法本体、证书解析、PDF 渲染。

### 2.2 版本锁定（本课题专属，违反必炸）

- `paddleocr==2.7.3` + `paddlepaddle==2.6.*`：3.x 与 2.7 的 API 不兼容，升级等于重写识别层。
- `gmssl` 与 `gmssl-python` 是**两个不同的包**：本项目用前者（纯 Python，pip 即装）。
  `gmssl-python`（官方 ctypes 封装，需先编译 libgmssl）只作可选升级路线，不进主线。
- `requirements.txt` 必须写死版本号；改任何版本前先跑全量测试，并在 `docs/DECISIONS.md` 记一条。
- Python 必须是 3.11：`paddlepaddle` 无 3.13/3.14 wheel。Windows 用 `py -3.11 -m venv .venv`。

## 3. 目录约定

```
backend/app/{api/v1,models,schemas,services,ocr,crypto,core,tasks,utils}
backend/app/ocr/{preprocess,detector,recognizer,layout,extractor,validators}.py + templates/*.yaml
backend/app/crypto/{canonical,signature,envelope,keystore,certs}.py + sm/{sm2,sm3,sm4}.py
backend/alembic/versions/          backend/tests/          backend/eval/{datasets,configs,results}
web/src/{api,components,views,stores,router,utils,types}
docs/{SCHEMA.md,API.md,PROGRESS.md,DECISIONS.md,EVAL.md}   docs/{spec,plan}/
deploy/   scripts/   samples/（图片不入库）   secrets/（整目录不入库）
```

- 脚本一律放**根目录 `scripts/`**（合成票据、基线跑批、冷启动检查），不在 `backend/` 下再建 `scripts/`。
- 评测代码放 `backend/eval/`，结果写 `backend/eval/results/`（按时间戳命名，不入库）。

## 4. 编码规范

### 4.1 通用

- 分层：路由只做参数校验与调用；业务逻辑在 services；OCR 只在 `ocr/`；密码只在 `crypto/`。
- 命名：数据库与 API 用 `snake_case`；前端变量用 `camelCase`；常量全大写。
- 类型：所有函数签名必须有类型标注；Pydantic 模型用于所有出入参，路由与 service 之间禁止传裸 dict。
- 调用链：路由 → service → （`ocr/` 或 `crypto/` 封装）→ 数据层。**禁止跨层直连**。
- 错误：不吞异常，不用 `except: pass`；异常带错误码与 `trace_id`，但**不带敏感数据**。
- 注释：只解释"为什么"，不复述"做了什么"；密码相关必须写明协议意图（例如"先验 MAC 再解密，防 padding oracle"）。

### 4.2 数据库与枚举

- 表结构以 `docs/SCHEMA.md` 为唯一依据；**枚举一律 `TEXT + CHECK`**，不用 PostgreSQL ENUM（便于加值不锁表）；
  枚举常量集中放 `app/models/enums.py`。
- 主键 `BIGINT GENERATED ALWAYS AS IDENTITY`；时间 `TIMESTAMPTZ`；半结构化 `JSONB`。
- **金额一律 `NUMERIC(12,2)` + Python `Decimal`，禁止 float**（0.01 容差判断用 float 会误判）。
- 索引与约束命名：`uq_` / `ck_` / `ix_` / `fk_` 前缀。
- 部分唯一索引必须真的建（`(invoice_code, invoice_no) WHERE status <> 'void'`、
  `signatures (form_id) WHERE status = 'active'`）——业务不变式靠库兜底，不靠应用层自觉。

### 4.3 时间与边界

- 数据库存 UTC，接口输出 ISO-8601（带时区）；需要"业务日"时按 `Asia/Shanghai` 换算，且只在一个地方换算。
- 文件与影像：**明文只存在于内存**；落盘/落对象存储一律密文；临时文件用 `try/finally` 清理。

## 5. OCR 实现约束（高压线）

- **EXIF 方向修正必须先做**（`ImageOps.exif_transpose`），否则整条链路全错。
- 预处理每一步都要能单独开关（为消融实验）；**不做无收益的强预处理**（全局二值化、inpaint 去印章），
  除非消融实验证明有正收益。印章只**标记**不修复。
- 透视校正失败必须降级而非抛异常：轮廓法 → `minAreaRect` 旋转校正 → 原图直送 + `quality_flag="low"`。
- 检测框 `det_db_unclip_ratio` 不得超过 1.8（票据文本密集会粘连导致识别串行）；推荐 1.5–1.7。
- **坐标与置信度必须一路保留到字段层**，禁止中途丢弃；bbox 格式写死为 `[x1,y1,x2,y2]`（原图坐标系），
  前端画框只认这一种格式。
- OCR 输出结构必须有契约测试（`backend/tests/test_ocr_contract.py`）；库升级后结构一变，测试先炸。
- PaddleOCR 实例是进程内单例；模型**非线程安全**，worker 并发 1–2。

## 6. 密码实现约束（违反即打回）

- **随机数只许用 `secrets`**。禁止 `random`，也禁止直接用 `gmssl.func.random_hex`（它基于 `random`，nonce 可预测）。
  SM2 加密的会话密钥、签名用的 `K`、CBC 的 IV 全部走 `secrets`。
- SM2 必须显式 `mode=1`（C1C3C2）。`CryptSM2` 默认 `mode=0`（C1C2C3），不许用默认值。
- 签名验签统一用 `sign_with_sm3` / `verify_with_sm3`；**禁止与 `sign(data, K)` 混用**（双重哈希 → 恒验不过）。
- 对称层：SM4-CBC + PKCS7 + 随机 IV + HMAC-SM3，**Encrypt-then-MAC**；MAC 覆盖
  `magic ‖ cipher_algo ‖ key_algo ‖ IV ‖ ciphertext ‖ recipient_cert_fp`；**解密前必须先验 MAC**，
  MAC 不过直接拒绝，禁止"先解密再校验"。
- **不依赖 SM2 自带的 C3 校验**（`gmssl` 的 `decrypt` 算了 C3 却不比对，密文被改也可能返回垃圾明文），
  完整性一律由 HMAC-SM3 与 `payload_sha256` 保证。
- GM/T 国标排列：`gmssl` 输出/接受的 `C1` **不带 `0x04` 前缀**，跨实现互通时转换集中在 `crypto/sm/sm2.py` 一处。
- `sm3.sm3_hash(list)` **会原地修改入参**，业务代码禁止直接调用；一律用 `crypto/sm/sm3.py::sm3_digest(bytes)`。
- 私钥：口令 + PBKDF2-HMAC-SHA256（iters ≥ 200000，16B 盐）派生 KEK → AES-256-GCM 包裹 PKCS#8；
  **私钥不入库、不进日志、不进异常堆栈、不进接口响应**；`key_records` 只登记路径。
- 业务代码禁止直接 import `gmssl` / `cryptography`；只能调 `crypto/` 的封装函数。
- 每个密码封装必须有：**国家标准测试向量用例** + 往返用例 + 篡改失败用例（改 1 bit 必须失败）。

## 7. 禁止事项

- 禁止修改已提交的 Alembic 迁移文件。
- 禁止引入未经确认的新依赖；禁止擅自改动 `requirements.txt` 里锁定的版本。
- 禁止在任务范围外"顺手重构"其他模块。
- 禁止删除或跳过已有测试来让测试通过。
- 禁止生成假的数据、假的指标、假的抽查结果。
- 禁止把真实票据图片、姓名、身份证号、税号、银行账号写进代码、日志、示例数据、提交历史。
- 禁止在日志中打印明文金额、税号、身份证号（一律走 `utils/masking.py` 脱敏）。
- 禁止把 `secrets/`、`.env`、`samples/` 中的影像提交入库。

## 8. 验收习惯（每个任务必须做到）

1. 改完代码给出**可执行的验证命令**（pytest / curl / 界面路径 / 看哪张输出图），并贴实测输出。
2. 新增功能必须配测试；模型推理类可标 `@pytest.mark.slow`，但**密码测试必须默认跑**。
3. 更新 `docs/PROGRESS.md`；接口变化同步 `docs/API.md`；表变化同步 `docs/SCHEMA.md`。
4. 新增环境变量同步 `.env.example`；新增依赖同步 `requirements.txt` / `package.json`。
5. 生成符合 Conventional Commits 的提交信息（含任务号，如 `feat(s4-3): ...`）。**git push 由用户执行。**

## 9. 与其他文档的关系

- `docs/SCHEMA.md`：数据结构的唯一依据，冲突时以它为准。
- `docs/API.md`：接口契约的唯一依据，前后端都以它为准。
- `docs/PROGRESS.md`：当前进度与遗留问题的唯一依据。
- `docs/HANDOFF.md`：**会话交接入口**（环境、复现命令、指标、关键取舍、下一步）。开新会话先读它。
- `docs/EVAL.md`：评测口径与实验数字，**只读契约，AI 不得修改其中数字**。
- `docs/DECISIONS.md`：选型与取舍记录，论文"方案选型"章节的草稿。
- 方案见 `docs/OCR票据识别与加密系统-毕业设计完整方案.md`；
  任务卡见 `docs/OCR票据识别与加密系统-AI协作开发实施计划.md`（任务编号 S0-1 ~ S7-5 均出自该文件）。
- 发现文档与代码不一致：**先报告，不要自行决定改哪边**。

## 10. 功能编码细则（S1 起写正式功能代码时逐条自检）

### 10.1 动笔前

- 先读任务卡 + 对应契约（接口 → `API.md`，数据 → `SCHEMA.md`）；发现契约冲突停下报告（§9）。
- 契约先行：新接口先补 `API.md`、新表先补 `SCHEMA.md`，再写代码。
- 新代码先对"一致性锚点"（§10.8）找样，再动手；锚点为空的模块，第一个落地者负责回填锚点。
- 功能级任务走 §11 的 spec → plan 流程。

### 10.2 后端（FastAPI + Pydantic v2 + SQLAlchemy 2.0）

- 路由函数 ≤30 行：参数校验 → service 调用 → 统一响应壳组装；超了说明该挪 service。
- 一个 service 用例一个事务；失败回滚后抛带上下文的 `AppError` 子类，禁止返回 `None` 当失败。
- 配置只从 `core/config.py` 的 `Settings` 读；其它模块禁止直读 `os.environ`。
- 日志统一带 `trace_id`（contextvar + 中间件注入），贯穿 API → 任务 → OCR → 密码 → 审计。
- 列表接口统一用 `utils/pagination.py` 的分页/排序白名单；禁止手写 `limit/offset`、禁止字符串拼 `order_by`。
- 行级隔离：所有票据/报销单查询必须显式 `WHERE uploader_id = <当前用户>`（`reviewer` 读全量待审除外）；
  越权一律 `404`，不泄露资源是否存在。

### 10.3 OCR 层

- `ocr/` 对外只暴露函数 + Pydantic 模型；检测/识别必须做成**适配器**（`detector.py` / `recognizer.py` 定义接口，
  内部实现可替换），以便第 9 章换 CRNN 做对比实验。
- 抽取器（`extractor.py`）是"字段 → 规则"的唯一入口；规则数据先来自 `templates/*.yaml`，S2-8 起改读库并缓存。
- 校验器（`validators.py`）是纯函数：输入字段值，输出冲突列表；**禁止在抽取器里静默修正**冲突值。
- 置信度聚合（`services/confidence.py`）公式与阈值必须可配置，且写进 `docs/DECISIONS.md` 说明取定依据。

### 10.4 密码层

- 对外只暴露：`crypto/canonical.py::canonical_json`、`crypto/sm/sm3.py::sm3_digest/hmac_sm3`、
  `crypto/signature.py::sign_canonical/verify_canonical`、`crypto/envelope.py::seal/open_sealed`、
  `crypto/keystore.py`、`crypto/certs.py`。业务层只调这些。
- 签名对象永远是 `canonical_json(...)` 的字节；**禁止对 dict 直接签名**，禁止对格式化展示文本签名。
- `report_forms.canonical_json` 存签名当时的原文字串，验签时用它重算，不重新拼装。
- 信封拆封顺序写死在 `envelope.py` 一处：验 MAC → 解 CEK → 解密 → 证书校验 → 验签 → 摘要比对。
- 失败必须归类返回：`mac_mismatch` / `bad_padding` / `digest_mismatch` / `bad_signature` / `cert_invalid`。

### 10.5 异步任务

- 提交必须走 `task_service.create_task`（先落库再入队，入队失败置 `failed`）。
- 任务幂等：同一 `invoice_id` 不重复入库；重试用指数退避，最多 3 次。
- handler 内**不得自行 commit 业务表**（由 runner 统一提交/回滚）；需要中途可见的状态（如 `ocr_jobs` 阶段进度）
  例外，但必须在文档里写明理由。
- 单任务硬超时 180s（OCR 全链路），超时置 `failed` 并记录原因。

### 10.6 测试

- 命名：`tests/test_<模块>.py`，用例 `test_<被测函数>_<场景>_<预期>`。
- service 公共函数至少 2 条用例：1 条 happy path + 1 条失败路径（权限/不存在/非法输入择一）。
- 外部依赖（模型推理、MinIO、Redis）一律 mock 或注入假实现；测试套件禁止真实网络调用。
- 密码测试三类必备：国标测试向量、往返、篡改失败。
- `pytest` 全绿是提交前提；确需跳过必须写明原因并经用户确认。

### 10.7 前端（S0-6 起）

- API 调用只经 `src/api/` 封装；组件里禁止直接 `import axios`。
- 识别核对页要求：字段 hover → 图上 bbox 高亮；低置信标红；冲突字段给两个候选让用户选，**不自动替换**；
  坐标按 `scaleX/scaleY` 换算后用 canvas 绘制，不用 DOM 绝对定位堆上百个框。
- 必须处理加载中 / 空数据 / 请求失败三态；提交按钮防重复点击。
- 前端校验规则与后端 Pydantic 保持一致（从 `API.md` 抄，不各自发明）。

### 10.8 一致性锚点（每个正式模块落地后回填，防代码风格漂移）

> 已登记模块见下表。规则：第一个把某模块写成正式代码的任务，负责在此登记锚点文件，
> 后续同类代码先照锚点写；觉得锚点过时 → 先改锚点，再写新码。

| 模块 | 锚点文件 | 回填任务 |
|---|---|---|
| 后端骨架 | `backend/app/core/{config,logging,exceptions,health}.py` + `backend/app/schemas/common.py` + `backend/app/main.py`（`create_app()` 工厂、`X-Trace-Id` 中间件、`request.state.trace_id`、异常处理器；`/health` 双路径 + 503 降级；`Settings` 缺项启动即抛 `ConfigError`） | S0-5 ✅ |
| 数据模型与迁移 | `backend/app/models/base.py`（`DeclarativeBase` + 命名约定 + `hex64_check`/`probability_check`）+ `models/enums.py`（`StrEnum` 唯一来源，CHECK 文本由 `enum_check()` 拼接）+ 16 个一表一文件模型（`Identity(always=True)`、`TEXT + CHECK`、`NUMERIC(12,2)`、JSONB、部分唯一索引 `postgresql_where`、GIN、`DESC` 表达式索引；不写 relationship）+ `backend/alembic/{env.py,versions/0001_init.py}`（URL 由 Settings 注入不落盘；autogenerate 起草 → 人工对照 SCHEMA → `alembic check` 零差异为门槛）+ `core/database.py`（`postgresql://` → `postgresql+psycopg://` 方言映射）+ `tests/{test_models,test_migration}.py` | S2-1 ✅ |
| 认证与权限依赖 | `backend/app/core/security.py`（Argon2id 口令哈希 + HS256 JWT；`jti` 用 `secrets`；解码显式 `algorithms=["HS256"]` 并校验 `typ`，防 refresh 当 access 用）+ `backend/app/services/{auth_service,token_store}.py`（refresh **轮换** + Redis 白名单、登录失败统一文案 + dummy verify）+ `backend/app/api/deps.py`（`HTTPBearer(auto_error=False)`、`get_current_user` 按 `sub` 回表且**角色以库为准**、`require_roles` 角色级 **403** 与资源级 404 分离）+ `api/v1/auth.py` + `schemas/auth.py` + `scripts/create_user.py`（reviewer/admin 只能脚本建）+ `tests/{test_security,test_auth}.py` | S2-2 ✅ |
| 行级数据隔离 | `backend/app/services/repository.py`（策略表 `POLICIES` + `scope_for` / `filter_visible` / `select_visible` / `get_visible_or_404`；submitter 仅本人、reviewer 票据全量 + 报销单 `submitted` 之后、admin 业务表 404；**未登记模型 `false()` 默认拒绝**；越权对外 404、内部区分 `NotFoundError` / `PermissionDeniedError`）+ `tests/{test_repository,test_isolation}.py`（列表/详情一致、8 状态参数化、HTTP 404 语义） | S2-3 ✅ |
| 分页与筛选 | `backend/app/schemas/common.py`（`PageParams`/`PageResult`）+ `backend/app/services/pagination.py`（`apply_sort` 白名单 + `paginate`：同一 `Select` 求 total、`id asc` 稳定兜底、`scalar=False` 支持 join 附加列）+ `backend/app/utils/text.py::escape_like`（`q` 的 `%`/`_`/`\` 转义）+ `scripts/bench_list.py`（5 万行 p50/p95 + EXPLAIN ANALYZE）+ `tests/test_pagination.py`（翻页并集/超末页/count 同条件/2 条 SELECT 的 N+1 断言/HTTP 422 边界） | S2-4 ✅ |
| 票据 CRUD 与状态机 | `backend/app/services/invoice_service.py`（`create_draft` 内部工厂、`transition` 唯一状态机 **5 值**、列表/详情/软删、契约版 `update_fields`：乐观锁 + D-017 权限 + `confirm_amount` + 42201 回滚）+ `backend/app/api/v1/invoices.py` + `backend/app/schemas/invoice.py`（金额两位小数字符串、无 `storage_key`）+ `tests/test_invoices.py`（31 条：三权限/筛选/软删幂等/void 释放唯一索引/状态机/字段修改分支） | S2-5 ✅ |
| 文件上传与去重 | `backend/app/services/{storage,upload_service}.py`（分块 sha256；魔数 + Pillow/pypdf 校验；**去重先于写对象**；`storage.put` 后落库失败必补偿删除；对象名 `invoices/yyyy/mm/<32hex>.bin`，原文件名只展示）+ `backend/app/api/v1/files.py`（`POST /invoices/upload` 202）+ `schemas/invoice.py::UploadData`（无 `storage_key`）+ `core/config.py`（`storage_allow_plaintext` 仅本地，prod 启动拒绝）+ `tests/test_upload.py`（11 条） | S2-6 ✅ |
| 异步任务框架 | `backend/app/services/job_service.py`（作业状态机 `pending→running→succeeded|failed`；终态跳过 + Redis `SET NX EX 600` 幂等；可重试错误回 `pending` + 退避 5/30；`retry_count`=已尝试次数；30min `running` 恢复为 failed）+ `backend/app/tasks/{worker,ocr_task,queue}.py`（`WorkerSettings` 与 API 共用 Settings；`ocr_task` 在 S3-1 前如实失败；入队协议可注入假实现）+ `tests/{test_jobs,test_upload}.py` | S2-7 ✅ |
| 模板与规则管理 | `backend/app/services/template_service.py`（CRUD/升版本：PUT 生成 v+1 新行、旧行保留、规则复制+按 field 覆盖；同票种 active 互斥；`/test` 复用 extractor 且 OCR 可注入）+ `services/{template_registry,template_seed}.py`（60s 缓存 + 写后失效；YAML seed 构建）+ `api/v1/templates.py` + `schemas/template.py`（正则 compile/长度、strategy 白名单）+ `scripts/seed_templates.py` + `tests/test_templates.py` | S2-8 ✅ |
| OCR 预处理与输入分流 | `backend/app/ocr/preprocess.py`（`PreprocessOptions` 全开关；EXIF→缩放→定位→透视/降级→放大→去斜→照度→印章；返回 `to_original` 3×3；`needs_retake`+`retake_hints`）+ `backend/app/ocr/intake.py`（**PDF 只渲染不预处理**；照片走预处理 + 拍前引导 + 重拍闸门；按魔数判形态）+ `scripts/{preprocess_demo,preprocess_eval}.py` | S1-2 ✅ |
| OCR 检测识别适配器 | `backend/app/ocr/{detector,recognizer,types}.py`（`OcrEngine` 单例 + 懒加载 + 互斥锁；`parse_paddle_result` 解析 2.7 结构并**按 y 分桶再按 x 排序**给 `line_no`；遇到 3.x dict 抛 `OcrContractError`；`recognize_line` 供 S3-3 复用）+ `scripts/ocr_demo.py` | S1-3 ✅ |
| 票种识别与版面分块 | `backend/app/ocr/{layout.py,templates/*.yaml}`（关键字加权 ×3 标题加成 + 互斥词扣分 + 长宽比先验；`unknown` 降级；区域**按内容框归一化**；block 聚类）+ `scripts/{layout_demo,layout_eval}.py` | S1-4 ✅ |
| 字段抽取器与校验器 | `backend/app/ocr/{extractor.py,validators.py}` + `templates/field_rules.yaml`（锚点优先级 + 同行/邻近取值 + 忽略 OCR 空格 + exclude 表头陷阱 + whole_line 无标签名称行；冲突保留 candidates 不静默替换）+ `scripts/{field_demo,field_eval}.py` | S1-5 ✅ |
| 密码封装（canonical / sm / signature / envelope / keystore） | `backend/app/crypto/canonical.py`（`canonical_json`：键按**Unicode 码点**升序 + `(",", ":")` + `ensure_ascii=False`/UTF-8；校验遍历先拒 float/Decimal/非 str 键/`|int|>2^53-1`/循环/孤立代理项，转义细节交给 `json.dumps`；`canonical_sha256` 仅作对照；`CanonicalJSONError(ValueError)`，消息只带类型与路径、**不回显值**）+ `app/crypto/sm/sm3.py`（`sm3_digest` 恒 32B 的唯一入口、`sm3_hex` 仅日志；`gmssl.sm3.sm3_hash` **会原地改入参**且返回 hex → 只传 `list(bytes)` 副本再 `bytes.fromhex`；业务代码禁止 import gmssl）+ `backend/tests/test_crypto/{test_canonical,test_sm3}.py`（54 条：SM3 国标向量/键序不同同摘要/中文不转义/`100` vs `"100.00"`/float 与 Decimal 被拒/入参不被改/循环拒）+ `scripts/canonical_cross_check.{py,mjs}`（Node 侧显式**按码点**排序，三字段逐字对照） | S4-1 ✅（sm2/signature/envelope/keystore 待 S4-2 ~ S4-6） |
| 审计哈希链 | 待回填 | S4-7 |
| OCR 流水线（S3-1） | `backend/app/services/ocr_pipeline.py`（`run_pipeline` + `PipelineDeps` 注入 engine/storage/prepare/clock；`metrics` 结构 `version/stages/timings_ms/counts/warnings`；抽取结果 upsert，人工/已确认字段不覆盖；`(invoice_code,invoice_no)` 冲突用 savepoint 回退且作业不失败）+ `services/ocr_status_service.py`（`STAGE_WEIGHTS` 10/30/40/20、`build_stages/compute_progress`）+ `services/job_service.py`（`started_at/finished_at` + 终态 `(status,retry_count)` 指纹守卫 → `superseded`）+ `ocr/intake.py::prepare_pages` + `services/storage.py::get_bytes` + `ocr/extractor.py::rules_from_specs` + `alembic/versions/0004_ocr_jobs_metrics_and_times.py` | S3-1 ✅ |
| 置信度聚合（S3-2） | `backend/app/services/confidence.py`（`compute_field_confidence` = 加权 OCR 分 × agreement × rule_factor；`apply_validator_cap` 封顶 0.45；`level_for` 0.90/0.60 三档；`resolve_field` 冲突降级；`aggregate_invoice` 均值只展示）+ `ocr/extractor.py` 记录 `anchor_score/value_score/agreement`（证据与判定分离）+ `ocr_pipeline` 写 `need_review/overall_confidence/extract_status` + `metrics.counts.review_levels`+ `invoice_service._field_out` 派生 `review_level` + `scripts/confidence_eval.py` | S3-2 ✅ |
| 局部重识别（S3-3） | `backend/app/ocr/refine.py`（`expand_bbox` = max(8px,10%行高) + clamp；`crop_region`/`upscale`(2×,长边1024)/`reread_box`；`bbox_to_original`/`bbox_from_original` 坐标映射）+ `services/reocr_service.py`（`submit_reocr` 校验 + `run_reocr`：只改 raw_text/confidence/source=ocr_retry，`field_value` 不动；1.5s 预算与 skipped 语义）+ `reocr_task`/`enqueue_reocr` + `0005`（`field_reread` 枚举，不写审计行）+ `ocr_pipeline` 字段 bbox 原图坐标 | S3-3 ✅ |
| 识别产物契约（S3-4） | `backend/app/schemas/ocr.py`（唯一来源：`OcrLine.box` 四点/校正图、`ExtractedField.bbox` 外接框/原图、`raw_value` 票面原文、`FieldError`）+ `app/ocr/types.py`（re-export，禁止再定义）+ `services/ocr_pipeline.py`（`field_value=raw_value or value`、`normalized_value=value`）+ `GET /invoices/{id}/fields` + `scripts/export_ocr_schema.py` + `docs/schemas/ocr_result.schema.json` + `web/src/types/ocr.ts` + `tests/test_ocr_schema.py`（三项防漂移） | S3-4 ✅ |
| 详情页影像画框（S3-5） | `backend/app/services/{image_service,line_service}.py`（`render_image`：照片原字节/PDF **250DPI** PNG/preprocessed，明文仅 `STORAGE_ALLOW_PLAINTEXT`；`get_invoice_lines`：ocr_lines 四点→**原图**外接框、**不返回文本**）+ `api/v1/invoices.py`（`GET /{id}/image`、`GET /{id}/lines`）+ `web/src/utils/bbox.ts`（唯一换算/裁剪/命中）+ `components/{ImageCanvas,FieldTable}.vue`（img+canvas 叠层、CSS 尺寸缩放、culling、`data-*` 调试属性）+ `ui_smoke --eval` 数值断言 | S3-5 ✅ |
| 字段修正与整单确认（S3-6） | `backend/app/services/rule_source.py`（`resolve_rules`：DB 模板优先/YAML 回退，流水线与确认同源）+ `invoice_service.collect_confirm_errors`（闸门顺序 必填→冲突→低置信未确认→validators，同字段只留第一条）+ `confirm_invoice`（本人权限/幂等/version+1/`status+extract_status` 同事务）+ `POST /invoices/{id}/confirm`（失败 422/42201 + `data.fields`）+ 详情页"确认整单"/逐字段"确认" + `tests/test_confirm.py` | S3-6 ✅ |
| 重复报销检测（S3-7） | `backend/app/services/duplicate.py`（`check_duplicates`：强=duplicate_of/同票号/同影像，弱=同金额+日期+销方且票号不同；只给最小字段、只读）+ `ocr_pipeline` 唯一冲突回退写 `duplicate_of` + `invoice_service.confirm_invoice` 强重复 409（字段闸门之前）+ `GET /invoices/{id}/duplicate-check` + 详情页重复横幅（strong 红/weak 黄）+ `tests/test_duplicate.py` | S3-7 ✅ |
| 批量上传与进度（S3-8） | `backend/app/services/batch_service.py`（`create_batch` 复用单文件上传；`dispatch_pending` 单用户并发 ≤3；`retry_item` 只建新 job 不建新单）+ 迁移 0006（`invoices.batch_id`、`ocr_jobs.dispatched`）+ `tasks/worker.py::after_job_end`（ARQ 0.28 只传 ctx，`Job.info()` 回查参数）+ 3 个 batch 接口 + `web/src/views/Upload.vue` 批量模式（总进度/逐项状态/重试）+ `tests/test_batch.py` | S3-8 ✅ |
| 双人复核（S3-9） | `backend/app/services/invoice_service.py`（`update_fields`：提交人可改全部字段、金额勾选对所有角色、复核锁定；`review_invoice`：reviewer 二审、幂等、闸门复用 `collect_confirm_errors`）+ 迁移 0007（`invoices.reviewed_by/reviewed_at`，不加 status 枚举）+ `POST /invoices/{id}/review`+ 列表 `review_status` + 详情"复核通过"/"待复核/已复核" + `tests/test_review.py` | S3-9 ✅ |
| 评测脚本与指标函数 | `backend/eval/metrics.py`（纯函数指标：覆盖匹配检测 P/R/Hmean、字符准确率、字段级、整单、待确认率、分位数）+ `backend/eval/configs/baseline.yaml` + `scripts/ocr_baseline.py`（逐张跑全链路 → JSON + Markdown 报告，支持预处理开关对照） | S1-6 ✅ |
| 合成票据生成器 | `scripts/{invoice_layout,invoice_templates,perturb,sample_data,sample_fonts,make_samples}.py`（绘制即记录坐标 → 几何扰动同步变换 bbox → 光度/叠加 → JSONL 标注；`check_annotations.py` 校验、`verify_samples.py` 五项自动验收） | S1-1 ✅ |
| 管理端模板页（票种/规则） | `web/src/views/TemplateAdmin.vue`（列表 + 详情抽屉 + 字段规格 JSON + 规则编辑 + 试跑；PUT 升版本，旧版本保留）+ `api/templates.ts` + `types/template.ts` + `http.put` + `scripts/ui_smoke.mjs`（`--steps` 的 tab/upload 动作）；新建入口已移除，票种固定 5 类 + unknown（D-054） | 用户反馈后补 ✅ |
| 定时任务 | `backend/app/tasks/scheduler.py`（AsyncIOScheduler + CronTrigger，时区 `Asia/Shanghai`；`run_anchor_audit_once` 抢 Redis `SET NX EX`，**成功后锁保留到 TTL** 作为时段去重、失败释放 + Lua token 校验；`SCHEDULER_ENABLED` 开关）+ `main.py` lifespan + `core/config.py`（cron/锁配置）+ `tests/{test_scheduler,test_api_docs}.py` | S2-12 ✅ |
| 前端票据列表与详情 | `web/src/views/InvoiceList.vue`（筛选/分页，**route.query 为唯一状态源**）+ `views/InvoiceDetail.vue`（字段表/置信度按 `review_level` 三档着色/草稿行内编辑 + 40900 刷新/42201 保留输入；影像占位"待 S3-5"）+ `api/invoices.ts`（list/update/waitForOcr）+ `utils/format.ts`（金额/Asia-Shanghai 时间/标签）+ `types/invoice.ts` + `scripts/ui_smoke.mjs` `--steps` | S2-11 ✅ |
| 前端上传页 | `web/src/views/Upload.vue`（拖拽/多选 ≤20、缩略图、本地校验、**串行上传**、单文件+总进度、409 绿色去重、120s 轮询、objectURL 清理）+ `api/files.ts`（FormData + `silent` 上传）+ `api/invoices.ts`（`waitForOcr` 3s→15s 退避、总 120s）+ `types/invoice.ts` + `scripts/ui_smoke.mjs` `--upload`（CDP setFileInputFiles） | S2-10 ✅ |
| 前端登录与主框架 | `web/src/api/http.ts`（401 自动 refresh **单飞** + 重放一次 + 失败清会话；不解析 JWT exp；DEV `__httpDebug` 诊断钩子）+ `stores/user.ts`（access/refresh/user 持久化、`role`/`permissions`/`hasRole`/`setSession`）+ `router/index.ts`（`meta.roles` + `/auth/me` 水合 + `/403` + 按角色落地）+ `utils/landing.ts` + `styles/theme.css`（全局视觉基线）+ `layout/MainLayout.vue`（深色侧栏/角色菜单，无概览/联调入口）+ `views/{Login,Forbidden,Placeholder}.vue` + `scripts/ui_smoke.mjs`（`--fill`/`--clear`/`--view`/`--shot`/`--steps`） | S2-9 + UI ✅ |
| 前端 http 封装与页面骨架 | `web/src/api/http.ts`（拦截器注入 token、统一解包、401 跳登录、`ApiError` 带 `payload`）+ `web/src/router/index.ts`（守卫 + `meta.roles` 预留）+ `web/src/stores/user.ts`（token 在 localStorage）+ `web/src/views/Debug.vue`（联调自检页）+ `scripts/ui_smoke.mjs`（无头浏览器冒烟） | S0-6 ✅ |

## 11. 任务执行协议（功能先 spec 后 plan，再写码）

1. 读任务卡 + 契约 → 走 brainstorming 澄清设计与边界 → **spec** 落 `docs/spec/<任务号>-<主题>.md`
   （如 `s1-2-preprocess.md`）；再由 writing-plans 出 **plan** 落 `docs/plan/<任务号>-<主题>.md`
   （分步、每步带验证命令）。spec 与 plan 都经用户确认后才写码。
2. 严格按 plan 实现；要偏离 plan 必须先说明原因并获用户同意。
3. 规模适配：小修 / bugfix 不开 spec、plan，聊天里短设计 + 确认即可；**功能级任务卡一律全流程**。
4. 写完 → 真实验证（pytest / curl / 界面路径）→ 贴实测输出，禁止"应该没问题"。
5. 文档同步：`PROGRESS` 必更；动接口补 `API.md`、动表补 `SCHEMA.md`、动配置补 `.env.example`、动依赖补 `requirements.txt`。
6. Conventional Commits 提交（含任务号）；**用户负责 `git push`**。
7. 验收不过或做不完：如实报告，禁止粉饰进度、禁止假数据（§7）。
8. spec/plan 与 `API.md`/`SCHEMA.md` 冲突时契约文档优先；发现不一致先报告（§9）。