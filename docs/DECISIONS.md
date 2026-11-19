# DECISIONS.md — 决策记录

> 用途：记录每一次**选型与取舍**的背景、备选、结论与代价。论文"系统设计 / 方案选型"章节可以直接改写自本文件。
>
> 格式约定：每条一个 `## D-xxx 标题`，字段固定为
> `日期 / 背景 / 备选 / 决策 / 理由 / 代价 / 验证`。
> 编号只增不改；被推翻的决策**不删除**，追加"被 D-xxx 取代"。
>
> 维护人：本人（关键决策）。AI 可以起草，但不能自行新增或修改结论。

---

## D-001 后端框架：FastAPI 而非开题报告写的 Flask

- **日期**：2026-11-18（S0 阶段确认）
- **背景**：开题 PPT 的技术选型表写的是 `Flask + MySQL`。动手前重新评估一次，避免后期返工。
- **备选**：① Flask（开题口径） ② FastAPI ③ Django
- **决策**：FastAPI
- **理由**：
  1. OCR 是秒级长任务，FastAPI 原生 `async` 与 ARQ 队列生态更顺，不需要自己拼装异步与后台任务；
  2. Pydantic 同时承担"接口出入参校验"和"字段抽取结果校验"两个职责，省掉一套 schema；
  3. 自动生成 OpenAPI 文档，前端联调成本低；
  4. 与开题技术栈的调整需要在论文里说明——见《完整方案》4.3.2 的答辩话术。
- **代价**：与开题口径不一致，需在论文"系统设计"中说明调整理由，答辩可能被追问。
- **验证**：S0-5 后端骨架可运行、`/api/v1/health` 返回正常；S1 的基线脚本用同一套配置体系。

---

## D-002 数据库：PostgreSQL 16 而非开题报告写的 MySQL

- **日期**：2026-11-18
- **背景**：同上，开题写 MySQL。
- **备选**：① MySQL 8 ② PostgreSQL 16
- **决策**：PostgreSQL 16
- **理由**：
  1. 抽取结果、文本框坐标、规则命中详情天然是 JSON，`JSONB` 可直接建 GIN 索引与查询，MySQL 的 JSON 能力弱一档；
  2. 需要**部分唯一索引**：`invoices (invoice_code, invoice_no) WHERE status <> 'void'`（作废票可重开）、
     `signatures (form_id) WHERE status = 'active'`（每单只有一条有效签名）——MySQL 不支持部分索引；
  3. `CHECK` 约束与表达式索引更完整，可以把业务不变式下沉到库。
- **代价**：与开题口径不一致，需在论文中说明。
- **验证**：S2-1 迁移里三条部分唯一索引创建成功，并用"重复发票/重复 active 签名"的插入用例证明真的拦得住。

---

## D-003 国密库：`gmssl`（纯 Python）而非官方 `gmssl-python`

- **日期**：2026-11-18
- **背景**：需要 SM2/SM3/SM4 完成签名与数字信封。
- **备选**：① PyPI `gmssl`（纯 Python 实现，`pip install gmssl`） ② PyPI `gmssl-python`（官方 ctypes 封装，需先编译 libgmssl）
  ③ 调 OpenSSL 3.x 命令行 ④ 商用密码机
- **决策**：① 为主；③ 仅用于生成 SM2 密钥对与证书这类一次性操作
- **理由**：
  1. Windows 开发环境零编译，`pip install` 即用、离线可跑；
  2. 覆盖 SM2 加解密/签名、SM3 摘要、SM4 分组加密，够本课题使用；
  3. ② 需要本机编译 C 依赖，Windows 上安装成本高，与"两周内跑通链路"的目标冲突，列为可选升级路线。
- **已知缺陷（已在代码中补偿，见 AGENTS.md §6）**：
  - `func.random_hex` 基于 Python `random` 模块 → 全项目改用 `secrets` 提供 nonce/IV；
  - `decrypt()` 内部计算了 C3 校验值却未比对 → 完整性由 HMAC-SM3 与 `payload_sha256` 保证；
  - `mode` 默认 `0`（C1C2C3），国标排列为 C1C3C2 → 全项目强制 `mode=1`；
  - SM4 只提供 ECB/CBC（无 GCM）→ 采用 CBC + HMAC-SM3 的 Encrypt-then-MAC；
  - `sm3_hash` 接收 `list[int]` 且会原地修改入参 → 统一封装为 `sm3_digest(bytes)`。
- **代价**：纯 Python 性能有限（SM4 处理 2MB 约数百毫秒），需在性能章节如实报告；无证书链验证能力。
- **验证**：S4-8 用国家标准测试向量（SM3 `"abc"`、SM4 单块）全部通过；信封往返 + 篡改 1 bit 失败用例通过。

---

## D-004 证书体系：只做指纹绑定 + 有效期 + 吊销，不做证书链验证

- **日期**：2026-11-18
- **背景**：开题要求"公钥以数字证书形式发布"，但纯 Python 国密库不提供证书解析与链验签能力。
- **备选**：① 完整 PKI（自建 CA + 链验证 + CRL/OCSP + TSA） ② 指纹绑定 + 有效期 + 吊销字段 ③ 直接用裸公钥
- **决策**：②
- **理由**：签名与信封的安全性首先取决于私钥保密与公钥—身份绑定；链验证需要 SM2 证书签名验证能力，
  纯 Python 库不具备，自研等于再写一套密码库，工期与风险都不允许。③ 无法防御公钥替换，安全性不足。
- **代价**：无法抵御"CA 私钥泄露后伪造证书"的场景。**论文"安全性分析"必须把这条写成局限性**，并给改进路径
  （引入 GmSSL 原生库或商用密码机）。
- **验证**：S4-6 实现签发、指纹入库、有效期与吊销校验，并给出"篡改指纹 / 用过期证书 / 用错配私钥"三组用例。

---

## D-005 不做浏览器端端到端加密（服务端密码服务）

- **日期**：2026-11-18
- **背景**：真正的 E2E 要求私钥在用户侧生成与保管，服务端无法解密。
- **备选**：① 浏览器端 `sm-crypto` 生成密钥对 + IndexedDB 加密存储 ② 服务端密码服务 + 私钥口令加密托管
- **决策**：②
- **理由**：① 需要 JS 国密库、浏览器端密钥生成与本地安全存储、跨设备密钥同步策略，工作量再增 2–3 周，
  而毕设核心指标（识别准确率、密码性能）并不因此改善。
- **代价**：服务端在密码运算瞬间能接触明文，机密性依赖服务端可信与运维规范。**必须写进论文"不足与展望"**。
- **验证**：S5 任务里通过"明文不落盘 + 日志脱敏 + 临时文件零残留扫描"（S7-3）证明工程侧做到了当前方案的极限。

---

## D-006 锁定 `paddleocr==2.7.3`，不升级 3.x

- **日期**：2026-11-18
- **背景**：PaddleOCR 3.x 重构了推理入口与返回结构。
- **备选**：① 2.7.3（`ocr.ocr(img, cls=True)` 返回嵌套列表） ② 3.x（`predict()` 返回含 `rec_texts/rec_scores/dt_polys` 的字典）
- **决策**：①
- **理由**：2.7 的 PP-OCRv4 模型与坐标输出完全满足需求，且文档/示例最丰富；升级意味着识别层适配重写，
  而识别层不是论文创新点。3.x 的差异由 `backend/tests/test_ocr_contract.py` 契约测试兜住。
- **代价**：无法使用 3.x 的新特性（如 new IR 模型）；若未来升级，需同时改 `detector.py` / `recognizer.py` 与契约测试。
- **验证**：S1-3 的契约测试对返回结构做断言；`requirements.txt` 写死版本。

---

## D-007 枚举用 `TEXT + CHECK`，不用 PostgreSQL ENUM

- **日期**：2026-11-18（S0-3 契约阶段定稿）
- **背景**：`users.role`、`invoices.status`、`signatures.status` 等都是有限取值集合。
- **备选**：① PostgreSQL 原生 `ENUM` ② `TEXT + CHECK` 约束 ③ 独立枚举表 + 外键
- **决策**：②
- **理由**：原生 ENUM 增加取值需要 `ALTER TYPE`，且在事务/迁移里限制多；`TEXT + CHECK` 加值只需换约束，
  迁移简单，且值集常量可以放在 `app/models/enums.py` 里被 Python 侧复用（Pydantic 校验与 CHECK 文本同源）。
- **代价**：数据库层不做类型收敛，拼写错误只能靠 CHECK 拦；取值较多时 CHECK 表达式会变长。
- **验证**：S2-1 迁移里每个枚举列都有 `ck_<表>_<列>` 命名的 CHECK；插入非法值必须被拒。

---

## D-008 金额与置信度：`NUMERIC` + `Decimal`，禁止 float

- **日期**：2026-11-18（S0-3 契约阶段定稿）
- **背景**：金额一致性校验需要 `|total − (amount + tax)| ≤ 0.01`，置信度需要 0–1 的阈值比较。
- **备选**：① `DOUBLE PRECISION` + Python float ② `NUMERIC(12,2)` / `NUMERIC(4,3)` + Python `Decimal`
- **决策**：②
- **理由**：float 在 0.01 容差处会误判（如 `0.1+0.2 != 0.3`），且 JSON 序列化时 `1234.50` 会变 `1234.5`，
  直接破坏签名规范化的稳定性。
- **代价**：运算稍慢、需要显式量化（`Decimal("0.01")`），Pydantic 需配置 `Decimal` 序列化为字符串。
- **验证**：S1-5 校验器单测里放一组"浮点会算错"的用例（如 `1234.56 − (1000.00 + 234.56)`）。

---

## D-009 业务不变式下沉到数据库（部分唯一索引 + CHECK）

- **日期**：2026-11-18（S0-3 契约阶段定稿）
- **背景**：几条不变式（一张发票只能报一次、每单只能有一条有效签名、重复提交要幂等）如果只靠应用层判断，
  并发下必然被击穿。
- **备选**：① 只在应用层判重 ② 数据库唯一/部分唯一索引兜底 + 应用层给出友好提示
- **决策**：②
- **理由**：`uq_invoices_code_no`、`uq_signatures_form_active`、`report_forms.idempotency_key` 唯一、
  `invoice_files.sha256` 唯一，四条约束让"并发重复"在库层直接失败，应用层捕获 `IntegrityError` 转 409。
- **代价**：迁移里必须写部分索引（Alembic 需手写 `op.create_index(..., postgresql_where=...)`）；
  软删除/作废要设计成"释放约束"的语义（`status='void'` 不参与唯一）。
- **验证**：S3-7 / S5-2 的并发用例：两个请求同时提交同一发票，断言 1 成功 1 冲突。

---

## D-010 评测口径：字段级准确率与整单正确率

- **日期**：2026-11-18（S0-3 契约阶段定稿，S6 用实测数据校准）
- **背景**：论文需要可复现的识别指标，口径必须先定死，否则最后写论文时口径打架。
- **备选**：① 只报字符准确率 ② 字符准确率 + 字段级准确率 + 整单正确率 ③ 再加端到端业务指标
- **决策**：②，端到端指标（耗时、待确认率）单列
- **口径定义**：
  - **字符准确率**：`1 − 编辑距离 / 标注长度`，按行拼接后计算；
  - **字段级准确率**：单字段 `normalized_value` 与标注**完全相等**记为正确；空标注不算分母；
  - **整单正确率**：该票**所有必填字段**都正确的票据占比（这是最严、最有说服力的指标）；
  - **低置信字段命中率**：被标记为 `need_review` 的字段中，确实错误的比例（用于校准阈值）。
- **代价**：整单正确率会显著低于字段级准确率，需要在论文里解释两者的关系，不能只报好看的。
- **验证**：S1-6 指标函数用手算小样本断言数值；S6 输出三张表并交叉核对。---

## D-011 票据级状态与报销单级状态分离

- **日期**：2026-11-18（S0-3 定稿）
- **背景**：《完整方案》5.2 给 `invoices.status` 定了 5 个值（`draft/confirmed/in_report/archived/void`），
  但 6.6 的状态图把 `signed/encrypted/submitted/approved/rejected` 也画进了同一条链，两处冲突。
