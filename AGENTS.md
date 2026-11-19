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

> 当前为空（S0 阶段）。规则：第一个把某模块写成正式代码的任务，负责在此登记锚点文件，
> 后续同类代码先照锚点写；觉得锚点过时 → 先改锚点，再写新码。

| 模块 | 锚点文件 | 回填任务 |
|---|---|---|
| 后端骨架 | `backend/app/core/{config,logging,exceptions,health}.py` + `backend/app/schemas/common.py` + `backend/app/main.py`（`create_app()` 工厂、`X-Trace-Id` 中间件、`request.state.trace_id`、异常处理器；`/health` 双路径 + 503 降级；`Settings` 缺项启动即抛 `ConfigError`） | S0-5 ✅ |
| 数据模型与迁移 | 待回填 | S2-1 |
| 认证与权限依赖 | 待回填 | S2-2 |
| 分页与筛选 | 待回填 | S2-4 |
| 文件上传与去重 | 待回填 | S2-6 |
| 异步任务框架 | 待回填 | S2-7 |
| OCR 预处理 | `backend/app/ocr/preprocess.py`（`PreprocessOptions` 全开关；EXIF→缩放→定位→透视/降级→放大→去斜→照度→印章；返回 `to_original` 3×3 供坐标回算；失败只降级不抛异常）+ `scripts/{preprocess_demo,preprocess_eval}.py` | S1-2 ✅ |
| OCR 检测识别适配器 | `backend/app/ocr/{detector,recognizer,types}.py`（`OcrEngine` 单例 + 懒加载 + 互斥锁；`parse_paddle_result` 解析 2.7 结构并**按 y 分桶再按 x 排序**给 `line_no`；遇到 3.x dict 抛 `OcrContractError`；`recognize_line` 供 S3-3 复用）+ `scripts/ocr_demo.py` | S1-3 ✅ |
| 票种识别与版面分块 | `backend/app/ocr/{layout.py,templates/*.yaml}`（关键字加权 ×3 标题加成 + 互斥词扣分 + 长宽比先验；`unknown` 降级；区域**按内容框归一化**；block 聚类）+ `scripts/{layout_demo,layout_eval}.py` | S1-4 ✅ |
| 字段抽取器与校验器 | 待回填 | S1-5 |
| 密码封装（canonical / sm / signature / envelope / keystore） | 待回填 | S4-1 ~ S4-6 |
| 审计哈希链 | 待回填 | S4-7 |
| 评测脚本与指标函数 | 待回填 | S1-6 |
| 合成票据生成器 | `scripts/{invoice_layout,invoice_templates,perturb,sample_data,sample_fonts,make_samples}.py`（绘制即记录坐标 → 几何扰动同步变换 bbox → 光度/叠加 → JSONL 标注；`check_annotations.py` 校验、`verify_samples.py` 五项自动验收） | S1-1 ✅ |
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