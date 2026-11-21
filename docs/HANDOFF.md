# 会话交接（HANDOFF）

> 给"下一个会话"的入口文档。**先读它，再读 `AGENTS.md` 与 `docs/PROGRESS.md`**。
> 最后更新：2026-11-21 18:55（S5-2 完成，阶段 5 进行中；下一步 S5-3）

## 1. 一句话现状

**阶段 0 ~ 阶段 4 + S5-1/S5-2 全部完成**（S0-1 ~ S4-9 + S5-1、S5-2，共 45 张卡）：
识别链路（S1/S3）+ 基础框架（S2）+ 密码模块（S4）+ **报销单建单/明细 + 签名/加密提交（S5-1/S5-2）**均已收口。

**下一步：S5-3 财务端待审列表**（阶段 5 第三张卡，详见 §7）。

S5-2 交付了什么（论文"业务闭环"一章的加密提交段）：

- `POST /reports/{id}/sign`（200）：规范化 → SM2/RSA 签名 → 旧签名置 `void` → `signed`；
- `POST /reports/{id}/submit`（200）：**同一事务**内 验签 → 单据信封 + 每票文件信封 → `submitted` →
  `sign/seal/submit` 审计；影像转密文落 MinIO（明文对象提交后删除）；幂等键重复提交返回既有结果；
- 业务链路统一走 `AlgoProvider`（`ALGO_PROVIDER=sm` 默认 / `rsa` 对照），信封格式 `EnvelopeV1`
  （AAD 绑 `wrapped_key` + `payload_sha256`，D-085）；
- `scripts/register_identity.py` 把证书/密钥**登记**进库（私钥仍只在 `secrets/`）；
  本地库已登记 `alice`(user) 与 `finance`(server) 两套身份。

S5-1 交付了什么（报销业务的第一段，论文"业务闭环"一章的入口）：

- `api/v1/reports.py` + `services/report_service.py` + `schemas/report.py`：
  `POST /reports`（201，`draft`）、`POST /reports/{id}/items`（201，明细 + 重算合计）、
  `GET /reports/{id}`（`{report, signature, envelope, verify_status}`）、`GET /reports/mine`（分页/筛选）；
- 单号 `RPT-YYYYMM-序号` 用 PG 序列 `report_no_seq`（迁移 **0008**，head 已到 0008），
  审计枚举补 `report_create`（建单与审计**同事务**，`verify_chain` 仍 ok）；
- "一张票只能进一张有效单据"：**单据行 + 票据行 `FOR UPDATE`**（不加 SCHEMA 列/约束，D-084）；
- 8 值单据状态机集中在 `report_service.transition()`（S5-2 已调用）。

阶段 4 交付了什么（论文"密码链路"一章的实体）：

- `crypto/canonical.py`：canonical JSON（签名域唯一字节形态）；
- `crypto/sm/{sm3,sm2,sm4}.py`：SM3/HMAC-SM3、SM2-SM3 签名与 SM2 加解密、SM4-CBC EtM；
- `crypto/signature.py` / `crypto/envelope.py`：签名记录与数字信封（SM2 包 CEK；S5-2 起业务用 provider 版 `EnvelopeV1`）；
- `crypto/keystore.py`：私钥口令保护（PBKDF2+AES-GCM）；`crypto/certs.py`：证书运行期校验；
- `services/audit.py` + `api/v1/admin.py`：审计哈希链 + `verify-chain`；
- `crypto/{rsa_layer,aesgcm_layer,provider}.py`：RSA-OAEP/PSS 与 AES-GCM 对照实现（S6 用）。

**注意**：`sign`/`submit` 已可用，单据能走到 `submitted`；但**财务端列表/验签/解密/审核还没做**
（S5-3~S5-5）。提交后的影像在 MinIO 里是密文，`GET /invoices/{id}/image` 会 **409**，
S5-4 才做"内存解密 → 渲染 → 释放"。浏览器端还没接报销单页面（S5-3 起补）：

- 登录三角色 → 落地：submitter `/invoices`、reviewer `/review`（= `review_status=pending` **待复核**）、admin `/templates`；
- 上传：单文件/批量；同图去重（同账号给"查看"链接，跨账号只说"已被其他账号上传"，不泄露 invoice_id）；
- 列表业务状态：**待确认 / 待复核 / 已复核**（另：已入报销单 / 已归档 / 已作废）；筛选仍用 DB 语义；
- 详情：左图右表；hover 行高亮框、点框定位行；滚轮缩放/拖拽平移；字段表无"操作"列；
- 确认：提交人"确认整单"（弹核对提示，全部字段置 confirmed → 待复核）；审核人"复核通过"（锁定字段）；
- 删除：仅**草稿**、仅本人、**硬删除**（含 MinIO 对象），可重新上传同一影像；
- 管理端模板查看/升版本/试跑；影像明文仅本地调试（密文/生产 409，等 S5 接入信封）；
- 人工走查清单：`docs/UI_TEST_FLOW.md`。

## 2. 仓库与环境