- **备选**：① 合成一条大状态机（票据状态随报销单推进）② 两级分离：票据管识别与归属，报销单管签批流转
- **决策**：② 两级分离。`invoices.status` 只保留 5 值；`signed` 之后的状态全部落 `report_forms.status`。
- **理由**：一张票可以被驳回、退出某张报销单、再进另一张单（`in_report → confirmed`），
  它自己并没有"已提交财务"这种状态；把签批状态挂在票据上会导致"同一张票在两张单里状态互相打架"。
- **代价**：查"这张票现在走到哪了"需要 join 报销单；论文里 6.6 的状态图要重画成两张（票据生命周期 / 报销单生命周期）。
- **验证**：S2-1 迁移里 `invoices.status` 的 CHECK 只有 5 值；S5 的单测覆盖"驳回后票据回到 `confirmed` 再进新单"。

---

## D-012 信封归属与外键策略

- **日期**：2026-11-18（S0-3 定稿）
- **背景**：原设计在 `invoice_files.envelope_id` 与 `envelopes.file_id` 两侧互相引用，且 `certificates.owner_id`、
  `audit_logs.target_id` 是跨表多态引用；同时 `key_records.user_id` 对财务服务器密钥没有归属用户。
- **备选**：① 两侧都建外键 + `DEFERRABLE` ② 单向引用 + 应用层校验 ③ 不存储归属（每次现算）
- **决策**：
  1. **删除 `invoice_files.envelope_id`**，文件级信封只通过 `envelopes.file_id` 反查；
  2. 加部分唯一索引 `envelopes (file_id) WHERE file_id IS NOT NULL` 与 `envelopes (form_id) WHERE form_id IS NOT NULL`，
     保证"一个文件最多一个信封、一张单最多一个载荷信封"；
  3. 多态引用（`certificates.owner_id`、`audit_logs.target_id`、`audit_logs.target_type`）**不建外键**，用 CHECK 兜语义；
  4. `key_records.user_id` 允许 NULL，用 `CHECK (user_id IS NOT NULL OR key_type='envelope')` 兜住。
- **理由**：循环外键会让 Alembic 建表顺序死锁（或被迫写两阶段迁移），而收益只有"数据库帮我保证 1:1"；
  用"单向 + 部分唯一索引"能拿到同样的保证，且建表顺序自然。多态引用本来就不可能用单一外键表达。
- **代价**：归属关系靠应用层维护，脏数据只能被 CHECK 或唯一索引部分拦住；查询需要额外 join。
- **验证**：S2-1 建迁移后，插入"同一 file_id 的第二个信封"必须被唯一索引拒绝；`alembic downgrade base` 可往返。

---

## D-013 范围界定：本期明确不做的 6 件事

- **日期**：2026-11-18（S0-3 定稿）
- **背景**：S0-3 梳理契约时出现一批"要不要做"的岔路，逐条拍板以免 S2~S6 各写一套。
- **决策**：

| 项 | 决策 | 理由 |
|---|---|---|
| 红冲 / 负数票 | **不支持**，`total_amount >= 0` 保留 | 回收流程与作废语义会牵动重复检测与签名域，收益低；论文"范围限定"写明 |
| 批量上传接口 | **不新增**，前端串行队列复用单文件接口 | 后端多文件接口要处理部分失败、事务边界，工期不划算；串行队列已满足"一次传 10 张" |
| 弱重复检测接口 | **不单独建接口** | 结果已体现在上传响应的 `dedup` 与详情 `duplicate_of`，再开接口是重复表达 |
| 模板 rules 独立 CRUD / 别名接口 | **不做**，规则随模板 `PUT` 的 `rules` 数组同步 | 规则脱离模板没有业务意义，独立 CRUD 只会带来一致性漏洞 |
| 审计日志 CSV 导出 | **不做** | 论文不需要；导出会扩大敏感数据泄露面，分页接口足够 |
| 评测结果入库（`eval_runs` 表） | **不建表**，结果只落 `backend/eval/results/*.json` | 文件即真相，天然带时间戳、不占库、便于论文附图；接口按文件读 |

- **代价**：以上能力若答辩被追问，统一回答"已列入展望，接口/表已预留扩展点"。
- **验证**：S6-5 的评测跑批只写文件；S3-8 只做前端队列与进度；`docs/API.md` 无批量上传与 rules CRUD 接口。

---

## D-014 小值集与默认值集中定稿

- **日期**：2026-11-18（S0-3 定稿）
- **背景**：多个"方案没写死"的小枚举与默认值散落在契约里，不统一就会在代码里各写各的。
- **决策**：
  - `invoices.quality_flag` = `ok` / `low`（**不设 `fail`**：不可识别由 `ocr_status='failed'` 表达）
  - `ocr_lines.block_id` = `TEXT`，格式 `b{页码}-{块序号}`
  - `field_rules.priority` = 默认 100、小者先、同分按 `id` 升序；`pattern` 写入时 `re.compile` 预校验
  - `audit_logs.target_type` = `invoice/invoice_field/form/report_item/signature/envelope/certificate/key/user/template/eval_run`
  - `users.status` = `active` / `disabled` / `locked`
  - OCR 进度权重 = `preprocess 10 / detect 30 / recognize 40 / extract 20`
- **理由**：小值集最怕"两处编码同一事实"（quality_flag 的 `fail` 与 `ocr_status` 的 `failed`），
  定成互斥的一套能省掉后续所有对齐成本；权重直接按耗时预算比例给，前端进度条可解释。
- **代价**：值集固定后新增取值需要改 CHECK（用 `NOT VALID` + `VALIDATE`，不锁表）。
- **验证**：S2-1 每个枚举列都有 `ck_<表>_<列>`；`app/models/enums.py` 与本文件一致（同一份常量生成 CHECK 文本）。

---

## D-015 审计哈希链的并发串行化

- **日期**：2026-11-18（S0-3 定稿）
- **背景**：`entry_hash` 依赖上一条的 `entry_hash`，并发写会读到同一个 `prev_hash` 而产生分叉，导致链校验误报。
- **备选**：① 单写者内存队列 ② `SELECT ... FOR UPDATE` 锁最后一行 ③ 事务级咨询锁 `pg_advisory_xact_lock` ④ 串行化隔离级别
- **决策**：③
- **理由**：② 在"表为空/首条"时不阻塞（`FOR UPDATE` 对空结果不生效），照样分叉；① 多进程部署就失效；
  ④ 代价过大（全库串行）。咨询锁以固定常量作 key，事务结束自动释放，天然与"审计与业务同事务"配合。
- **代价**：审计写入被串行化，高并发下会排队（本课题量级完全可接受）；锁 key 冲突需全局唯一（用项目常量，如 `55001`）。
- **验证**：S4-7 并发写 50 条审计，`verify-chain` 必须一次通过、无断链。

---

## D-016 会话形状与口令托管边界

- **日期**：2026-11-18（S0-3 定稿）
- **背景**：认证要不要 refresh/logout、财务服务器私钥口令怎么传、提交人私钥口令怎么传、预览凭据能不能复用。
- **决策**：
  1. `access_token` 30min + `refresh_token` 7d，refresh **轮换式**（用一次换一个）；refresh 存 Redis 白名单，`logout` 删白名单；
  2. **提交人**签名私钥口令由请求体传入（每人一把，口令只能由本人掌握）；
  3. **财务服务器**信封私钥口令走**环境变量** `FINANCE_KEY_PASSPHRASE`（单实例服务端持有一把）；
  4. 财务预览凭据 `preview_token` 30min 内**可重复读**，过期 410。
- **理由**：口令的传输方式取决于"谁该知道它"。用户私钥口令进环境变量等于服务端能代签，不可否认性立刻失效；
  服务器私钥口令进请求体则等于每次审核都把它塞进 HTTP 与日志风险面。预览一次性会让财务"看一眼就得重新验签"，反人类。
- **代价**：环境变量口令在容器内可见（演示可接受，论文列为局限，生产应接 HSM/密钥管理服务）；用户口令走 HTTP 依赖 HTTPS。
- **验证**：S2-2 单测覆盖 refresh 轮换后旧 token 失效、logout 后 refresh 失效；S5-4 断言响应与日志中无口令。

---

## D-017 人工修正的权限矩阵

- **日期**：2026-11-18（S0-3 定稿）
- **背景**：字段核对页人人可改，但"改金额"这件事性质不同——识别错了改是修正，人为压低金额是舞弊。
- **决策**：`submitter` 可改非金额字段；`amount` / `tax_amount` / `total_amount` **仅 `reviewer` 可改**且必须带 `confirm_amount=true`；
  `admin` 一律 404（系统管理员不碰业务数据）。所有修正写 `audit_logs`（before/after 脱敏）。
- **理由**：金额是报销的唯一利益相关字段，必须双人可见；`admin` 越权改业务数据会让"职责分离"的设计失效。
- **代价**：报销人发现金额识别错时不能自己改，需要走审核环节（这是制度上的正确，不是技术缺陷）。
- **验证**：S3-6 单测：submitter 改 `total_amount` → 403/422；reviewer 不带 `confirm_amount` → 422；admin → 404。

---

## D-018 单文件上限统一为 20MB

- **日期**：2026-11-18（S0-3 定稿）
- **背景**：《完整方案》3.2 A1 写 20MB，实施计划 S2-6 的提示词举例写 10MB，两处口径不一致。
- **决策**：**20MB**，单一来源为配置 `MAX_UPLOAD_MB=20`；前端与后端都读它，Nginx `client_max_body_size 25m` 留余量。
- **理由**：票据 PDF（多页扫描件）常见 5–15MB，10MB 会让正常票据被拒；上限的真正约束是 Nginx 与内存，而不是业务。
- **代价**：单请求内存峰值上升（流式写入可缓解）；超过上限返回 `413/41300` 而非 422，前端要能识别。
- **验证**：S2-6 用 19.9MB / 20.1MB 两个文件各测一次，前者 202、后者 413。
---

## D-019 基础服务端口错开与本机镜像优先

- **日期**：2026-11-18（S0-4）
- **背景**：本机已有一套 `study-agent` 的容器占着 5432 / 6379 / 9000 / 9001；同时 Docker Hub 直连超时，
  `docker pull` 拉不到新镜像，但本机已缓存 `pgvector/pgvector:pg16`、`redis:7-alpine`、`minio/minio:latest`、`minio/mc:latest`。
- **备选**：① 沿用默认端口，两套环境互斥（跑 OCR 就得停 study-agent）② 端口错开，两套并存
  ③ 改用 Podman/其它 runtime（无必要）
- **决策**：② + 镜像 tag 走 `.env` 变量、默认取本机已缓存版本
- **理由**：毕设期间两套项目都要能随时演示，互斥会频繁打断；端口只是宿主侧映射，改一下没有技术代价。
  镜像默认值取"离线可用"的 tag，保证没网也能 `up -d`——本课题真正的环境风险是网络，不是版本洁癖。
- **代价**：所有连接串要带非默认端口（PG 5433 / Redis 6380 / MinIO 9002），
  第一份 `DATABASE_URL` 写错就会排查半天，因此在 `deploy/.env.example` 与 README 里都写在显眼处；
  镜像 tag 未钉到具体 release，生产化前需换成固定 tag（S7-2）。
- **补充：镜像拉取的实测结论（2026-11-18）**
  - `docker pull postgres:16` 会因 mirror 逐个失败后**回退到 registry-1.docker.io** 而超时，
    报错只显示最后一次尝试，容易误判为"镜像源没配"；
  - 解法是**用镜像源全名拉取再 `docker tag` 成标准名**，绕过回退逻辑；已封装为 `scripts/pull_image.sh`；
  - 实测：`docker.m.daocloud.io` 可用（postgres:16、nginx:alpine 均成功）；`docker.1ms.run` 小镜像可、
    大镜像层出现 `unknown blob`；华为 SWR 只缓存部分镜像（本机 minio 来自它，但 `library/postgres:16` 无 tag）。
  - 因此**默认镜像仍取标准名**（`postgres:16`），能否拉到由脚本负责；离线时退回 `.env` 变量指向本机缓存。
- **验证**：`docker compose -f deploy/docker-compose.dev.yml up -d` 与 `python scripts/check_services.py`
  5 项全 OK；`down` 不删卷再 `up` 后 bucket 仍在、`minio-init` 不报错（幂等）；
  把 PG 镜像从 `pgvector/pgvector:pg16` 换成 `postgres:16` 后重建，自检仍 5/5。

---

## D-020 后端骨架的四处实现取舍

- **日期**：2026-11-18（S0-5）
- **背景**：骨架代码虽小，但有四处"以后很难改"的选择，先定下来。
- **决策**：

| # | 取舍 | 选择 | 理由 |
|---|---|---|---|
| 1 | 日志设施 | **只用 stdlib `logging` + 自写 `JsonFormatter`** | 任务卡明确禁止 loguru/structlog 混用；stdlib 足够输出单行 JSON，少一个依赖就少一处版本冲突 |
| 2 | 响应壳组装 | **各接口显式调 `envelope()`**，不靠 `response_model` 自动包装 | 自动包装会与 FastAPI 的 `response_model` 序列化打架，还要处理泛型 `Envelope[T]`；显式组装一眼看得出返回什么，也便于错误处理器复用 |
| 3 | trace_id 传递 | contextvar **+** `request.state.trace_id` 双通道 | 未捕获异常由最外层 `ServerErrorMiddleware` 兜底，此时 contextvar 已被中间件 reset，只靠 contextvar 会导致**日志与响应里的 trace_id 变成 `-`**（实测踩到，见 PROGRESS 变更日志） |
| 4 | 健康检查 | 两条路径同一实现；检查 DB/Redis/MinIO；任一项失败 → **503 + status=degraded**；单项超时 2s | 容器探针只认状态码；探测本身不能变成新故障源 |

- **代价**：响应壳靠人工调用，忘记包一层就会返回裸 JSON（用测试兜住：每个新接口都要有一条断言 `set(body)=={code,message,data,trace_id}` 的用例）。
- **验证**：24 个单测（配置 7 / 健康 5 / 错误与 trace 12）；起 uvicorn 后 `/api/v1/health` 真实连三件套返回 200；同一 trace_id 能在日志里串起错误日志与访问日志。

---

## D-021 前端四处取舍（token 存哪、CORS 还是 proxy、错误要不要携带 payload、开发态冒烟入口）

- **日期**：2026-11-18（S0-6）
- **背景**：S0-6 搭前端骨架时遇到四个"以后很难改"的选择；其中 token 存储方式是任务卡明确要求记录的（S4 讨论私钥存储会引用它）。
- **决策**：

| # | 取舍 | 选择 | 理由与代价 |
|---|---|---|---|
| 1 | token 存哪 | **localStorage**（键 `study_ocr_token`） | 前后端分离 + 开发期跨域时，httpOnly Cookie 要处理 SameSite/Secure/CORS 携带，毕设成本高。**代价：XSS 可读**——因此要求所有动态内容走 Vue 插值（天然转义），禁止 `v-html` 渲染用户可控内容。这条会成为 S4 讨论"私钥能不能放浏览器"时的引用点：token 都这么处理，私钥更不能放前端。 |
| 2 | 跨域方式 | **只用后端 CORS**，不配 Vite proxy | 跨域配置只在一处（后端 `CORS_ORIGINS`），前端不需要知道后端端口；生产同源时把 `VITE_API_BASE` 改成 `/api/v1` 即可。任务卡的坑就是"两个都配会出难以理解的 404"。 |
| 3 | 错误响应要不要带数据 | `ApiError` 增加 `payload` 字段，透传后端信封 | 实测踩到：`/health` 依赖挂了返回 **503 + 完整信封**，若拦截器只取 `message`，页面就只剩"未请求"，降级详情全丢。带 `payload` 后前端能渲染降级表（`degraded` 标签 + `fail` 行）。409 的 `current_version`、422 的字段明细同理。 |
| 4 | 开发态冒烟入口 | 允许 `?dev_token=xxx` 在 `main.ts` 里写入 localStorage 并抹掉参数，**整段包在 `import.meta.env.DEV` 内** | 无头浏览器冒烟需要直接打开受保护页面（S7 冷启动检查会复用）。`import.meta.env.DEV` 是编译期常量，生产构建会被摇掉（可验证：`npm run build` 后在 `dist/` 里搜 `dev_token` 应无命中）。代价：开发态存在"URL 带 token"的便利口子，团队内约定不在真实环境使用。 |

- **验证**：`npm run typecheck` + `npm run build` 通过；无头 Edge（CDP）实测：未登录访问 `/` → `/login?redirect=/`；`/debug?dev_token=smoke` → 4 个 tag 全 ok；点「触发一个 404」→ 弹出后端「资源不存在」且 trace_id 回填；停 MinIO → 显示 `degraded` + 一个 `fail`，恢复后回全 ok；控制台无 CORS 报错。

---

## D-022 基础设施组合：PostgreSQL 16 + Redis 7 + MinIO + ARQ

- **日期**：2026-11-18（S0-7）
- **背景**：S0 阶段把三件套跑起来之后，需要一个**完整的**选型记录（任务卡要求"第一条 ADR"）。前面 D-002 只解释了
  PostgreSQL 与 MySQL 的取舍，Redis / MinIO / 队列的选型散落在方案里，没有单独成条。
- **备选（整体）**：
  ① 全托管云服务（RDS + ElastiCache + OSS） ② 单机 Docker 三件套 + ARQ ③ SQLite + 本地文件系统 + 线程池（最轻）
- **决策**：②
- **逐项理由**：

| 组件 | 选择 | 不选别的的原因 |
|---|---|---|
| 关系库 | **PostgreSQL 16** | 需要 `JSONB`（字段/坐标/规则详情）与**部分唯一索引**（发票唯一只约束非作废、每单只有一条 active 签名）；MySQL 的 JSON 能力弱一档、不支持部分索引（详见 D-002） |
| 缓存 + 队列 | **Redis 7 + ARQ** | OCR 是秒级长任务，必须离屏执行；ARQ 是 asyncio 原生，与 FastAPI 同生态、依赖少。Celery 要多一套 broker 概念与配置，RabbitMQ 对单机毕设是过度设计。Redis 同时承担缓存与限流，避免再引入一个组件 |
| 对象存储 | **MinIO（S3 兼容）** | 票据影像必须"库里只存引用、内容存对象存储且是密文"；MinIO 可离线、可与生产 S3 无缝互换，本地产物不会污染数据库备份。本地磁盘能跑但不便于演示"密文独立存储 + 独立备份"这一安全设计 |
| 定时任务 | **APScheduler** | 只需"审计链锚定 + 临时文件清理"两个定时动作，进程内调度足够；不引入 Celery Beat |
| 为什么不用 SQLite | — | 需要并发写、`JSONB`、部分索引与后续的可能 RLS；SQLite 在这些点上都会成为返工来源。开发即用生产同款，减少"开发能跑、上线炸" |

- **代价**：
  - 本机多三个容器、约 1.5 GB 镜像；首次启动要等 healthcheck；
  - 端口被默认值占用时需错开（本项目用 5433/6380/9002，见 D-019）；
  - Docker 不可用时整套环境起不来 —— 因此 `scripts/smoke.py` 要在"服务没起"时给人话提示而不是 traceback。
- **验证**：`docker compose -f deploy/docker-compose.dev.yml up -d` 三个容器 healthy；`scripts/check_services.py` 5 项
  通过；`scripts/smoke.py` FAIL 0（迁移项在 S2-1 前为 WARN）；`down` 不删卷再 `up`，数据与 bucket 都在。

---

## D-023 票种覆盖以数电票为主，旧版增值税电子普通发票保留为"历史形态"

- **日期**：2026-11-18（S1-1 修正）
- **背景**：S1-1 首版只做了旧版「增值税电子普通发票」版式（有发票代码、20 位校验码）。
  随后拿到一份 2026-10 开具的真实票据（`samples/MANIFEST.md` R-002），结构统计显示它是
  **电子发票（普通发票）· 数电票**：20 位发票号码、**无发票代码、无校验码**、购销方以
  "名称 / 统一社会信用代码"成对出现、页面 210×140 mm、开票人是独立字段。
- **备选**：① 只保留旧版版式 ② 只做数电票 ③ 两者都做，数电票为默认
- **决策**：③，并新增 `vat_digital` 版式为第一优先；合成集从 40 张扩到 **60 张**（6 种版式各 10 张）。
- **理由**：开题报告本身写明"全电发票已于 2024 年 12 月起在全国推广"，**数电票才是 2026 年的主流**；
  只做旧版等于把系统训练/评测在一个正在退场的版式上，S1-5 的抽取规则也会照着错的形态写。
  旧版票仍会出现在历史报销里，保留它可以让 S6 观察"跨版式的泛化"。
- **代价**：字段集出现票种差异（`invoice_code` / `check_code` 仅旧版有），抽取规则要按票种分支；
  样本集从 40 涨到 60，生成时间与标注文件体积略增（约 250 KB）。
- **验证**：`check_annotations.py --strict` 按 `ticket_type` 校验号码位数（数电票 20 位、其余 8 位），
  并**拒绝**数电票出现 `invoice_code` / `check_code`；`verify_samples.py` 五项仍全过（60 张）。

---

## D-024 电子发票 PDF：有文本层时优先走文本层，不强行 OCR

- **日期**：2026-11-18（S1-1 发现，S2-6 实现）
- **背景**：真实数电票 PDF（R-002）由税务端 `Suwell OFD convertor` 生成，**自带文本层**（373 字符 / 31 行），
  每行文字都有精确坐标；而纸质票据拍照件完全没有文本层。
- **备选**：① 一律先转图再 OCR（流程统一） ② 有文本层就用文本层，没有才 OCR
- **决策**：②（S2-6 上传链路落地）
- **理由**：电子件走文本层是"零误差"路径——不引入 OCR 误差，还能省掉整条预处理/检测/识别耗时；
  纸件才需要 OCR。这条差异本身可以写进论文的"工程取舍"：**同一个系统里两条抽取路径，按输入形态分流**。
  它也给了我们一个天然的对照：同一张票的文本层结果 vs OCR 结果，可以直接量化 OCR 的损失。
- **代价**：两条路径要各自维护字段抽取规则（文本层给的是带坐标的行，OCR 给的是带坐标的行 —— 结构一致，
  因此可以共用 `extractor.py`，代价不大）；要判断"是否有可用文本层"（无文字层的 PDF 很常见：扫描件）。
- **验证（待 S2-6）**：对 R-002 这类 PDF 断言"走文本层能抽到字段"，对扫描版 PDF 断言"自动回退到 OCR"。

---

## D-025 版本三角：opencv-python 4.6.0.66 + numpy 1.26.4 + paddleocr 2.7.3

- **日期**：2026-11-19（S1-2）
- **背景**：S1-2 要用 OpenCV；而 S1-3 要用的 `paddleocr==2.7.3` 把 `opencv-python<=4.6.0.66` 写死。
  先按"最新"装了 numpy 2.3.5 + opencv 4.6.0.66，结果 `import cv2` 直接
  `ImportError: numpy.core.multiarray failed to import`。
- **备选**：① 用最新 opencv(≥4.10) + numpy 2，S1-3 时覆盖 paddleocr 的 pin
  ② 降 numpy 到 1.26.4，严格对齐 paddleocr 的 pin ③ 把 OCR 放进独立进程/容器，两套依赖隔离
- **决策**：②（③ 记为后续可选项）
- **理由**：paddlepaddle 2.6 本身也是在 numpy 1.x 下编译的，用 numpy 2 大概率在 S1-3 再炸一次；
  "先对齐上游的硬约束，再谈升级"比"先升再说"省事得多。① 的代价是把风险推迟到 S1-3 才暴露，
  而那时已经在写识别代码，排查成本更高。