| 项 | 值 |
|---|---|
| 仓库 | `E:\BiSheXZY\study-OCR`（分支 `main`；**本地领先 origin 83 个 commit**（以 `git rev-list --count origin/main..HEAD` 为准）；最近代码提交见 `git log -1 --format=%h`；**push 永远由用户做**） |
| Python | 3.11.9（`E:\BiSheXZY\study-OCR\.venv`，**所有命令都用 `.venv/Scripts/python.exe`**） |
| 关键版本 | numpy **1.26.4**、opencv-python **4.6.0.66**、paddlepaddle **2.6.2**、paddleocr **2.7.3**、Pillow 12.3.0、PyMuPDF 2.1、SQLAlchemy **2.0.58**、Alembic **1.20.1**、PyJWT **2.15.1**、passlib **1.7.4**、ARQ **0.28.0**、redis-py **5.3.1**（ARQ 要求 `redis<6`，D-045）、APScheduler **3.11.1**、**gmssl 3.2.2**（D-073）、**cryptography 50.0.2**（D-077）、pycryptodomex 3.24.2、**pytest-cov 7.1.0 + coverage 7.17.4**（仅测试，D-082） |
| pip 源 | 阿里云全局镜像（`%APPDATA%\pip\pip.ini`），装依赖不用再加 `-i` |
| OCR 模型 | 已缓存 `~/.paddleocr/whl/{det,rec,cls}`（约 18MB），离线可用 |
| Docker | `study-ocr-{postgres,redis,minio}`（5433 / 6380 / 9002，控制台 9003）；**DB head = 0008**（16 业务表 + `report_no_seq` 序列 + alembic_version）；S4 起另有 OpenSSL 3.5 容器（`scripts/openssl.sh` / `.ps1`，D-078） |
| 依赖锁 | `backend/requirements.txt`（手写锁）+ `requirements.lock.txt`（pip freeze） |
| 必填配置 | `DATABASE_URL` / `REDIS_URL` / `MINIO_*` / `JWT_SECRET` / **`AUDIT_HMAC_KEY`（≥32 字符，D-081；缺失启动即失败，prod 拒绝示例占位符）**；S5-2 起另有 `ALGO_PROVIDER`（默认 `sm`）与 `KEY_STORAGE_ROOT`（空 = 仓库根） |

> **AUDIT_HMAC_KEY 特别说明**：它是审计链的"防伪印章"，写进 `backend/.env`（已本地生成，未入库）。
> 一旦库里开始有审计行就**不要换**——换了历史链全部验不过（遗留 #99）；多人共用同一个库时必须用同一个值。

### 2.1 本地开发库现状（2026-11-21 18:52 实测）