- **代价**：numpy 停留在 1.26（放弃 numpy 2 的新特性，本课题用不到）；
  · S1-1 的 60 张验收、65 个测试在降级后全部重跑通过，说明没有回归。
- **验证**：`import cv2, numpy` 正常；`pytest` 65 passed；`scripts/verify_samples.py` 5/5。

---

## D-026 预处理的三处实现取舍（都是被 OCR 对比逼出来的）

- **日期**：2026-11-19（S1-2）
- **背景**：S1-2 的验收里有一条"预处理不能弄坏文字"——用系统 OCR 在预处理前后各跑一次、比字段命中率。
  这条抓到三个"看起来更干净、OCR 反而更差"的坑。
- **决策**：

| # | 做法 | 不用另一做法的原因 |
|---|---|---|
| 1 | **检测在小图、透视重采样在原图** | 先缩放到 max_side 再裁切，会把纸面像素密度一起压小；实测一张 2800px 的票，OCR 字段命中率 56% → **0%**。改成在原图上 warp、只对结果限幅后，命中率恢复 |
| 2 | **恒等快捷**：四角点≈图像四角（±0.5%）时不做透视 | 对本来就在"正视角 + 满幅"的票，多一次插值就是白白损失清晰度：加粗的"票价"数字被抹花，OCR 3/3 → 2/3 |
| 3 | 照度校正：**目标取 p90、增益限幅 0.75–1.6、只施加 70%** | 用中位数会把整图压暗（contrast 84→9.5）；不限幅则把小票的浅灰底提成一片白，OCR 认错字（seller_name 100%→67%） |

- **代价**：恒等快捷让 `steps["rectify"]` 多了一个取值（`quad_identity`），下游统计要把它算作"校正成功"。
- **验证**：`scripts/preprocess_eval.py` 在可比样本上平均命中率 88% → 88%（+0%，无样本变差）；
  60 张数据集几何校正成功率 85%、耗时 P95 245ms（预算 300ms）。

---

## D-027 OCR 依赖整组锁死：paddleocr 2.7.3 + paddlepaddle 2.6.2（并压住三个"间接顶包"）

- **日期**：2026-11-19（S1-3）
- **背景**：S1-3 首次安装 OCR 引擎。先只装了 `paddlepaddle==2.6.2` + `paddleocr==2.7.3`，
  pip 的解析结果把 **numpy 从 1.26.4 顶回 2.4.6**（因为 paddleocr 依赖 `scipy/scikit-image/pandas`，
  它们的最新版要求 numpy>=2），并额外装进了 `opencv-python-headless 5.1.0.95`。
  结果：`import cv2` 与 `from paddleocr import PaddleOCR` 双双报
  `compiled against ABI version 0x1000009 but this version of numpy is 0x2000000`。
- **备选**：① 跟着 numpy 2 走，把 opencv 升到 ≥4.10（但 paddleocr 2.7.3 硬 pin `opencv<=4.6.0.66`）
  ② 压住 numpy 1.x，同时**把三个"间接顶包"钉到 numpy 1.x 兼容线** ③ 前后端各用一套 Python 环境隔离
- **决策**：②（③ 记为后续可选项：把 OCR 放进独立 worker 镜像）
- **理由**：paddlepaddle 2.6 与 opencv 4.6 都是用 numpy 1.x 编译的，跟着 numpy 2 走等于同时放弃两者；
  而 paddleocr 2.7.3 是"必须锁 3.x 不兼容"的既定选型（AGENTS §2.2）。压 numpy 是唯一自洽的解。
  教训是：**锁住直接依赖不够，还要锁住会把关键传递依赖顶走的那些包**。
- **代价**：`scipy==1.11.4` / `scikit-image==0.21.0` / `pandas==2.1.4` 三个包也被钉住（略旧）；
  另需 `opencv-python` 与 `opencv-contrib-python` 同时 4.6.0.66（paddleocr 两个都要），并确保没有 headless 版本混入。
- **验证**：`import cv2, paddle, paddleocr` 全部成功；`pytest` 72 passed；`scripts/ocr_demo.py` 单张 41 行、
  平均置信度 0.99、两次结果完全一致。

---

## D-028 票种识别用"关键字加权 + 长宽比先验"，不上分类器

- **日期**：2026-11-19（S1-4）
- **背景**：要判 5 类票种（数电票 / 旧版增值税电子普通发票 / 铁路客票 / 行程单 / 出租车票）。
- **备选**：① 微调一个图像分类器 ② 传统图像特征 + 聚类 ③ 关键字加权 + 长宽比 + 互斥词
- **决策**：③
- **理由**：票据**标题本身就是最强的版式信号**（"铁路电子客票""航空运输电子客票行程单"几乎逐字命中）；
  规则可读、可调、可解释，答辩时能说清"为什么判成这个票种"（命中哪些词、各占多少权重）。
  训练集只有 60 张，分类器必然过拟合，且无法解释误判原因。
- **代价**：依赖 OCR 把标题读对——所以这轮把"标题被裁掉"的预处理缺陷一并修了（见 D-029）。
- **验证**：60 张判对率 **100%**（60/60，unknown 0）；13 条单测覆盖五类 + 未知 + 真歧义。

---

## D-029 四角点必须过四项几何校验（退化/自交/单边裁切/越界）

- **日期**：2026-11-19（S1-4 期间发现，属 S1-2 的修正）
- **背景**：票种识别依赖标题文本，于是暴露了 S1-2 的几何缺陷——**"warp 成功"不等于"warp 正确"**：
  1. 一张行程单的四角点被排成 `(0,497)(1199,0)(1199,699)(0,498)`：左下两点只差 1px，形状其实是三角形，
     面积 50%、内角正常，**面积/角度阈值全部通过**，warp 后 OCR 从 20 行掉到 **0 行**；
  2. 竖屏截图被裁成 611×1919（宽度只剩 57%、高度满格），标题整行被裁掉，OCR 39 → 21 行；
  3. 四角点越界时 warp 会把边缘复制进来并把内容拉伸（输出 1079×2118 比原图 1080×1920 还高）。
- **决策**：`_accept_quad` 增加四项校验，任一不过即降级到 `minAreaRect`（只修旋转、不裁内容）：
  ① **自交**：四个连续叉积必须同号；② **退化**：最小边 ≥ max(12px, 12% 最长边)；
  ③ **对边均衡**：对边长度差 ≤ 60%；④ **裁切可疑**：一个方向 >85% 而另一个 <65%；⑤ **越界**：四角点不得超出画面 2%。
- **代价**：S1-2 的"几何校正成功率"从 85% 掉到 65%（21 张走 `minAreaRect`）——**这是好事**，
  原来的 85% 里混着一批"warp 成功但把内容裁坏"的样本；票种判对率与 OCR 行数同时上升。
- **验证**：上述三张样本 OCR 行数恢复到 20/39/41；票种判对率 91.7% → **100%**；
  `pytest` 85 passed；预处理耗时 P50 77ms / P95 195ms（检测改在 1000px 小图做 + 重采样用双线性）。

---

## D-030 字段抽取用"关键字锚定 + 显式规则"，冲突一律不静默替换

- **日期**：2026-11-19（S1-5）
- **背景**：要从 OCR 行里抽 12 个字段。票据版式固定、标签明确，但同一字段在不同票种里
  叫法不同（"价税合计/合计金额/小写"），且表头里还有同名列（"金额""名称"）。
- **备选**：① LLM 直接抽字段 ② 端到端信息抽取模型（LayoutLM） ③ 关键字锚定 + 坐标/规则
- **决策**：③
- **理由**：① 成本高、结果不可复现，而 S6 要做可重复的规则基线；② 训练数据只有 60 张，
  LayoutLM 微调必过拟合，且无法解释"为什么这个字段取这个值"；③ 规则可读、可解释、可精确定位失败原因
  （是锚点没命中、还是取到了表头、还是值被印章遮挡）。
- **关键约束**：**冲突不静默替换**——同一字段出现多个候选值时保留 `candidates` 并记 conflict，
  由前端让用户选择（AGENTS §10.3 与前端核对页的要求一致）。
- **代价**：规则维护成本（每条都要写 zone/exclude）；新票种要补模板。
- **验证**：60 张总体字段级准确率 **85.6%**（368/430），check_code 100%、invoice_code/date 96.7%、
  invoice_no 95%；短板 amount 60% / tax 56.7%（竖屏截图里合计行被拆行+印章遮挡），已记入 PROGRESS 遗留。

---

## D-031 检测指标改用"覆盖匹配"，且真值保持元素级

- **日期**：2026-11-19（S1-6）
- **背景**：`docs/EVAL.md` §2 写的是"预测框与标注框按 IoU≥0.5 一对一匹配"。
  实测照这个口径跑：Hmean 只有 **0.26**。排查发现两个原因叠加：
  ① 标注是**元素级**（一行的 4 个单元格是 4 个框），引擎输出是**行级**（1 个框）；
  ② 标注在原图坐标、OCR 在校正图坐标，**坐标系都没对齐**（Hmean 0.09）。
- **备选**：① 把标注合并成行再算纯 IoU（实测 Hmean 0.12，更差：合并规则本身引入误差）
  ② 保持元素级真值 + 覆盖匹配 ③ 放宽 IoU 阈值到 0.3（治标不治本）
- **决策**：② + 坐标系对齐
- **理由**：覆盖匹配（标注被某预测框 IoU≥0.5 或 85% 面积包含即命中；预测框需覆盖某标注 50% 才不算误检）
  正是为"粒度不一致"设计的，也不会放松标准——凭空多出来的框照样算误检。
- **代价**：与通用检测论文的"纯 IoU 匹配"口径不同，**论文里必须写明**；跨系统比较时要标注这一点。
- **验证**：60 张 Hmean 0.948（P 0.967 / R 0.930），与人工抽查观感一致。

---

## D-032 排除超市购物小票：它不是合规报销凭证

- **日期**：2026-11-19
- **背景**：唯一一张真实样片（R-001）经本人确认是**超市购物小票**。
- **判断**：购物小票没有发票代码/发票号码、没有税务监制章，**不属于报销合规凭证**，
  学院财务与《发票管理办法》口径下都不能单独作为报销凭据。
- **决策**：① 该样本**移出数据集**（`samples/MANIFEST.md` 与 `docs/EVAL.md` 均已登记移除），
  ② **不做**该版式的模板、字段规则与评测，③ 只保留其**像素统计**作为"真实成像工况"的固定参照
  （灰度均值 129 / σ 64 / 红偏 33.5% / 纸面约 30%），因为这组数字只描述拍摄条件，与票种无关。
- **理由**：把不合规凭证纳入识别范围会让论文的研究边界变模糊，也会误导系统使用者；
  本项目只处理**五类合规票据**（数电票 / 旧版增值税电子普通发票 / 铁路电子客票 / 航空行程单 / 出租车票）。
- **代价**：真实样本从 2 份减为 1 份（剩数电票 PDF），"真实样本定性对照"的说服力更依赖合成集；
  论文需如实写"以合成集为主 + 1 份真实数电票作定性校验"。

---

## D-033 金额/税额缺失时用"合计约束"反解（显式推导，不静默补值）

- **日期**：2026-11-19（应"把 amount/tax 做到 0.8 以上"的要求）
- **背景**：S1-5 的字段级准确率里 `amount 60.0%`、`tax_amount 56.7%` 是最大短板。
  诊断发现：旧版票的**竖屏截图没有"合计"这一行**（只有"价税合计（大写）"），金额与税额散落在
  相邻几行，锚点法取不到 → 10 张样本直接把这两个字段判漏。
- **备选**：① 继续调锚点/正则（试过，这些图里根本没有该标签）
  ② 用"邻近两个金额之和等于合计"反解 ③ 交给 S3-3 的局部二次识别
- **决策**：②（③ 作为后续补强）
- **约束（防止变成"猜数"）**：
  ① 两个候选必须落在合计行 ±5% 图高的窗口内；② 两者之和与合计值相差 ≤ 0.01；
  ③ 较大的当金额、较小的当税额（税额不会大于金额）；④ **解不唯一就不补**；
  ⑤ 补出来的字段 `rule_id="paired:sum:*"`、置信度 0.6、并写 warning —— 前端可标"推导值"，用户可改。
  另外保留"三缺一"的互补推导（`derived:*`，置信度 0.55），同样带标志。
- **实测**：amount 60.0% → **86.7%**，tax_amount 56.7% → **83.3%**，总体字段级 85.6% → **89.3%**。
- **代价**：推导值依赖合计正确（total_amount 76.7% 仍是瓶颈）；推导值不能作为审计原始依据，
  所以置信度降档、并且 `bbox=None`（前端不会给推导值画框）。

---

## D-034 残留倾斜：不把去斜阈值调小（精度优先）

- **日期**：2026-11-19
- **背景**：用户抽查对照图反馈"有些校正后仍然歪"。分析：`_deskew` 只在残差 >1.5° 时旋转。
- **试过**：把阈值降到 **1.0°** 并让 `minAreaRect` 兜底路径也去斜。
- **结果**：字段准确率**下降 3–5 个点**（buyer_tax_no 86.7%→83.3%、total_amount 76.7%→71.7%），
  因为多一次小角度重采样会把 2–3px 的细笔画糊掉，税号/金额最先受损。
- **决策**：**回退**为 1.5° 且只对透视路径做残差校正。
- **理由**：用户的实际判据是"字都看得清"，≤1.5° 的倾斜不影响识别与阅读；
  而为 1° 的视觉改善牺牲 5 个点的字段准确率不划算。若后续需要更正的版面，
  应该在 S3-3 用"倾斜感知"的坐标变换解决，而不是在像素层重采样。

---

## D-035 输入分流：PDF 不做预处理；照片走"引导 + 重拍闸门"；透视加安全边距

- **日期**：2026-11-19（用户提出：电子发票 PDF 格式本来就没问题，只有人拍的照片需要处理；
  拍不清就该让用户重拍）
- **决策与理由**：

| 输入 | 处理 | 为什么 |
|---|---|---|
| **PDF 电子发票** | 只渲染成图（PyMuPDF，250 DPI），**跳过 S1-2 全部步骤** | 电子件端正、无背景、无阴影；S1-6 实测预处理让检测 Hmean 从 0.974 掉到 0.948，对它是纯损失 |
| **拍照件** | 跑预处理 + **拍前引导** + **拍后重拍判断** | 照片的问题（歪、糊、暗、没拍全）只有预处理能救；救不了的必须让用户重拍，而不是硬识别出一堆错字段 |

- **"重拍闸门"的边界**：`needs_retake` 只是**建议**，不阻断流水线——用户坚持用这张也能继续，
  低置信字段由 S3 的人工核对兜底。触发条件：模糊 / 过暗 / 低对比 / 过曝 / 认不出四边 / 边缘贴画面
  （最后一条只在真的做过裁切时生效，"整幅就是票面"的样本不提示，避免误伤）。
- **透视安全边距（2%）**：四角点比真实纸面小 4% 就会切掉贴边的字（实测 1200 宽只剩 1101）。
  宁可多留一点桌面背景，也不切字——多出的背景对下游无害。
- **四角点校验收紧**：对边长度差 60% → **30%**、内角 [50,130] → **[55,125]**。
  同一张行程单原被检成"上下边 685 vs 1106"的错切四边形（warp 后整页歪 + 边缘文字被切），
  收紧后落到 `minAreaRect` 兜底，输出与原图同尺寸、零裁切。
- **验证**：`flight_itinerary_landscape_007` 从 1200×700→1101×698（裁切+歪）变为 **1200×700→1200×700**；
  全域指标同步改善：字段级 0.893→**0.895**、整单 0.633→**0.650**、检测 Hmean 0.948→**0.951**；
  `pytest 164 passed`（新增 8 条 intake/重拍单测）。

---

## D-036 ORM 用同步 SQLAlchemy 2.0 + psycopg3 方言映射（不引入 async ORM）

- **日期**：2026-11-19（S2-1 引入 ORM 时定）
- **背景**：S2-1 要把 16 张表落成 SQLAlchemy 模型与 Alembic 迁移。项目已装 `psycopg==3.3.9`，
  而 `backend/.env` 与 `tests/conftest.py` 里的 `DATABASE_URL` 是 `postgresql://`（`/health` 用 psycopg 直连，需要这个形态）。
  SQLAlchemy 默认把 `postgresql://` 解释成 **psycopg2**（未安装）。
- **备选**：① 同步 SQLAlchemy + psycopg3；② async SQLAlchemy（`create_async_engine` + `async_sessionmaker`）；
  ③ 新装 `psycopg2-binary` 沿用默认方言。
- **决策**：①。`database_url` 保持 `postgresql://` 不动，只在 `core/database.py::to_sqlalchemy_url()`
  映射成 `postgresql+psycopg://`；引擎关闭时 `pool_pre_ping=True`。
- **理由**：
  1. S1 的预处理 / 识别 / 评测脚本，以及 S2-7 起的 ARQ worker handler 都是同步代码；
     async ORM 会逼出"脚本用同步、API 用异步"两套会话，事务语义容易分叉；
  2. FastAPI 的 `def` 路由自动走线程池，同步会话不会阻塞事件循环；
  3. 不新增驱动依赖，`/health`、`conftest`、`.env` 三处都不用改；
  4. 少一层 greenlet，测试与排错简单。
- **代价**：高并发下受线程池上限约束（本课题单机演示规模可控）；若将来要上 async，
  模型层完全复用，只需改 `core/database.py` 与路由风格。
- **验证**：`tests/test_database.py` 4 条（三种 URL 形态 + 引擎方言/预探活）；
  `alembic upgrade head` 实跑成功；`pytest 264 passed`。

---

## D-037 迁移以模型为唯一来源，`alembic check` 零差异为门槛；JSONB 不设 server_default

- **日期**：2026-11-19（S2-1）
- **背景**：部分唯一索引（`postgresql_where`）、GIN、`DESC` 表达式索引、CHECK 表达式都不是
  autogenerate 的强项；同时任务卡提示词要求"JSONB 用 `server_default='{}'::jsonb'`"，
  但 `SCHEMA.md` 对 JSONB 列没有定义默认值（默认值列均为 `—`）。
- **决策**：
  1. **模型是唯一来源**：约束/索引全部声明在 `app/models/*`，`enums.py` 生成 CHECK 文本；
     `alembic revision --autogenerate` 只用于起草 `0001_init.py`，随后逐条对照 SCHEMA 人工核对；
  2. **门槛是 `alembic check` 零差异**（"No new upgrade operations detected"），
     再用集成测试从 `pg_constraint` / `pg_indexes` 回读约束与谓词，防止"迁移里漏了、库里没拦住"；
  3. **JSONB 不设 server_default**（契约优先）：可空 JSONB 列缺省为 NULL，
     `ticket_templates.field_specs_json` 保持 `NOT NULL` 且必须显式给值；
     `report_forms.canonical_json` 保持 `TEXT`，不得优化成 JSONB。
- **理由**：迁移是"生成当时的快照"，模型是持续演进的契约；用 `alembic check` 把两者钉在一起，
  才能保证"库里看到的约束就是模型声明的约束"。给 JSONB 补 `'{}'` 会让"没有值"与"空对象"混淆，
  且契约没要求——按 AGENTS §9 以 `SCHEMA.md` 为准，不擅自加。
- **代价**：改模型必须同时生成新迁移并重跑 `alembic check`；集成测试依赖本地 Docker PG，
  默认 `pytest` 不跑（`-m integration` 单独跑）。
- **验证**：`alembic upgrade → downgrade base → upgrade` 往返成功；
  `alembic check` 无差异；集成 23 条（16 表结构、枚举 CHECK 回读、5 条部分唯一索引、
  `verified/verify_result` 一致性、密文/金额/十六进制格式、identity、外键等）。

---

## D-038 认证实现：PyJWT + passlib Argon2id（HS256；m=64MiB / t=3 / p=4）

- **日期**：2026-11-19（S2-2）
- **背景**：S2-2 要落地登录会话。需要两个选型：JWT 库、口令哈希库与参数。
- **备选**：
  - JWT：① PyJWT；② python-jose；③ fastapi-users（自带整套用户体系）。
  - 口令：① passlib 的 `argon2` 方案（Argon2id）；② bcrypt；③ pwdlib。
- **决策**：**PyJWT 2.15.1** + **passlib 1.7.4**（Argon2id，`m=65536 KiB, t=3, p=4`，`type=ID`），
  HS256（`algorithms=["HS256"]` 显式白名单），`jti` 用 `secrets`，argon2-cffi 钉 25.1.0。
- **理由**：
  1. PyJWT 维护活跃、FastAPI 官方安全示例同款；python-jose 更新停滞；fastapi-users 会接管
     用户表与路由，和 S2-1 已定稿的 `users` 契约冲突。
  2. AGENTS §2 明确口令哈希用 Argon2id（passlib）；pwdlib 虽是更现代的选择，但不在项目技术栈里。
  3. 参数高于 OWASP 最低线（m=19MiB、t=2、p=1）：64MiB 让 GPU/ASIC 暴力破解成本显著上升；
     `type=ID` 是 Argon2id（同时抗侧信道与 GPU）。
  4. 显式 `algorithms` 白名单 + 必填声明（`exp/iat/sub/jti/typ`），防 `alg=none` 与算法混淆。
- **实测（本机 CPU，单次）**：hash **59.4 ms**、verify **45.7 ms**；参数串 `m=65536,t=3,p=4`。
  **代价**：每次登录约 50ms、瞬时内存 64MiB——并发登录时线程池内存会上来，是隐藏瓶颈；
  如果将来压测不过，优先降到 m=32MiB（而不是换算法）。
- **验证**：`tests/test_security.py` 24 条（哈希/校验/过期/篡改/alg=none/类型混用）、
  `tests/test_auth.py` 28 条（注册/登录/me/刷新/登出/RBAC）；手工 uvicorn + 真实 Redis 链路通过。

---

## D-039 登出只吊销 refresh：access token 在剩余 TTL 内仍可用

- **日期**：2026-11-19（S2-2）
- **背景**：JWT 是无状态的，`logout` 若只删 Redis 里的 refresh 白名单，已签发的 access token 仍然有效。
- **备选**：① access 黑名单（每请求查 Redis）；② 短 TTL + 只吊销 refresh；③ 服务端会话表。
- **决策**：②。access 30min、refresh 7d；refresh 轮换 + Redis 白名单；不做 access 黑名单。
- **理由**：
  1. 每请求查 Redis 会把认证变成有状态：Redis 抖动 = 全站 401，而 Redis 在本项目里还承担队列；
  2. 30 分钟的残留窗口对校园报销系统可接受（不是支付/即时通讯）；
  3. refresh 被窃取后只能"抢用一次"（轮换），要长期冒充必须持续持有 refresh 并抢在用户前刷新，
     这个风险用"登出必须真的吊销 refresh"已经压到很低。
- **代价**：登出后旧 access 最多还能用 30 分钟；论文"不足与展望"里如实写，
  并说明若要即时失效需引入黑名单/会话表及其代价。
- **验证**：`test_logout_revokes_refresh_token`（登出后 refresh 401）与
  `test_access_token_survives_logout_within_ttl`（钉住 200，防止误以为已失效）。

---

## D-040 `audit_logs.action` 增加 `login_failed` / `logout` / `forbidden`（迁移 0002）

- **日期**：2026-11-19（S2-2，用户拍板）
- **背景**：API.md §0/§1 要求"登录失败计入审计 `login_failed`、登出写 `logout`、资源级越权写 `forbidden`"，
  但 SCHEMA §9.19b 的 `action` 枚举只有 9 个值（upload/confirm_field/sign/seal/submit/verify/decrypt/review/revoke_cert）；
  同时审计哈希链要到 S4-7 才实现，现在无法计算 `entry_hash`。