| 表 | 行数 | 说明 |
|---|---|---|
| `users` | 6 | `alice`(submitter) / `bob`(reviewer) / `root`(admin) / `test_sub`(submitter) / `test_rev`(reviewer) / `test_admin`(admin)；口令不在仓库，忘了就用 `scripts/create_user.py` 新建 |
| `invoices` | 5 | `111` / `112` / `122` 已确认、**待复核**（`reviewed_at` 空）；`113` / `117` **已复核**；`117` 是真实 PDF（金额 1057.43 / 税额 10.57 / 合计 1068.00） |
| `invoice_fields` / `ocr_lines` / `ocr_jobs` / `invoice_files` | 47 / 174 / 7 / 5 | 阶段 3 的识别数据 |
| `ticket_templates` / `field_rules` | 5 / 37 | seed 模板与校验规则 |
| `audit_logs` | **4** | S5-2 真机冒烟留下 `report_create/sign/seal/submit` 四行，`verify_chain ok`（`checked_count=4`）；审计只增不删 |
| `certificates` / `key_records` | **2 / 2** | `alice`(user, SM2, `.p8.enc` 路径) + `finance`(server, SM2)；由 `scripts/register_identity.py` 登记，**私钥仍在 secrets/** |
| `report_forms` / `report_items` / `signatures` / `envelopes` | **0 / 0 / 0 / 0** | S5-2 真机冒烟的临时单据/信封已清理，库里没有半成品 |
| `report_no_seq` | 序列存在 | 迁移 0008 建；当前已消耗到 1（冒烟那一单） |

- 本地 `secrets/`（整目录 gitignore，**不入库**）：
  - `secrets/keys/alice/sign.p8.enc` + `sign.pub.pem`（S4-5 生成，演示口令 `demo-pass-1234`，**仅演示**）；
  - `secrets/keys/finance/sign.p8.enc` + `sign.pub.pem`（S5-2 生成，财务服务器信封密钥，**演示口令同上**）；
  - `secrets/ca/{ca.key.pem,ca.crt.pem}`（S4-6 的 CA，**CA 私钥未加密**，遗留 #93）；
  - `secrets/certs/alice.crt.pem`（CN=alice/OU=submitter）、`secrets/certs/finance.crt.pem`（CN=finance/OU=server）。
- 端口：后端 8000、Vite 5173、MinIO 控制台 9003；无头冒烟 Edge 用 9222（临时 profile）。
- **S4-1 ~ S5-2 会话只跑 pytest / 命令行脚本 / Docker**，没有起 API / ARQ worker / Vite；
  下一次浏览器联调按 §5 启动（或先 `netstat -ano | findstr :8000` 看残留进程）。

## 3. 目录与关键文件（认路用）

```
backend/app/models/            ← 16 张表模型（一表一文件）+ base.py（命名约定/CHECK 助手）+ enums.py
backend/app/core/{config,security,redis,database,logging,exceptions,health}.py
backend/alembic/               ← 0001_init … 0008_report_number_and_audit_action（**head=0008**；0008 建 `report_no_seq` 并放开 `report_create`）；禁止改旧迁移
backend/app/services/repository.py   ← 行级隔离（filter_visible / get_visible_or_404，deny-by-default）
backend/app/services/report_service.py ← S5-1 报销单：单号序列 / 8 值状态机 / 建单+审计 / 明细+票据 in_report
backend/app/services/submit_service.py ← S5-2 签名+提交：签名域 / 验签 / 信封 / 幂等 / 审计（同一事务）
backend/app/services/report_crypto.py  ← 签名域 v1、report-v1 封包明文、EnvelopeV1 封装/拆封
backend/app/services/key_service.py    ← 证书/密钥选取、路径解析、口令解锁、证书校验
backend/app/services/pagination.py   ← 分页/排序白名单（PageParams/PageResult）
backend/app/services/{invoice_service,upload_service,storage,batch_service,duplicate,rule_source,confidence,reocr_service}.py
backend/app/services/job_service.py  ← 作业状态机/幂等/重试/僵尸恢复
backend/app/services/audit.py        ← S4-7 审计链（append_audit / verify_chain / list_audit_logs）
backend/app/api/{deps.py,v1/{auth,files,invoices,reports,templates,admin,health}.py}
backend/app/tasks/{worker,ocr_task,queue,scheduler}.py（anchor_audit_task 已改为每日真实复算）
backend/app/ocr/               ← 预处理 / 检测识别适配器 / 版面 / 抽取 / 校验 / 局部重识别
backend/app/crypto/            ← 阶段 4 全部产物 + S5-2 的 key_loader.py：
  canonical.py  signature.py  envelope.py  keystore.py  certs.py  key_loader.py
  sm/{sm3,sm2,sm4}.py        rsa_layer.py  aesgcm_layer.py  provider.py
backend/tests/                 ← 默认 751 条；`test_crypto/` 287 条；集成用例 `-m integration` **303** 条（含 S5-1 25 + S5-2 19）
scripts/                       ← 根目录统一放脚本：
  openssl.sh / openssl.ps1 / _openssl_docker.sh     ← Docker 内 OpenSSL 3.5.9（D-078）
  keygen.py / ca_init.sh / unlock_key.py            ← 密钥与证书（S4-5/S4-6；S5-2 起 `issue … server`）
  register_identity.py                              ← 证书/密钥登记进 certificates / key_records（S5-2）
  crypto_cross_check.py / crypto_bench_compare.py   ← 跨实现验签 / 国密 vs RSA/AES 对拍
  verify_chain.py / canonical_cross_check.{py,mjs}  ← 审计链校验 / canonical 跨语言对照
  ocr_acceptance.py / confidence_eval.py / reread_eval.py / ocr_baseline.py / ui_smoke.mjs 等
docs/{SCHEMA.md,API.md,PROGRESS.md,DECISIONS.md,EVAL.md,UI_TEST_FLOW.md} + docs/{spec,plan}/
```

## 4. 指标（实测汇总，论文口径）

**当前基线（2026-11-21）**：默认单测 **751 passed**；集成 **303 passed**（需 Docker）；
`tests/test_crypto` **287 passed**；`app/crypto` 行覆盖 **94%**；`alembic check` 零差异（head 0008）。

| 主题 | 关键数字 | 来源 |
|---|---|---|
| 预处理 | P50 75ms / P95 201ms；正视角 17 / 透视 21 / 兜底 22 / 失败 0 | `scripts/preprocess_demo.py` |
| OCR 单张推理 | P50 ≈1.4s（P95 ≈2.0s） | baseline 报告 |
| S3-1 真实链路 20 张 | 20/20 成功、P50 1012ms、P95 1680ms | `scripts/ocr_acceptance.py` |
| S3-2 置信度（40 张 / 213 字段） | auto 101 / review 110 / manual 2 | `scripts/confidence_eval.py` |
| S3-3 局部重识别（112 个低置信） | 109 个提升（**97.3%**）、平均 Δ +0.180 | `scripts/reread_eval.py` |
| S3-5 详情页画框 | 532px 显示 `scale=0.338854`；放大两次 `0.487898`；drawMs 0.39/0.48ms | `ui_smoke --eval` + 单测 |
| S3-6 整单确认 | 首次 confirm **422**（5 字段）→ 修复后 **200 confirmed**（v0→v3）；并发 PATCH 第二次 **409** | `tests/test_confirm.py` |
| S3-7 重复检测 | 同图再传 **409/40901**；同票号 confirm **409/40900** | `tests/test_duplicate.py` |
| S3-8 批量上传 | 30 文件 POST 1.17s；48.6s 全部 succeeded；批内并发 3 | `tests/test_batch.py` |
| S3-9 双人复核 | 复核 200（`reviewed_by/at` 落库）；复核后改字段 409；重复复核幂等 | `tests/test_review.py` |
| S4-1 canonical+SM3 | SM3 国标向量全过；`100`/`"100"`/`"100.00"` 三种字节；Python↔Node 三字段逐字相等 | `test_canonical/test_sm3` + `canonical_cross_check` |
| S4-2 SM2 签名 | **GB/T 32918.5 A.1** 向量逐位一致（`r=F5A03B06…`、`s=B1B6AA29…`）；双重哈希陷阱、`s+n` 可塑、`04` 开头公钥均有用例 | `test_sm2.py` |
| S4-3 SM4 EtM | GB/T 32907 A.1 向量；与 Node `sm4-cbc` 逐字节一致；坏填充/坏 MAC 分类正确 | `test_sm4.py` |
| S4-4 数字信封 | **2MB seal+open 0.095s**；`wrapped_key` 112B；C2/C3 段篡改都 `mac_mismatch` | `test_envelope.py` |
| S4-5 密钥库 | OpenSSL 生成 PKCS#8；产物只有 `.p8.enc` + `.pub.pem`；**OpenSSL 独立验签成功**（显式 distid） | `test_keystore.py` + `keygen.py` |
| S4-6 证书 | CA + 用户证书真签发；`openssl verify` OK；KeyUsage/EKU/SM2 齐全；五类状态判定 | `test_certs.py` + `ca_init.sh` |
| S4-7 审计链 | 改 detail/entry_hash/删行都报正确断点；**并发 50 条不分叉** | `test_audit*.py` + `verify_chain.py` |
| S4-8 向量与覆盖 | 集中国标向量；端到端 + 7 类 1 bit 篡改；`app/crypto` **94%** | `test_vectors/test_pipeline` + `--cov` |
| S4-9 对照实现 | SM2 sign 5.28ms / RSA-2048 29.4ms / RSA-3072 95.1ms；256KB 对称 SM4+HMAC 3.57ms vs AES-GCM 0.049ms（**实现后端不同，见 #103**） | `test_provider_rsa_gcm.py` + `crypto_bench_compare.py` |
| S5-1 报销单 | 建单 201（单号 `RPT-202611-0001` 形态）；3 条明细合计 **"330.50"**；**10 线程并发建单 10 个不同单号**；两线程抢同一票 **恰好 1 成功 / 1 → 40901**；`report_create` 审计后 `verify_chain` ok | `tests/test_reports.py`（25 条集成） |
| S5-2 签名/提交 | 签名 200 + 证书公钥**独立验签通过**；重签旧行 `void`/新行 `v2`；口令错 **422** 统一文案；篡改 canonical → 409 `digest_mismatch` 且整体回滚；并发提交恰好 1 成功；真机：明文对象已删、密文可被财务私钥解回、审计 4 行链 OK | `tests/test_submit.py`（19）+ `test_report_crypto.py`（17） |

**最弱环节**：`taxi_ticket` 整单 0.200（纸面约 300px 高、字太小）、`total_amount` 约 80%；
`vat_general` 的 amount/tax 靠"合计约束反解"补到 83–86%（D-033）。

## 5. 常用命令（一条一条可复制）

```bash
cd /e/BiSheXZY/study-OCR

# ── 基础回归 ─────────────────────────────────────────────────────────
(cd backend && ../.venv/Scripts/python.exe -m alembic upgrade head)        # head=0008
(cd backend && ../.venv/Scripts/python.exe -m alembic check)               # 模型↔库零差异
(cd backend && ../.venv/Scripts/python.exe -m pytest -q)                   # 751 passed（默认跳过集成）
(cd backend && ../.venv/Scripts/python.exe -m pytest -m integration -q)    # 303 passed（需 Docker）
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto -q) # 287 passed（密码层）
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_reports.py -m integration -q)  # 25 passed（S5-1）
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_submit.py -m integration -q)   # 19 passed（S5-2）
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_report_crypto.py -q)           # 17 passed（S5-2 纯函数）

# ── 报销单（S5-1/S5-2；需后端在跑 + 一张已确认票据 + 已登记身份）────
curl -s -X POST localhost:8000/api/v1/reports -H "Authorization: Bearer <submitter-token>" \
  -H "Content-Type: application/json" -d '{"title":"2026-11 差旅报销","reason":"学术会议"}'
# → 201 {"data":{"report_no":"RPT-202611-0001","status":"draft","total_amount":"0.00","items":[],...}}
curl -s -X POST localhost:8000/api/v1/reports/1/items -H "Authorization: Bearer <submitter-token>" \
  -H "Content-Type: application/json" -d '{"invoice_id":117,"amount":"1068.00","remark":"交通"}'
# → 201 data.total_amount 重算；票据 status→in_report
curl -s -X POST localhost:8000/api/v1/reports/1/sign -H "Authorization: Bearer <submitter-token>" \
  -H "Content-Type: application/json" -d '{"private_key_passphrase":"<本人私钥口令>"}'
# → 200 {"signature_id":1,"status":"signed","algo":"SM2",...}
curl -s -X POST localhost:8000/api/v1/reports/1/submit -H "Authorization: Bearer <submitter-token>" \
  -H "Content-Type: application/json" -d '{"idempotency_key":"demo-key-0001"}'
# → 200 {"status":"submitted","envelope_id":1,"duplicated":false}；重复同一键 → duplicated=true
curl -s localhost:8000/api/v1/reports/1 -H "Authorization: Bearer <submitter-token>"
curl -s "localhost:8000/api/v1/reports/mine?status=submitted&page=1&page_size=20" -H "Authorization: Bearer <submitter-token>"

# ── 证书/密钥登记（S5-2；私钥仍只在 secrets/）───────────────────────
STUDYOCR_KEY_PASSPHRASE='demo-pass-1234' .venv/Scripts/python.exe scripts/keygen.py --name finance --role finance
STUDYOCR_KEY_PASSPHRASE='demo-pass-1234' bash scripts/ca_init.sh issue finance server
.venv/Scripts/python.exe scripts/register_identity.py --user alice
.venv/Scripts/python.exe scripts/register_identity.py --server finance

# ── 密码层覆盖率（任务卡验收 ≥85%；实测 94%）────────────────────────
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto \
  --cov=app/crypto --cov-report=term-missing -q)

# ── 跨实现 / 对拍（需 Docker）───────────────────────────────────────
.venv/Scripts/python.exe scripts/crypto_cross_check.py                      # 我们签 → OpenSSL 验（显式 distid）
.venv/Scripts/python.exe scripts/crypto_bench_compare.py --n 10 --symmetric-kb 256

# ── 审计链 ──────────────────────────────────────────────────────────
.venv/Scripts/python.exe scripts/verify_chain.py                            # {"ok": true, "checked_count": N}
curl -s localhost:8000/api/v1/admin/audit/verify-chain -H "Authorization: Bearer <admin-token>"
curl -s "localhost:8000/api/v1/admin/audit?page=1&page_size=20" -H "Authorization: Bearer <admin-token>"

# ── 密钥与证书（OpenSSL 在 Docker 内；口令只走 env/getpass）──────────
STUDYOCR_KEY_PASSPHRASE='demo-pass-1234' .venv/Scripts/python.exe scripts/keygen.py --name alice --role submitter
bash scripts/ca_init.sh init
STUDYOCR_KEY_PASSPHRASE='demo-pass-1234' bash scripts/ca_init.sh issue alice submitter
bash scripts/ca_init.sh verify secrets/certs/alice.crt.pem
# 用 OpenSSL 独立验我们签的名（必须显式 distid，否则默认 SM2 ID 为空会验不过）
bash scripts/openssl.sh pkeyutl -verify -pubin -inkey secrets/keys/alice/sign.pub.pem \
  -rawin -digest sm3 -pkeyopt distid:1234567812345678 -in msg.bin -sigfile sig.der

# ── OpenSSL 命令行（宿主机没装，用 Docker；D-078）───────────────────
bash scripts/openssl.sh version                                  # Git Bash/MSYS2
powershell -NoProfile -File scripts/openssl.ps1 version          # Windows PowerShell
# 镜像可换源 / 离线：OPENSSL_IMAGE=registry.example.com/alpine/openssl:3.5.9
bash scripts/openssl.sh genpkey -algorithm SM2 -out secrets/keys/alice-sign.pem
bash scripts/openssl.sh dgst -sm3 -binary somefile

# ── canonical 跨语言对照 ────────────────────────────────────────────
.venv/Scripts/python.exe scripts/canonical_cross_check.py
node scripts/canonical_cross_check.mjs

# ── 起服务（浏览器联调时）──────────────────────────────────────────
(cd backend && ../.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000)
(cd backend && ../.venv/Scripts/python.exe -m arq app.tasks.worker.WorkerSettings)   # 另开终端
(cd web && npm run dev)                                                              # 5173

# ── 前端检查 / 冒烟（需先起后端）────────────────────────────────────
(cd web && npm run typecheck && npm run build)
node scripts/ui_smoke.mjs "http://127.0.0.1:5173/login" --clear --view 1440x900 \
  --fill 'input[autocomplete="username"]' <用户名> \
  --fill 'input[autocomplete="current-password"]' <口令> \
  --click 登录 --expect 我的票据 --shot ui.png

# ── 阶段 3 的验收脚本（浏览器/API 均在跑时）─────────────────────────
.venv/Scripts/python.exe scripts/ocr_acceptance.py --samples 20 --username <账号> --password <口令>
.venv/Scripts/python.exe scripts/confidence_eval.py --samples 40
.venv/Scripts/python.exe scripts/reread_eval.py --samples 40
.venv/Scripts/python.exe scripts/export_ocr_schema.py --check
.venv/Scripts/python.exe scripts/verify_samples.py
.venv/Scripts/python.exe scripts/bench_list.py --rows 50000 --iterations 50
(cd backend && ../.venv/Scripts/python.exe ../scripts/create_user.py --username reviewer1 --role reviewer --real-name 审核员)
(cd backend && ../.venv/Scripts/python.exe ../scripts/seed_templates.py)
```

## 6. 重要取舍（D-023 ~ D-085，都记在 `docs/DECISIONS.md`，别重复踩）

| 决策 | 要点 |
|---|---|
| D-023 | 票种以**数电票**为主流（旧版增值税电子普通发票为历史形态） |
| D-027 | OCR 依赖**整组锁死**：paddleocr 2.7.3 + paddle 2.6.2 + numpy 1.26 + opencv 4.6（会自动顶 numpy 2，必须压住） |
| D-028 | 票种识别用**关键字规则**，不上分类器 |
| D-029 | 四角点四项校验（自交/退化/对边均衡/单边裁切）——"warp 成功 ≠ warp 正确" |
| D-031 | 检测指标用**覆盖匹配**；论文必须写口径 |
| D-032 | 排除超市小票；范围 = 五类合规票据 |
| D-033 | amount/tax 用**合计约束反解**（唯一解才采用，带 `paired:sum:*`） |
| D-034 | 去斜阈值**不收紧**（1.0° 会让字段准确率掉 3–5 点） |
| D-035 | **PDF 不做预处理**；照片走"拍前引导 + 重拍判断"；透视加 2% 安全边距 |
| D-036 | 同步 SQLAlchemy 2.0 + psycopg3；URL 在 `core/database.py` 映射 |
| D-037 | 迁移以模型为唯一来源；`alembic check` 零差异为门槛；JSONB 不设 server_default |
| D-038 | PyJWT + passlib Argon2id（m=64MiB/t=3/p=4）；HS256 显式白名单 + `typ` |
| D-039 | **登出只吊销 refresh**：access 剩余 TTL 内仍可用（无状态 JWT 固有代价，论文写"不足"） |
| D-040 | 审计动作补 `login_failed/logout/forbidden`（0002）；**审计行**留到 S4-7/S5 |
| D-041 | 行级隔离用**应用层统一过滤**，不用 RLS |
| D-042 | 分页：同 `Select` 求 total、`id asc` 兜底；keyset 暂不做 |
| D-043 | 票据状态机按 **D-011 的 5 值**；建票无 HTTP 口；字段修改 `PATCH /{id}/fields` |
| D-044 | S4 前上传**本地明文调试**（`is_cipher=false`，prod 拒绝）；对象名 `invoices/yyyy/mm/<hex>.bin` |
| D-045 | ARQ 0.28 强制 redis-py 5.3.1；幂等=终态跳过 + `SET NX EX 600`；重试 5s/30s |
| D-046 | 模板：6 列 + `field_specs_json`；PUT 升版本；active 互斥；60s 缓存；显式 seed |
| D-047 | 前端 401 **单飞 refresh**；守卫 `/me` 水合；`meta.roles` |
| D-048 | 上传页串行上传；409 绿色去重；上限 20MB；批量不自动跳详情 |
| D-049 | 列表以 `route.query` 为唯一状态源；字段编辑权限/金额确认/乐观锁 |
| D-050 | APScheduler 进程内；每 tick 抢 Redis 锁，**成功后锁留到 TTL** 做时段去重 |
| D-051 | 第一版 UI：分栏登录、深色侧栏、按角色落地 |
| D-052 | 管理端模板页：列表+抽屉+升版本+试跑；无删除接口 |
| D-053 | 内容页撑满右侧；登录表单保持 400px |
| D-054 | 票种锁定 5 类 + unknown；管理端不提供新建入口 |
| D-055 | `ocr_jobs` 一 run 一行；逐阶段明细进 `metrics.stages` |
| D-056 | `ocr_jobs` 加 `metrics/started_at/finished_at`（0004） |
| D-057 | PDF 复用 PyMuPDF（前 3 页）；规格 DB 优先/YAML 回退；worker 并发 1；deadline 165s |
| D-058 | 置信度 = (0.35 锚点 + 0.65 值) × agreement × rule_factor；三档 0.90/0.60；校验失败封顶 0.45 |
| D-059 | S3-9 起 reviewer 入口 = `review_status=pending`；auto ≠ confirmed |
| D-060 | 重识别异步（202）；只改 raw_text/confidence/source；bbox 原图坐标 |
| D-061 | 外扩 max(8px,10%行高)、2× 放大、1.5s 预算；`field_reread` 枚举不写行 |
| D-062 | 识别产物契约唯一来源 `schemas/ocr.py`；`field_value=raw_value` |
| D-063 | `GET /image` 明文仅 `STORAGE_ALLOW_PLAINTEXT`；PDF 250 DPI；前端 blob 取图 |
| D-064 | 新增 `GET /lines`（原图坐标、不返回文本）；canvas + culling；缩放改 CSS 尺寸 |
| D-065 | 整单确认闸门 必填→冲突→低置信未确认→validators；本人权限；幂等 |
| D-066 | 新增 `GET /duplicate-check`；强重复 confirm 409；弱重复只提示 |
| D-067 | 批量：0006；服务端排队 ≤3；`after_job_end` 补位；重试只建新 job |
| D-068 | 双人复核（取代 D-017 金额限制）；`reviewed_at` 锁定修改；不加 status 枚举 |
| D-069 | 横版票金额规则排除"价税合计"、税号 O→0/I→1 纠偏 |
| D-070 | 字段表去"操作"列；整单确认一次置全部 confirmed；reocr API 保留无入口 |
| D-071 | DELETE 改**硬删除**（仅 draft，含 MinIO 对象）；删除后 404 不幂等 |
| D-072 | 业务状态前端派生：待确认/待复核/已复核；不加 DB 枚举 |
| D-073 | `gmssl==3.2.2` + `pycryptodomex==3.24.2` 锁版本；不用 gmssl 随机源 |
| D-074 | canonical 11 条硬规则（码点序/无空格/拒 float 等）；JS 默认 UTF-16 排序会算出不同摘要 |
| D-075 | SM2 只走国标 SM2-SM3（ZA 绑定）；K 只用 `secrets`；封堵 gmssl 七个坑 |
| D-076 | SM4 EtM：MAC 覆盖 `aad‖IV‖密文`；CBC/PKCS7 自实现（gmssl 不校验填充） |
| D-077 | 性能改造：SM3/HMAC 走 `hashlib`、SM4 走 `cryptography`；信封 MAC 多绑 `wrapped_key` |
| D-078 | 宿主机无 openssl：用 Docker 内 OpenSSL 3.5.9（`scripts/openssl.sh`/`.ps1`） |
| D-079 | 私钥口令保护：PBKDF2 200k + AES-256-GCM；`key_fp=SHA-256(0x04‖X‖Y)`；`key_records` 落库属 S5 |
| D-080 | 证书：OpenSSL 签发（CSR 经 stdin，私钥不落盘）；运行期**不做链验签**；角色取自 OU |
| D-081 | 审计链：HMAC-SM3 绑全部审计字段；`payload_sha256` 不落列；咨询锁 8071701；业务审计行留 S5 |
| D-082 | 向量集中（注明出处）；新增测试期依赖 pytest-cov/coverage；`app/crypto` 覆盖 94% |
| D-083 | `AlgoProvider` 同接口；SM 默认、RSA/AES 对照；OAEP/PSS 显式 SHA-256；provider 接入 S5-2 |
| D-084 | 单号 = 全局序列 `report_no_seq`（不按月重置）；"一票一单"靠单据行 + 票据行 `FOR UPDATE`；补 `report_create`；明细不单独审计；建单/明细偏差以契约为准 |
| D-085 | 签名/信封统一走 `AlgoProvider`（sm 默认）；`EnvelopeV1` AAD 绑 `wrapped_key`+摘要；单据信封 + 每票文件信封；影像转密文、明文提交后删；一次操作 version 只 +1；签名方不自证验签 |

## 7. 下一步：S5-3 财务端待审列表（阶段 5 第三张卡）

- 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 第 8 章 S5-3；
- 契约：**`docs/API.md` §5 的 `GET /finance/pending`、`GET /finance/{id}`（+ CSV 导出）**；
  产出 `backend/app/api/v1/finance.py`、`web/src/views/FinanceList.vue`（任务卡要求）；
- 流程（AGENTS §11）：spec（`docs/spec/s5-3-*.md`）→ plan → 写码（用户已授权"不逐步确认"）；
- 本卡硬要求（契约/任务卡原文）：
  1. 仅 `reviewer`；`submitter` 403、`admin` 403（**不得代审**，职责分离）；
  2. `verify_status`（`unverified`/`ok`/`failed`）**提交时已落库**的字段——列表现成字段直接读，
     **不在列表里实时验签**（否则 N+1 + 几十秒延迟）；S5-2 的签名行目前 `verified=false`，
     S5-4 之前列表会显示"未验"；
  3. 列表**绝不返回 ciphertext 全文**（`ciphertext_b64`/`wrapped_key_b64`/`ciphertext_storage_key` 一律不出现）；
  4. 筛选分页复用 S2-4 协议（`verify_status` / `submitted_from`/`submitted_to` / `q` / 排序白名单）；
  5. CSV 导出**流式响应 + UTF-8 BOM**（Excel 中文不乱码）；
  6. 行级隔离：reviewer 可见 `submitted/approved/rejected/archived`（`services/repository.py` 已有策略）。
- 可直接复用：`services/pagination.py`（含 `nulls_last`）、`schemas/report.py` 的 `EnvelopeOut`（只给元数据）、
  `report_service` 的 8 值状态常量、S5-2 落库的 `envelopes.payload_sha256`（列表展示用摘要，不给全文）。
- 验收（任务卡）：提交 5 单看到 5 条；CSV 用 Excel 打开中文正常；submitter 访问 403。

### 7.1 S5-2 已交付的契约要点（S5-3/S5-4 直接依赖）

| 项 | 事实 |
|---|---|
| 可提交单据 | `status=submitted`、`submitted_at` 非空、`idempotency_key` 已落库；`signatures` 有 `active` 行 |
| 签名 | `report_forms.canonical_json` 原样字节；`digest_algo=SM3`；`verified=false`（S5-4 才独立验签） |
| 信封 | 单据级（`form_id`，`ciphertext_b64`）+ 文件级（`file_id`，`ciphertext_storage_key`）；`EnvelopeOut` 已过滤密文 |
| 影像 | 提交后 `invoice_files.is_cipher=true`、`storage_key` 指向密文对象；`GET /image` 409（S5-4 改内存解密） |
| 审计 | `report_create` → `sign` → `seal` → `submit` 四类行，全部同事务、`verify_chain ok` |
| 解封原语 | `services/report_crypto.open_sealed(...)`（S5-4 直接调用；AAD 由 `envelopes` 行字段重建） |

## 8. 未闭合 / 需要用户参与的点

1. **push**：本地领先 origin **83 个 commit**（S3-1 ~ S3-9 + D-069~D-085 + S4-1 ~ S4-9 + S5-1/S5-2），AI 不推，由用户 `git push`；
2. **人工抽查**（AI 看不了图）：`backend/eval/results/preprocess/*_compare.jpg`、`layout/*_layout.jpg`、
   `s37-dup.png`、`ui-111-new.png` 等；真实 PDF 的框选对齐需人眼确认；
3. `taxi_ticket` 整单 0.200（已知短板，S6 分组报告如实写）；`total_amount` 约 80%；
4. 真实样本：`E:\BiSheXZY\发票样本集`（1 份 PDF + 1 张 PNG）；论文写"合成集为主 + 真实件定性校验"；
5. **审计行已接入报销主链路**：`report_create`（S5-1）与 `sign/seal/submit`（S5-2）都在同一事务写入；
   `login_failed/logout/forbidden/confirm_field/field_reread/verify/decrypt/review` 等仍待各自接口接入
   （#21/#27/#54/#61 已重定向到 S5）；`AUDIT_HMAC_KEY` 一旦有行就不要换（#99）；
6. `GET /invoices/{id}/image`：**提交后**文件是密文（`is_cipher=true`）→ 当前返回 409/40900；
   S5-4 接"内存解密 → 渲染 → 释放"；本地未提交票据仍是明文（`STORAGE_ALLOW_PLAINTEXT=true`）；
7. **D-071 硬删除不可恢复**（仅草稿可删），论文需写明；
8. `POST /reocr` 接口保留但**前端无入口**（D-070）；`PATCH /fields` 的 `confirm` 参数保留（API 兼容）；
9. 管理端模板页已补，但**新建票种入口已移除**（D-054）：票种固定 5 类 + unknown；
   新增票种必须改 SCHEMA/迁移/模板/样本并发版；
10. 前端联调/回归走查：`docs/UI_TEST_FLOW.md`（含 test_sub/test_rev/test_admin 与种子数据状态）；
11. 阶段 4 的已知边界（论文"安全边界/不足"）：无证书链验签、无 CRL/OCSP、CA 私钥未加密（#93）、
    审计无外部锚（#98）、审计 key 无轮换（#99）、RSA 业务私钥无轮换（#101/#110）、
    AES-GCM 无 nonce 复用检测（#102）、对拍口径含实现语言差异（#103）；**provider 已于 S5-2 接入业务**（D-085）；
12. 本地演示密钥：`secrets/keys/alice` 与 `secrets/keys/finance`（口令均 `demo-pass-1234`，**仅演示**）、
    `secrets/ca`（CA 私钥未加密）；`certificates/key_records` 已在本地库各登记 2 条（S5-2）；
13. S5-1/S5-2 的已知边界（论文"不足"可写）：单号全局递增**不按月重置**（#104）；"一票一单"只在服务层保证
    （#105）；明细只增不删不改（#106）；建单无速率限制（#107）；业务信封是 `EnvelopeV1`、S4-4 参考实现不再走业务路径（#108）；
    **驳回重提无法二次提交**（信封唯一约束，#109）；RSA 业务密钥无轮换（#110）；明文对象删除是尽力而为（#111）；
    提交同步执行、耗时随张数线性增长（#112）。

## 9. 工作方式约定（用户偏好，新会话请沿用）

- 用户是"放手型"：希望**替他做决定并给理由**，事后复核，不要每步都问；
- 但**功能级任务卡走 spec → plan → 确认**（AGENTS §11），小修/bugfix 可只在对话里给设计；
- **2026-11-20 起**审批策略改为"从不询问"：spec/plan 仍落盘，但**不再等用户逐步确认**，
  拍板理由写进 spec/plan/DECISIONS，做完一次性汇报供事后复核（S4-1 ~ S4-9、S5-1、S5-2 均按此执行）；
- 汇报风格：先给**实测数字与命令输出**，再解释"为什么这样做"，**不要粉饰**（做不到就直说）；
- 每次收口都要更新 `docs/PROGRESS.md`，新增取舍写 `docs/DECISIONS.md`；
- 提交信息用 Conventional Commits（含任务号，如 `feat(s4-9): ...`）；**push 永远由用户做**。