- **决策**：
  1. SCHEMA §9.19b 增加三个值，生成 `0002` 迁移（只放开 CHECK，**不写任何审计行**）；
  2. S2-2 的登录失败/登出只写带 `trace_id` 的应用日志（`username_sha256` 脱敏），审计行留给 S4-7；
  3. API.md 另外提到的 `template_create` / `report_create` / `issue_cert` 仍缺枚举值，
     在对应任务（S2-8 / S5-1 / S4-6）开始前按同样流程补 SCHEMA + 迁移。
- **理由**：契约必须一致（否则 S4-7 写审计时才发现库拦不住）；但**不能写 `entry_hash` 造假的审计行**——
  那会破坏哈希链的"改一行，后面全对不上"这一核心不变式（AGENTS §7 禁止假数据）。
- **代价**：`0002` 的 `downgrade` 在有新 action 行时**显式拒绝**（审计不可删）；三个待补 action 还会再产生一次迁移。
- **验证**：`pg_get_constraintdef` 回读 12 个值；`tests/test_migration.py::test_enum_checks_in_database_match_code`
  断言库内 CHECK 与 `enums.py` 完全一致；`alembic upgrade/downgrade` 往返通过。

---

## D-041 行级隔离用应用层统一过滤，不用 PostgreSQL RLS

- **日期**：2026-11-19（S2-3；任务卡要求记录。SCHEMA 定稿 #17 引用的是 D-016，但 D-016 实际只写了会话形状与口令托管，没有 RLS 论证，本条补上）
- **背景**：权限矩阵要求 `submitter` 只看本人、`reviewer` 看全量、`admin` 对业务表一律 404。
  可选：① 应用层统一 `filter_visible`；② PostgreSQL RLS（`SET LOCAL app.user_id` + policy）；③ 每角色一套视图。
- **决策**：①，RLS 列为"可加固项/展望"。
- **理由**：
  1. **连接池 + `SET LOCAL` 的坑**：RLS 靠会话级变量表达"当前用户"。连接池复用连接时，
     必须保证每个事务都 `SET LOCAL` 且事务边界与请求边界严格一致；一旦用了 `SET`（非 `LOCAL`）
     或在事务外设置，**下一个请求会串用上一个人的身份**——比"忘写 where"更隐蔽的越权。
     本项目还有 ARQ worker、初始化脚本、Alembic 三个无请求上下文的入口，RLS 需要额外的
     `BYPASSRLS` 角色与独立连接串，身份传递每多一层就多一个越权入口。
  2. **策略复杂度**：可见性不是"按人一刀切"——reviewer 对报销单只看 `submitted` 之后，
     admin 对业务表连自己的都不看（404）。RLS 要写多条 policy + 角色切换；
     应用层就是一张策略表 + 一个函数（`services/repository.py`），评审时 grep 一次就能确认覆盖面。
  3. **可测试性**：谓词是纯函数，可以在 pytest 里同时断言 SQL 与结果；RLS 的错误往往只在
     真实连接池与并发下才复现，正好是毕设最没有时间排查的一类问题。
  4. **与既有安全边界一致**：业务字段本来就以明文存库，靠 DB 账号权限 + 应用层过滤
     （SCHEMA §8 已写明"不是服务端零知识"）；RLS 不会改变"DBA 直连可读"的残余风险。
- **代价**：所有读查询必须走 `repository.py`，漏用不会被数据库拦住；deny-by-default 只保护
  未登记模型，已登记模型仍靠代码纪律 + 每个读接口的"他人数据 404"测试兜底。
  论文"不足"里如实写：应用层过滤的正确性依赖实现纪律，可加固为 RLS，但要付出上述连接池成本。
- **验证**：`tests/test_repository.py` 9 条（三角色 × 三模型谓词、未登记模型默认拒绝、
  过滤不覆盖调用方条件）；`tests/test_isolation.py` 23 条（角色 × 模型、报销单 8 状态、
  列表/详情一致性、HTTP 404/40400）。

---

## D-042 列表分页：显式 join 而不是 relationship；count 与数据共用同一个 Select；keyset 暂不做

- **日期**：2026-11-19（S2-4）
- **背景**：列表接口要统一分页/排序协议。任务卡提示词建议 `selectinload/joinedload`，
  但 S2-1 决定模型层**不写 relationship**（避免懒加载与隐式 JOIN），且列表需要的关联字段是明确的一列（文件名/哈希）。
- **决策**：
  1. **保持无 relationship**：列表用 `select(Invoice).join(InvoiceFile, ...).add_columns(...)` +
     `paginate(..., scalar=False)` 一次取全；测试用 `before_cursor_execute` 统计每页 **2 条 SELECT**
     （count + 数据），断言不随行数增长。将来若 S2-5 需要 ORM 关系，再单独评审并改锚点，不混进分页层。
  2. **count 与数据共用同一 `Select`**：`select(count).select_from(stmt.order_by(None).subquery())`，
     从根上消除"列表一套 where、total 另一套"的经典 bug。
  3. **稳定排序**：白名单列 + `id asc` 兜底；同秒 `created_at` 翻页不重复、不漏行。
  4. **`q` 安全**：`%` / `_` / `\` 先转义再 `ILIKE ... ESCAPE '\'`，用户输入不会变成通配符。
  5. **keyset/游标分页暂不做**：5 万行下深页（page=1000）p95 25.4ms，offset 足够；
     等 S2-5/S5 确有深页或更大规模需求再评估（届时只改分页层，端点不用动）。
- **备选**：给 `Invoice` 加 `file/template` relationship 并用 `selectinload`。好处是 ORM 写法顺手，
  代价是改 S2-1 锚点、多一层懒加载风险，且列表字段明确、join 已经够用——收益不抵风险。
- **代价**：端点需要自己写 join + 字段映射（比 relationship 多几行）；`q` 目前是 `ILIKE` 子串，
  没有 trigram/GIN 索引（5 万行 p95 32.3ms 可接受，真实规模再评估）。
- **验证**：`tests/test_pagination.py` 13 单元 + 13 集成（翻页并集/超末页、count 同条件、
  排序方向、可见性组合、2 条 SQL 的 N+1 断言、HTTP 422 边界）；
  `scripts/bench_list.py --rows 50000 --iterations 50`：四组 p95 = **15.9 / 25.4 / 21.9 / 32.3 ms**，
  `EXPLAIN ANALYZE` 走 `Index Scan using ix_invoices_uploader_created`。

---

## D-043 票据 CRUD：状态机按 D-011、建票不设 HTTP 口、字段修改提前按契约实现

- **日期**：2026-11-19（S2-5）
- **背景**：任务卡 S2-5 与契约有三处不一致：
  ① 状态图写的是 `draft → ocr_pending → ocr_done → confirmed → signed → submitted → approved|rejected → archived`；
  ② 要求 `POST /invoices` 建草稿、`PATCH /invoices/{id}` 修改，但 API.md 没有这两条；
  ③ 要求 `PUT /invoices/{id}/fields`，API.md 是 `PATCH /invoices/{id}/fields`（标注 S3-6）。
- **决策**：
  1. **状态机以 D-011 / SCHEMA / API.md 为准**：`invoices.status` 只有
     `draft / confirmed / in_report / archived / void`；`ocr_pending/ocr_done/signed/submitted/approved/rejected`
     分别是 `ocr_status` 与 `report_forms.status` 的值，**不混入**。`transition()` 是唯一迁移入口，
     非法迁移 409 并带当前状态；`ocr_status` 由 Worker（S3-1）单独维护。
  2. **建票不新增 HTTP 接口**：契约里建票是 `POST /invoices/upload`（S2-6，含 sha256 去重/信封/MinIO）。
     S2-5 只提供内部 `create_draft()`，S2-6 上传成功后调用；不在契约外造 `POST /invoices`。
  3. **字段修改按契约的 `PATCH /invoices/{id}/fields` 实现**（任务卡的 `PUT /{id}/fields` 视为旧稿）。
     行为（乐观锁、D-017 权限矩阵、`confirm_amount`、42201/42202、`source=manual`）在 S2-5 落地，
     S3-6 只在此基础上补整单复核与审计；不新增 `PATCH /invoices/{id}`。
  4. **admin 列表 404 在入口判断**：仓库层对 admin 给空谓词（安全默认），但契约要求 404，
     由 `invoice_service.list_invoices` 在查询前拒绝，不把"空列表"当"无权限"。
  5. **42201 的语义**：字段修改后若交叉校验仍有未决冲突，**整次修改回滚**（不半提交），
     返回 42201；成功时 `conflicts` 必为空。较严，但符合契约字面；S3-6 若要放宽须先改 API.md。
- **理由**：契约是前后端唯一依据（AGENTS §9）；状态机混值会让前端、报表与审计同时错位；
  内部 factory 既满足 S2-6 的调用需要，又不制造一个"上传之外还能凭空建票"的入口（安全面更小）。
- **代价**：S2-5 的验收链路只能通过 TestClient 走"内部建单 → PATCH → 详情"，真正的 HTTP 建单要等 S2-6；
  字段修改与 S3-6 有重叠，S3-6 必须复用本卡实现而不是重写。
- **验证**：`tests/test_invoices.py` 31 条（三权限列表/筛选/分页、详情 fields/latest_job、软删幂等与 409、
  void 释放唯一索引、状态机合法路径/非法跳转/版本冲突、字段修改 200/403/42202/409/42200/42201 与回滚、
  确认标记、建单→改字段→查详情链路）；`pytest 310 passed`、`pytest -m integration 118 passed`。

---

## D-044 S2-6 上传：S4 前走本地明文调试、对象名按 SCHEMA、ARQ 入队留 S2-7

- **日期**：2026-11-19（S2-6，用户拍板方案 A）
- **背景**：契约要求"内存中信封加密后写 MinIO"，但信封/SM4 在 S4-3/S4-4；SCHEMA §3.1 允许
  `is_cipher=false` 仅出现在本地调试配置。另：任务卡与契约在路径（`/{id}/files` vs `/invoices/upload`）、
  大小（10MB vs D-018 的 20MB）、类型（含 TIFF vs 仅 jpeg/png/pdf）、对象名（sha 前缀 vs SCHEMA 的 uuid）上冲突。
- **决策**：
  1. **S4 前允许本地明文调试**：`storage_allow_plaintext` 默认 true，上传行 `is_cipher=false`；
     `environment='prod'` 时该配置为 true 直接 `ConfigError`（启动即失败，不会把调试路径带上线）；
     每次明文上传打一条 warning；S4 接入信封后默认切密文并删除该开关。
  2. **对象名按 SCHEMA §3.1**：`invoices/{yyyy}/{mm}/{secrets.token_hex(16)}.bin`；
     不用原文件名（路径穿越/中文/重名），也不用 sha 前缀（避免对象名泄露票据哈希）。
  3. **接口与限制按契约**：`POST /invoices/upload`、20MB（D-018）、仅 jpeg/png/pdf（TIFF 拒绝）、
     PDF ≤5 页、魔数 + Pillow/pypdf 双校验；去重**先于**写对象（命中 409/40901 + `data.invoice_id`）。
  4. **落库补偿**：`storage.put` 成功后 DB 失败（含并发同 sha 的 `IntegrityError`）必须 `storage.delete`；
     删除失败只告警，不掩盖原始异常。
  5. **ARQ 留 S2-7**：本卡只写 `ocr_jobs(stage=preprocess,status=pending)` 并返回 `job_id`；
     S2-7 在事务提交后入队，上传接口形状不变。
  6. **新依赖 `python-multipart==0.0.34`**：FastAPI 解析 `multipart/form-data` 的硬依赖（不装则路由无法注册）。
- **理由**：方案 B 会让 S2-6/S2-10/S3 的上传链路全部停摆；方案 C 打乱 S4 边界。本地方案把风险限定在
  "本机 Docker 卷中的明文票据"，并用 prod 启动校验兜底；论文"范围限定"如实写。
- **代价**：S4 前本地 MinIO 中是明文；`is_cipher=false` 是临时状态，S4 必须改回 true 并补迁移/测试；
  任务卡的 TIFF 与 sha 对象名不采用。
- **验证**：`tests/test_upload.py` 11 条（成功/去重/空文件/超限/假图/TIFF/PDF 页数/权限/补偿/503/文件名清洗）；
  真实 uvicorn + MinIO 手工：202 → 二次 409 + 同一 `invoice_id`、MinIO 只有 1 个对象、
  `is_cipher=false`、对象与测试数据已清理；`pytest 311 passed`、`pytest -m integration 129 passed`。

---

## D-045 ARQ 作业框架：redis 客户端随 ARQ 降级；重试/幂等/僵尸恢复的落地口径

- **日期**：2026-11-19（S2-7）
- **背景**：要落地 ARQ 投递/重试/幂等。任务卡要求写 `attempts/started_at/finished_at` 与 `low_confidence`，
  但 `ocr_jobs` 只有 `retry_count / created_at / elapsed_ms`、状态只有 `pending/running/succeeded/failed`（SCHEMA §3.5/§9.9）。
- **决策**：
  1. **依赖**：`arq==0.28.0`。它硬性要求 `redis<6`，因此 redis-py 从 **8.1.0 降到 5.3.1**（并引入 hiredis），
     Redis 7 服务端不变；`/health`、refresh 白名单、脚本用法全部用 5.3 兼容 API，全量回归通过。
  2. **不改表**：尝试次数用 `retry_count`（1=第一次尝试），耗时用 `elapsed_ms`，不新增 `attempts/started_at/finished_at`；
     任务卡的 `low_confidence` 不进入作业状态（它是字段置信度/`extract_status` 的事）。
  3. **幂等双保险**：执行前先看终态（`succeeded/failed` 直接跳过），再用 Redis `SET job:lock:{id} 1 NX EX 600`
     防并发/重复投递；锁在每次尝试结束时释放，重试才能重新拿到。
  4. **重试**：`max_tries=3`；只有网络/临时 IO/内存类（`RetryableTaskError`、`ConnectionError`、`TimeoutError`、
     `OSError`、`MemoryError`、`ServiceUnavailableError`）重试；参数/逻辑错误直接 `failed`。
     退避表 `(5, 30, 120)`：第 1 次失败后 5s、第 2 次后 30s，第 3 次失败即终态（120s 留给后续调大 `max_tries`）。
  5. **僵尸恢复**：worker `on_startup` 把 `status='running'` 且 `created_at` 超过 **30min** 的作业置 `failed`。
     `ocr_jobs` 没有 `started_at`，只能用 `created_at` 做保守近似；论文里如实写这个局限。
  6. **`ocr_task` 在 S3-1 前如实失败**（`TaskNotReadyError`），不伪造 `succeeded`；S3-1 只替换 `_run_pipeline`。
  7. **入队失败**：上传已落库时把 job 置 `failed` 并返回 503（不返回假成功）；客户端重试会命中 sha256 去重，
     需要后续提供一个"重跑失败作业"的运维入口（登记为遗留）。
- **理由**：ARQ 是 AGENTS §2 指定的队列；状态列与值以 SCHEMA 为唯一依据；幂等必须同时防"重复投递"与"并发执行"。
- **代价**：redis 客户端降级；`ocr_jobs` 没有 `started_at` 导致僵尸判定是近似值；入队失败的票据需要人工重跑入口。
- **验证**：`tests/test_jobs.py` 12 单元 + 7 集成（成功/终态跳过/锁占用/5s→30s→failed/永久失败/僵尸恢复/入队失败），
  `tests/test_upload.py` 12 条（含入队成功与入队失败）；真实 ARQ worker + uvicorn 手工：上传 202 → worker 取到 job →
  `ocr_jobs` 变 `failed`、`retry_count=1`、错误为"TaskNotReadyError: OCR 流水线未接入（S3-1）"；
  `pytest 323 passed`、`pytest -m integration 137 passed`；smoke 8 OK。

---

## D-046 模板/规则管理：DB 形态、一次性 seed、60s 缓存与 `vat_digital` 补齐

- **日期**：2026-11-19（S2-8，用户拍板方案 A）
- **背景**：S1 的 layout/extractor 以 YAML 为规则源，S2-8 要改成后台可维护、60s 热生效；
  任务卡的模板/规则字段（`name/ticket_type/anchors/region_layout/enabled` 等）与 SCHEMA 不符；
  S1 已有数电票模板但 `TicketType` 缺 `vat_digital`。
- **决策**：
  1. **契约补 `vat_digital`**：SCHEMA §9.6、`models/enums.py` 同步；`0003` 迁移只放开
     `ticket_templates.type_code` 与 `invoices.invoice_type` 两个 CHECK（不写数据）。
  2. **DB 形态**：`ticket_templates` 用 SCHEMA 的 6 列；字段锚定/策略放进 `field_specs_json`
     （`keywords`/`direction`/`regex`/`max_distance`/`zone`/`occurrence`/`required`…），
     `field_rules` 只存 `pattern/validator/error_msg/priority/active`——**不新增列、不按任务卡造字段**。
  3. **规则随模板走**：`POST /templates` 可带 `rules[]`；`PUT` 生成 `version+1` 新行，先复制旧版本规则、
     再按 `field_name` 覆盖；同票种 `active` 互斥（部分唯一索引兜底）。
  4. **seed 用显式脚本** `scripts/seed_templates.py`（幂等，已有模板即跳过），不在 `create_app()` 里连库：
     否则所有单测/健康检查（DB 未起）都会被 seed 拖慢或失败。S2-12/S7 的部署流程负责调用。
  5. **60s 缓存**：`TemplateRegistry` 的 active 快照 TTL 60s；任何写操作立即 `invalidate()`（写后即生效）。
  6. **`/test` OCR 可注入**：生产走 `preprocess + PaddleOCR`；测试 monkeypatch `_recognize_image`，不加载模型。
  7. **顺手修一个真 bug**：`extractor._extract_value` 原来用 `re.findall`，DB 正则一旦带捕获组会返回 tuple
     并抛 `TypeError`；改为 `finditer` + `group(0)`（YAML 无非捕获组时行为不变，S1 测试全绿）。
- **理由**：SCHEMA 是唯一表结构依据；seed 显式化换来测试与启动的确定性；缓存 + 写后失效兼顾"热更新"与一致性。
- **代价**：layout 的 `regions/aspect_ratio` 不在 DB 契约里，仍由 YAML 提供（S3-1 决定组合方式，登记遗留）；
  写审计 `template_create` 的枚举值仍缺，S4-7 前补；`/test` 首次会加载 PaddleOCR（冷启动约 6.5s）。
- **验证**：`tests/test_templates.py` 9 单元 + 9 集成（权限/创建/列表/详情/升版本/active 互斥/缓存失效/
  `/test` 成功与 50010/假图/未启用）；真实 uvicorn 手工：seed 5 个模板、详情 specs=10 rules=10、
  `/test` 真 OCR 抽出 **10 个字段**、matched 5 个关键字、confidence 0.881；reviewer 写 403；
  `pytest 332 passed`、`pytest -m integration 146 passed`、smoke 8 OK。

---

## D-047 前端会话：refresh 单飞 + 守卫水合 + 角色菜单；未实现页面用占位

- **日期**：2026-11-19（S2-9）
- **背景**：S0-6 只有占位登录与顶栏；S2-2 已有 login/refresh/me/logout。任务要求三角色登录后菜单不同、
  token 过期自动续、并发刷新去重、无权限跳 /403。
- **决策**：
  1. **会话存 localStorage**（access + refresh + user 三个 key），沿用 D-021 的取舍；
     XSS 风险仍由"动态内容只走 Vue 插值、禁止 v-html"约束。
  2. **不解析 JWT exp 判断过期**：完全依赖 401；避免前后端时钟偏差。401 时自动 `POST /auth/refresh`
     一次并重放原请求；登录/刷新接口本身不触发刷新。
  3. **并发 refresh 单飞**：模块级 `refreshPromise`，同一时刻所有 401 复用同一个 Promise；
     后端是轮换式 refresh，重复用旧 token 会 401——这是任务卡点名的"刷新风暴"。
     刷新失败才清会话 + 跳登录，提示做 1.5s 节流（并发失败不弹多个）。
  4. **守卫水合**：刷新浏览器只剩 token 时，守卫调 `/auth/me` 补 user；失败交给刷新/登录流程。
  5. **角色菜单与路由**：`submitter` 我的票据/上传；`reviewer` 待核对/待审核；`admin` 模板/规则/证书/审计；
     `meta.roles` 生效，不匹配 → `/403`。前端只是体验，**接口仍独立裁决**。
  6. **未实现页面用 `Placeholder.vue`**：S2-10/S2-11/S5 的页面明确标注"待实现"，不放假数据。
  7. **DEV-only 诊断钩子**：`import.meta.env.DEV` 下暴露 `window.__httpDebug`（`get` + `refreshCount`），
     供 `scripts/ui_smoke.mjs` 验证并发 401 只刷新一次；生产构建分支被摇掉。
- **理由**：单飞是轮换式 refresh 的唯一正确用法；守卫水合保证刷新页面不掉登录；占位页避免"看起来能用"的假象。
- **代价**：localStorage 的 XSS 暴露面（D-021 已接受）；前端无单测框架，refresh 逻辑靠浏览器冒烟 + 代码评审；
  菜单/路由占位需要 S2-10/S2-11/S5 逐个替换。
- **验证**：`npm run typecheck`、`npm run build`；后端（`ACCESS_TOKEN_TTL_SECONDS=1`）+ Vite + 无头 Edge：
  三角色真实登录后菜单分别命中「我的票据」「待核对/待审核」「票种模板/审计日志」；
  admin 访问 `/upload` → `/403`；登录后等 2s 用 `__httpDebug` 并发两个 `/auth/me` →
  `refreshCount === 1` 且两个请求都返回 `fe_sub`；`scripts/smoke.py` 9 OK / 0 FAIL；测试用户与 Redis key 已清理。

---

## D-048 上传页：轮询用详情接口、串行上传、409 绿色提示

- **日期**：2026-11-19（S2-10，用户拍板方案 A）
- **背景**：任务卡要求上传后轮询 `GET /invoices/{id}/ocr-status`，但该接口标注 S3-1/S2-7、尚未实现；
  现有 `GET /invoices/{id}` 已返回 `invoice.ocr_status` 与 `latest_job`。
- **决策**：
  1. **轮询详情接口**（方案 A）：`waitForOcr` 读 `ocr_status`/`latest_job.status`；3s 起指数退避、
     上限 15s、总 120s；到点停止并提示"识别时间较长"。S3-1 落地 `ocr-status` 后只换 `api/invoices.ts` 一个函数。
  2. **串行上传**：一次最多 20 个，逐个发（避免同时压 20 个识别任务）；单文件进度 + 总进度。
  3. **409 是绿色提示**：重复影像是正常业务，行内"该票据已上传"+ 查看链接；上传请求用 `silent: true`
     让 http 不弹全局红条，其他错误也在行内展示。
  4. **上限 20MB**（D-018 契约；任务卡写的 10MB 过时）。前端常量从 `VITE_MAX_UPLOAD_MB` 读，默认 20。
  5. **批量不自动跳详情**：任务卡说"上传完成跳 /invoices/{id}"，但 20 个文件时跳走会丢掉其余进度；
     改为逐行"查看详情"链接（S2-11 落地后可用），单文件场景同样给链接。
  6. **本地校验 + 后端裁决**：前端只查扩展名/MIME/大小；魔数与内容由后端判定（改扩展名会被 422）。
- **修的真实 bug**：`Upload.vue` 最初把原始 task 对象 push 后继续改它，Vue 不追踪 → 页面永远"上传中 0%"；
  改为操作数组里的响应式代理，实测恢复正常。
- **验证**：`npm run typecheck` / `npm run build`；后端 + ARQ worker + Vite + 无头 Edge：
  上传真实 JPEG → 行 "失败 / 识别失败：TaskNotReadyError: OCR 流水线未接入（S3-1）"（S2-7 的真实行为）；
  同文件重传 → "已上传 / 该票据已上传 + 查看链接"（无红色全局错误）；`.txt` → 本地拒绝"只支持 JPG/PNG/PDF"；
  `smoke.py` 9 OK / 0 FAIL；测试用户/票据/MinIO 对象/Redis key 已清理。

---

## D-049 票据列表/详情：URL 作为筛选唯一状态源；影像预览占位；字段编辑规则

- **日期**：2026-11-19（S2-11，预览方案 A）
- **背景**：S2-11 要列表筛选/翻页、详情字段核对与草稿编辑；`GET /invoices/{id}/image` 依赖 S4 信封/解密
  （API 标 S3-5/S5-4），当前未实现。
- **决策**：
  1. **列表筛选以 `route.query` 为唯一状态源**：控件改 query，`watch(route.query)` 统一拉数据；
     刷新/前进后退/分享链接都能还原筛选，不需要两套状态互相同步。
  2. **影像预览用占位**：详情左侧展示文件名/MIME/大小/sha256 前 12 位 + "影像预览待 S3-5（加密信封落地后）"；
     **不**临时加明文下载接口（会绕过 S4 的安全边界），S3-5 落地后替换预览组件。
  3. **字段编辑规则**：仅 `status=draft` 且 `extract_status ∈ {raw, need_confirm}`；submitter 只能改非金额字段；
     reviewer 改金额必须勾选"确认修改金额"（`confirm_amount=true`）；保存带 `version` 乐观锁，
     失败 40900 自动刷新详情并提示，42201 保留用户输入并展示后端原因。
  4. **后端补漏**：`InvoiceListQuery.invoice_type` 的 Literal 漏了 `vat_digital`（S2-8 已加票种），补上并加
     `invoice_type=vat_digital` 列表用例（此前会 422）。
  5. **reviewer 菜单增加"票据查询"**：审核人需要进入全量列表；submitter 仍是"我的票据"。
- **理由**：URL 单一状态源是筛选页最不容易漂移的做法；预览必须等加密链路，不能为了演示开明文后门。
- **代价**：预览暂不可用；字段中文标签/票种文案维护在前端（新增字段需两边同步）；编辑只覆盖字段值，
  不做冲突候选选择（S3-5/S3-6）。
- **验证**：`npm run typecheck` / `npm run build`；后端 `pytest`（含新增 vat_digital 用例）；
  后端 + Vite + 无头 Edge：列表 → 详情 → 编辑 buyer_name → 保存 → 页面刷新可见，
  DB `buyer_name` 更新、`confirmed=true`、`source=manual`、`version 0→1`；reviewer 登录菜单"票据查询"且能看全量；
  `?status=draft&invoice_type=vat_digital` 刷新后 URL 与结果一致；`smoke.py` 9 OK；测试数据/MinIO/Redis 已清理。

---

## D-050 APScheduler 进程内调度 + 按时段 Redis 锁；文档对账测试

- **日期**：2026-11-19（S2-12，方案 A）
- **背景**：S2-12 要把审计链锚定任务挂到 API 启动；APScheduler 每个进程都到点触发，
  多副本/`--workers N` 会重复执行；另外 S2 收口要求 API.md 与实际路由逐条对得上。
- **决策**：
  1. **依赖**：`APScheduler==3.11.1`（4.x 未稳定且 API 不兼容），带 `tzlocal==5.4.5`；
     纯 Python，Python 3.11 可用。
  2. **每进程启动调度器**，任务开始时抢 Redis `SET NX EX`；不做 leader 选举——锁更简单，leader 挂了不用重选。
  3. **成功执行后锁不释放，保留到 TTL**：APScheduler 在每个副本独立触发，占位任务瞬间完成；
     若成功后立即释放，另一个副本几毫秒后就能拿到锁，**同一 tick 会执行两次**（双进程实测必现）。
     锁因此是"本时段已执行"的去重标记；TTL（默认 3600s）必须小于调度周期（默认每日 02:00）。
     失败时释放锁，避免挡后续重试；释放用 Lua 比对 token，防 TTL 过期后误删别人的锁。
  4. **cron 按 `Asia/Shanghai`**（业务日）；cron 时/分/秒、锁 key、TTL 全部进 `Settings`，
     不散落硬编码；`SCHEDULER_ENABLED=false` 时完全不启动（测试默认关）。
  5. **文档对账变成测试**：`tests/test_api_docs.py` 用 `app.openapi()` 与 `API.md` 做归一集合比较，
     以后加路由忘写文档会直接红；API.md 里标注 S3/S4/S5 的未实现接口不参与。
- **理由**：进程内调度 + 每次抢锁是维护成本最低的多副本方案；锁保留到 TTL 才是真正的"一次"语义。
- **代价**：锁 TTL 内同 key 的后续执行会被跳过（生产是每日任务，无影响）；任务失败会立即放锁，
  另一个副本可能在同 tick 补跑一次（失败路径，不产生重复成功）；`anchor_audit_task` 仍是 S4-7 占位。
- **验证**：`pytest` 新增 `test_scheduler.py` 8 条（抢不到跳过/成功后不释放/失败释放/cron/开关/非法配置）+
  `test_api_docs.py` 1 条；双 uvicorn + 同一 Redis、`ANCHOR_CRON_SECOND=*/10`、`ANCHOR_LOCK_TTL_SECONDS=5`：
  4 个 tick 共执行 **4** 次（另一侧 4 次 skipped），而不是 8 次；`alembic check` 零差异；`smoke.py` 9 OK。

---

## D-051 第一版视觉整理：分栏登录、深色侧栏、按角色落地、移除概览/联调菜单

- **日期**：2026-11-19（S2 收口后的用户反馈）
- **背景**：S0-6/S2-9 只按功能验收，登录页只有输入框、登录后先到"概览"（里程碑/当前用户等开发期内容），
  侧栏是 Element Plus 默认白底；任务卡没有单独的美化任务。
- **决策**（有界改造，不改业务逻辑、不加依赖）：
  1. **登录页**改为左右分栏：左侧品牌与三项能力说明，右侧登录卡；窄屏（<900px）隐藏品牌区。
  2. **主框架**改深蓝灰侧栏 + 角色徽标 + 选中态；顶栏显示页面标题与用户菜单。
  3. **移除业务菜单里的"概览""联调自检"**；`/debug` 路由保留但不出现在菜单（联调脚本仍可用）。
  4. **按角色落地**：submitter/reviewer → `/invoices`，admin → `/templates`；根路径 `/` 由守卫重定向，
     `Home.vue` 只做兜底跳转，不再展示里程碑。
  5. 统一 `styles/theme.css`（底色/字体/圆角/表格头/卡片）与各页 page-heading；
     上传页突出拖拽区、列表页筛选成组、详情页预览卡 sticky。
- **理由**：概览/联调是开发期脚手架，对业务用户是噪音；登录页是信任入口，需要品牌与能力说明；
  角色落地减少一次无意义点击。
- **代价**：仍是第一版视觉，没有设计稿与完整设计系统；Element Plus 默认组件只做了轻量覆盖；
  详情工作区（图像+字段并排/框选）仍等 S3-5。
- **验证**：`npm run typecheck` / `npm run build`；无头 Edge（1440×900）三角色登录分别落到
  `/invoices`（alice）、`/invoices`（bob）、`/templates`（root）；`概览=false`；页面无横向溢出
  （scrollWidth=1440）；登录页品牌区可见、表单宽 400px；上传页拖拽区渲染正常。

---

## D-052 管理端票种模板页面：列表 + 详情抽屉 + 升版本编辑 + 试跑

- **日期**：2026-11-19（用户反馈"S2-8 模板功能为什么没有页面"后补）
- **背景**：S2-8 任务卡产出只有后端接口与测试，管理端页面从未排期；此前占位页误标 S2-12，已修正。
- **决策**：
  1. 单页 `/templates`（`TemplateAdmin.vue`）：列表（默认只看生效，可切历史版本）、详情抽屉、
     新建/编辑对话框、试跑 tab；不新增独立 `/rules` 页面，规则随模板维护，`/rules` 重定向到 `/templates`。
  2. 编辑走 **PUT 升版本**：对话框保存后生成 v+1 新行、旧行保留；成功后自动打开新版本详情。
  3. `field_specs_json` 用 JSON 文本编辑（后端是半结构化规格，强行做表单会丢字段）；
     `rules` 用可增删的表格编辑（字段/正则/校验器/优先级/启用）。
  4. 试跑复用 `POST /templates/{id}/test`：只支持 JPG/PNG，不落库、不写对象存储；仅生效版本可试跑。
  5. 新建按钮保留，但后端 `type_code` 受 SCHEMA 六个票种 CHECK 限制，seed 已覆盖六个票种，
     正常运维路径是"升版本"；新建只用于手工清掉某票种后的恢复。
- **理由**：管理端页面是 S2-8 目标的自然延伸；规则嵌套在模板里，避免 YAML/DB/页面三套逻辑。
- **代价**：没有删除模板 API（只能升版本/停用），页面也不提供删除；`field_specs_json` 的 JSON 编辑对非技术用户不友好，
  后续可做字段级表单（需要先约定 strategy 的完整 schema）。
- **验证**：`npm run typecheck` / `npm run build` 通过；root 登录 `/templates` 列出 5 个 seed 模板；
  详情抽屉显示字段规格与规则；编辑对话框可打开；`/rules` 重定向 `/templates`；
  试跑用真实样本 JPEG 走真 OCR，返回命中关键字/整体置信度/字段表（约 6s）；不落库。

---

## D-053 内容页撑满右侧可用宽度：`el-space` 补 width:100%，去掉 1280 上限

- **日期**：2026-11-19（用户反馈"为什么不占满右侧空间"）
- **根因**：页面根节点是 `<el-space fill>`，Element Plus 的 `.el-space` 默认 `inline-flex`，
  宽度按内容收缩；父级 `.main-inner` 已有 1148px，但页面只有 922px，右侧空出 226px。
  另外 `.main-inner`/各页面还有 `max-width: 1280px`，在 2K/4K 屏上仍会左右留白。
- **决策**：`Upload/InvoiceList/InvoiceDetail/TemplateAdmin/Placeholder` 的页面容器统一
  `width: 100%; max-width: none;`；`.main-inner` 改 `width: 100%`，不再限制阅读宽度。
  表单类页面（登录页右栏）仍保持 400px 可读宽度，不强行拉满。
- **验证**：1440px 下 `.main-inner`=1148、`.template-page`=1148、卡片=1148、表格=1106（原 922/922/880）；
  1920px 下 main/page/card=1628、表格=1586；列表/上传页同样撑满（D-053）。

---

## D-054 票种范围锁定：5 类真实票据 + unknown；管理端「新建模板」仅在未来扩票种时开放

- **日期**：2026-11-19（用户确认"5 类够用"）
- **范围**：`vat_general`（旧版增值税电子普票）、`vat_digital`（数电普票）、`train_ticket`、
  `flight_itinerary`、`taxi_ticket` + `unknown` 兜底；**不包含**增值税专用发票/数电专票、汽车票、过路费、
  财政电子票据等。方案明确"不做全票种通吃"。
- **决策**：
  1. 管理端「新建模板」**禁用**并说明原因；日常维护只走「编辑并升版本」（PUT 生成 v+1，旧版本保留）。
  2. 当前 `type_code` 受 SCHEMA CHECK 限制、5 个票种已 seed，POST 新建必然 409（unknown 除外且无业务意义）。
  3. 将来新增票种必须按顺序：SCHEMA 加枚举 + 迁移 → layout YAML/区域与关键词 → 字段规格与规则 →
     样本集 + 评测 → 再开放管理端新建；**不能靠管理端点一下“新建”绕过契约**。
- **代价**：专票/汽车票等真实票据只能走 `unknown` 低置信 + 人工，或在后续版本扩展；这是论文范围取舍。
- **验证**：`typecheck`/`build` 通过；root 打开 `/templates` 显示"当前 5 个票种已全部覆盖"，
  新建按钮 `disabled=true`；编辑升版本路径仍可用。
