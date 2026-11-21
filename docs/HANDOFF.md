# 会话交接（HANDOFF）

> 给"下一个会话"的入口文档。**先读它，再读 `AGENTS.md` 与 `docs/PROGRESS.md`**。
> 最后更新：2026-11-21 15:10（阶段 4：S4-1 ~ S4-6 完成；开新会话用）

## 1. 一句话现状

**阶段 3 已完成**（S3-1 ~ S3-8 + 补充卡 S3-9）；**阶段 4 进行中**：S4-1（canonical + SM3）、
S4-2（SM2 签名）、S4-3（SM4 EtM）、S4-4（数字信封）、S4-5（密钥与口令保护）、S4-6（CA 与角色证书）
已完成；密码链路已能"生成密钥/证书 → 签名 → 加密信封 → 拆封 → 验签"。
**下一步：S4-7 审计日志哈希链 ★关键**。

当前浏览器可走通（`http://127.0.0.1:5173`）：

- 登录三角色 → 落地：submitter `/invoices`、reviewer `/review`（= `review_status=pending` 的**待复核**）、admin `/templates`；
- 上传：单文件/批量；同图去重（同账号给"查看"链接，跨账号只提示"已被其他账号上传"，不泄露对方 invoice_id）；
- 列表：业务状态统一显示 **待确认 / 待复核 / 已复核**（另：已入报销单 / 已归档 / 已作废）；
  筛选仍用数据库语义 `status` / `extract_status` / `review_status`；
- 详情：左图（约 1/3 宽）右表；hover 行高亮框、点框定位行；滚轮缩放/拖拽平移；
  字段表**没有"操作"列**（重识别/逐字段确认已移除）；
- 确认：提交人点"确认整单"→ 弹"已仔细核对所有字段"提示 → 一次把全部字段置 confirmed → **待复核**；
  审核人点"复核通过"→ **已复核** 并锁定字段；
- 删除：仅**草稿**、仅本人、**硬删除**（票据/字段/行/作业/文件/MinIO 对象），删除后可重新上传同一影像；
- 管理端模板查看/升版本/试跑；影像明文仅本地调试（S4 接入信封后替换）；
- 人工走查清单：`docs/UI_TEST_FLOW.md`。

## 2. 仓库与环境

| 项 | 值 |
|---|---|
| 仓库 | `E:\BiSheXZY\study-OCR`（分支 `main`；本地领先 origin 若干 commit，**push 由用户做**） |
| Python | 3.11.9（`E:\BiSheXZY\study-OCR\.venv`，**所有脚本都用 `.venv/Scripts/python.exe`**） |
| 关键版本 | numpy **1.26.4**、opencv-python **4.6.0.66**（含 contrib 同版本）、paddlepaddle **2.6.2**、paddleocr **2.7.3**、Pillow 12.3.0、PyMuPDF 2.1、SQLAlchemy **2.0.58**、Alembic **1.20.1**、PyJWT **2.15.1**、passlib **1.7.4**、python-multipart **0.0.34**、ARQ **0.28.0**、redis-py **5.3.1**（ARQ 要求 `redis<6`，D-045）、APScheduler **3.11.1** + tzlocal **5.4.5**（S2-12） |
| pip 源 | 已配阿里云全局镜像（`%APPDATA%\pip\pip.ini`），装依赖不用再加 `-i` |
| OCR 模型 | 已缓存在 `~/.paddleocr/whl/{det,rec,cls}`（约 18MB），离线可用 |
| Docker | `study-ocr-{postgres,redis,minio}` 三个容器（5433 / 6380 / 9002，控制台 9003）；**库已迁到 `0007`（16 业务表 + alembic_version）**；S4 起还提供 OpenSSL 3.5 容器（`scripts/openssl.sh` / `.ps1`，D-078） |
| 依赖锁 | `backend/requirements.txt`（手写锁）+ `requirements.lock.txt`（pip freeze） |

> ✅ `gmssl==3.2.2`（+ 传递依赖 `pycryptodomex==3.24.2`）已在 S4-1 装好并写进 requirements/lock；
> 装入后 numpy 1.26.4 / paddle 2.6.2 / paddleocr 2.7.3 **未变**，全量单测 480 passed。

### 2.1 本地开发库现状（2026-11-20 19:40）

- 账号：老账号 `alice` / `bob` / `root`；联调账号 **`test_sub`（submitter）、`test_rev`（reviewer）、`test_admin`（admin）**。
  口令未写进仓库（避免提交 secret）；新会话冒烟若不知道口令，用 `scripts/create_user.py` 新建用户并自设口令。
- 数据：5 个 seed 模板；**5 张票据**：`111`（待复核）、`112`（待复核）、`122`（待复核，干净草稿已确认）、
  `113` / `117`（已复核）；`117` 是真实 PDF（金额 1057.43 / 税额 10.57 / 合计 1068.00）。
  `invoice_fields` 47 行、`ocr_lines` 174 行、`ocr_jobs` 7 行、`invoice_files` 5 行；审计行为 0（S4-7 才写）。
- 端口：后端 8000、Vite 5173、MinIO 控制台 9003；无头冒烟 Edge 用 9222（临时 profile）。
- **本次会话（S4-1）**只跑 pytest 与命令行脚本，**没有**起 API / ARQ worker / Vite；
  下一次需要浏览器联调时按 §5 启动（或先 `netstat -ano | findstr :8000` 看有没有残留进程）。

## 3. 目录与关键文件（认路用）

```
backend/app/models/      ← S2-1：16 张表模型（一表一文件）+ base.py（命名约定/CHECK 助手）+ enums.py
backend/app/core/database.py  ← S2-1：`postgresql://` → `postgresql+psycopg://` 方言映射 + 引擎/会话工厂
backend/alembic/         ← 0001_init / 0002_audit_action / 0003_ticket_type / 0004_ocr_jobs / 0005_field_reread / 0006_batch / 0007_review（head=0007）
backend/app/core/{security,redis}.py  ← S2-2：Argon2id + HS256 JWT；Redis 单例
backend/app/services/{auth_service,token_store}.py  ← S2-2：注册/认证/签发/轮换/吊销；refresh 白名单
backend/app/api/{deps.py,v1/auth.py}  ← S2-2：Bearer 解析、get_current_user、require_roles、5 条 auth 路由
backend/app/crypto/canonical.py  ← S4-1：canonical_json / canonical_sha256（键码点序、拒 float/Decimal/超大 int）
backend/app/crypto/sm/sm3.py     ← S4-1/S4-3：sm3_digest(bytes)->32B、hmac_sm3（RFC 2104）、sm3_hex 仅日志
backend/app/crypto/sm/sm4.py     ← S4-3：SM4-CBC + PKCS7 + EtM（mac=HMAC-SM3(mac_key, aad‖IV‖密文)；先验 MAC）
backend/app/crypto/sm/sm2.py     ← S4-2/S4-4：sign_bytes/verify_bytes（只走 sign_with_sm3；K=secrets；DER 签名）
                                    + encrypt_bytes/decrypt_bytes（C1C3C2、1–32B、SM3 计数器 KDF）
backend/app/crypto/signature.py  ← S4-2：SignatureRecord + VerifyResult(ok/digest_mismatch/bad_signature) + sign/verify_canonical
backend/app/crypto/envelope.py   ← S4-4：Envelope + EnvelopeError + seal/open_sealed（CEK 每封新生成；MAC 绑 header‖wrapped_key‖IV‖密文‖指纹）
backend/app/crypto/keystore.py   ← S4-5：口令保护私钥（PBKDF2 200k + AES-256-GCM；PKCS#8 解析；key_fp；统一"口令错误"）
backend/app/crypto/certs.py      ← S4-6：证书运行期校验（指纹/有效期/公钥一致/吊销/OU 角色；不做链验签）
scripts/keygen.py                ← S4-5：OpenSSL 生成 → 加锁 → 解锁 → 签名自检（口令只走 env/getpass）
scripts/ca_init.sh               ← S4-6：init/issue/show/verify（CSR 经 stdin，私钥不落盘）
scripts/unlock_key.py            ← S4-6：`.p8.enc` → PKCS#8 PEM 仅 stdout（给 ca_init 的管道）
scripts/_openssl_docker.sh       ← D-078/共享：openssl_docker / openssl_docker_stdin
scripts/openssl.sh / .ps1        ← D-078：宿主机没有 openssl，用 Docker 内 OpenSSL 3.5.9
backend/app/services/repository.py  ← S2-3：行级隔离（策略表 + filter_visible + get_visible_or_404，deny-by-default）
backend/app/services/pagination.py  ← S2-4：分页/排序协议（PageParams/PageResult、白名单排序、同一 stmt 求 total）
backend/app/services/invoice_service.py  ← S2-5/S3-9：建草稿/状态机/列表/详情/**硬删**/字段修改/复核
backend/app/services/{storage,upload_service}.py  ← S2-6：MinIO 封装 + 上传校验/去重/补偿
backend/app/services/job_service.py  ← S2-7：作业状态机/幂等锁/重试/僵尸恢复
backend/app/tasks/scheduler.py  ← S2-12：APScheduler + 按时段 Redis 锁（每日锚定）
backend/app/services/{template_service,template_registry,template_seed}.py  ← S2-8：模板 CRUD/升版本/60s 缓存/seed
backend/app/tasks/{worker,ocr_task,queue}.py  ← S2-7：ARQ WorkerSettings、任务函数、入队协议（S3-1 起接真实流水线，max_jobs 默认 1）
backend/app/services/ocr_pipeline.py  ← S3-1：流水线唯一编排（下载→渲染/预处理→识别→票种→抽取→校验→落库）
backend/app/services/ocr_status_service.py  ← S3-1：GET /invoices/{id}/ocr-status 读模型（stages/progress）
backend/app/services/{batch_service,duplicate,rule_source,reocr_service,image_service,line_service}.py
                         ← S3-1~S3-9：批量排队/重复检测/规则来源/局部重识别/影像/行框
backend/app/api/v1/{invoices,files,templates}.py + schemas/{invoice,template,ocr}.py  ← 票据/上传/模板/字段接口
web/src/api/{http,auth,files,invoices,templates}.ts + stores/user.ts + router + layout/MainLayout.vue  ← 会话/刷新/上传/轮询
web/src/views/{Login,Forbidden,Placeholder,Upload,InvoiceList,InvoiceDetail,TemplateAdmin}.vue  ← 登录/403/占位/上传/列表/详情/模板
web/src/utils/{format,bbox,landing}.ts + types/{invoice,ocr}.ts + components/{ImageCanvas,FieldTable}.vue
                         ← 金额/时间标签/业务状态派生 + 坐标换算 + 完整类型 + 影像画布/字段表
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
| S3-3 局部重识别（40 张 / 112 个低置信字段） | **109 个提升（97.3%）**、平均 Δ **+0.180** | `scripts/reread_eval.py`（`reread_eval_*.json`） |
| S3-4 识别产物契约 | `docs/schemas/ocr_result.schema.json`（5 模型）；`GET /fields` 实测原文 `￥1,796.37` / 归一 `1796.37` | `scripts/export_ocr_schema.py --check`；`tests/test_ocr_schema.py` |
| S3-5 详情页画框 | 532px 显示 `scale=0.338854`；放大两次 `0.487898`（×1.44）；`drawMs` 0.39/0.48ms；40 行框/12 字段框 | `ui_smoke --eval` 数值断言 + `tests/test_{image_service,lines_service,invoice_media}.py` |
| S3-6 整单确认 | 真实票首次 confirm **422**（5 字段：冲突1+低置信4）→ 修复后 **200 confirmed**（version 0→3）；同 version 并发 PATCH 第二次 **409** | `tests/{test_confirm,test_rule_source}.py` + 冒烟脚本 |
| S3-7 重复检测 | 同图再传 **409/40901**；同票面重编码 B → `duplicate_of=49`、duplicate-check **strong**、confirm **409/40900**（`data.invoice_id=49`） | `tests/test_duplicate.py`（8 条）+ 冒烟脚本 |
| S3-8 批量上传 | 30 文件 POST **1.17s** 建单；**48.6s** 全部 succeeded、progress 全 100；批内仅 3 个入队；重试后 invoice 30→30、job 2 条且重跑成功 | `tests/test_batch.py`（7 条）+ 冒烟脚本 |
| S3-9 双人复核 | 提交人改金额（勾选）→ 整单确认 v4；审核人复核 **200**（`reviewed_by=15`、`reviewed_at` 落库、v5）；复核后改字段 **409**、重复复核 **200 且版本不变** | `tests/test_review.py`（8 条）+ 冒烟脚本 |
| D-069 真实票修复 | 同一 PDF 重传后金额 **1057.43**、税额 **10.57**、合计 **1068.00**、税号显示 `91130730MAK5MAN60W`；重识别时影像/行框请求 1→1 | `test_extractor.py`（横版/税号用例）+ 冒烟 |
| S4-1 canonical + SM3 | SM3 国标向量全过（`abc`/512bit/空串）；键序不同同摘要；`100`/`"100"`/`"100.00"` 三种字节；float/Decimal/非 str 键/`2^53`/循环/代理项全被拒；跨语言 Python↔Node 三字段逐字相等（`sm3=ae5c73b0…`，JS 默认 UTF-16 排序会得 `1504e4f3…`） | `pytest tests/test_crypto`（54 passed）+ `scripts/canonical_cross_check.{py,mjs}` |
| S4-2 SM2 签名 | **GB/T 32918.5 A.1 公开向量**逐位一致：`k=59276E27…` → `r=F5A03B06…20B3`、`s=B1B6AA29…C1AA`；双重哈希陷阱（摘要当消息 → 原消息验不过）；`s+n` 可塑变体被拒（gmssl 会验过）；`04` 开头公钥回归（gmssl 未修时 128→126 并抛 `TypeError`）；同明文两次签名不同、固定 K 相同 | `pytest tests/test_crypto`（105 passed，S4-1 54 + S4-2 51） |
| S4-3 SM4 对称层 | **GB/T 32907 A.1 向量** `681edf34…4246`；固定 IV 的 CBC 与 Node/OpenSSL `sm4-cbc` 逐字节相同（`61e48c1e…af5c`）；HMAC-SM3 4 组向量与 Node 相同（`bd4a3407…`）；篡改/截断/错 key → `mac_mismatch`，MAC 合法的坏填充 → `bad_padding`，坏填充+坏 MAC 先报 `mac_mismatch` | `pytest tests/test_crypto`（146 passed，累计） |
| S4-4 数字信封 | **2MB seal+open 0.095s**（seal 0.054 / open 0.041；254KB 真实票尺寸 22.4ms）；`wrapped_key` 112B=C1 64+C3 32+C2 16；C2 段与 C3 段篡改都 `mac_mismatch`；`payload_sha256` 篡改 `digest_mismatch`；SM2 与 gmssl 双向互操作；SM3 双后端同向量、SM4 与 gmssl 参考实现逐字节一致 | `pytest tests/test_crypto`（181 passed，累计）+ `pytest` 607 passed |
| S4-5 密钥库 | OpenSSL 生成 PKCS#8 → `dG` 与 `pkey -pubout` 逐字节一致；产物只有 `.p8.enc`（498B 密文 JSON）+ `.pub.pem`；自检解锁+签名+验签通过；**OpenSSL 独立验签成功**（`-pkeyopt distid:1234567812345678`）；错误口令/篡改密文/盐/迭代全失败；`key_fp=SHA-256(0x04‖X‖Y)` | `pytest tests/test_crypto`（205 passed，累计）+ `pytest` 631 passed |
| S4-6 证书 | CA + 用户证书真签发；`openssl verify` **OK**；`x509 -text` 含 KeyUsage(DS/NR/KE)、EKU=clientAuth、SKI/AKI、签名算法 `sm2sign-with-SM3`；运行期指纹与 openssl 一致；过期/未生效/吊销/错公钥/错角色五类判定齐全；签发后公钥一致性自检通过，无临时密钥文件 | `pytest tests/test_crypto`（220 passed，累计）+ `pytest` 646 passed |

**最弱环节**：`taxi_ticket` 整单只有 0.200（纸面约 300px 高、字太小）、`total_amount` 80%；
`vat_general` 的 amount/tax 靠"合计约束反解"补到 83–86%（见 D-033）。

## 5. 一条命令复现全部验收

```bash
cd /e/BiSheXZY/study-OCR
(cd backend && ../.venv/Scripts/python.exe -m alembic upgrade head)          # 16 表，head=0007
(cd backend && ../.venv/Scripts/python.exe -m alembic check)                 # 模型↔库零差异
(cd backend && ../.venv/Scripts/python.exe -m pytest)                        # 365 passed（默认跳过集成）
(cd backend && ../.venv/Scripts/python.exe -m pytest -m integration)         # 169 passed（需 Docker）
# S4-1 ~ S4-6 密码封装（离线、不连库）：220 passed（含 SM3/SM2/SM4 国标向量、2MB 信封 <1s、密钥库、证书）
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto -q)
# 生成密钥（OpenSSL 在 Docker 里跑；口令只走环境变量/交互，不进命令行历史）
STUDYOCR_KEY_PASSPHRASE='demo-pass-1234' .venv/Scripts/python.exe scripts/keygen.py --name alice --role submitter
# 建 CA + 给已生成的密钥签发角色证书（私钥经 stdin 做 CSR，不落盘）
bash scripts/ca_init.sh init
STUDYOCR_KEY_PASSPHRASE='demo-pass-1234' bash scripts/ca_init.sh issue alice submitter
bash scripts/ca_init.sh verify secrets/certs/alice.crt.pem
# 用 OpenSSL 独立验证我们签的名（注意必须显式 distid，否则默认 ID 为空会验不过）
bash scripts/openssl.sh pkeyutl -verify -pubin -inkey secrets/keys/alice/sign.pub.pem \
  -rawin -digest sm3 -pkeyopt distid:1234567812345678 -in msg.bin -sigfile sig.der
# OpenSSL 3.x 命令行（宿主机没装，用 Docker 里的）：见 D-078
bash scripts/openssl.sh version                              # OpenSSL 3.5.9（Git Bash/MSYS2）
powershell -NoProfile -File scripts/openssl.ps1 version      # 同一个镜像（Windows PowerShell 用这个）
# 镜像可换源/离线：OPENSSL_IMAGE=registry.example.com/alpine/openssl:3.5.9
bash scripts/openssl.sh genpkey -algorithm SM2 -out secrets/keys/alice-sign.pem
bash scripts/openssl.sh dgst -sm3 -binary somefile           # 独立复算摘要
# 跨语言一致性：Python 与 Node 各算一遍，三字段（canonical_b64/sm3/sha256）必须逐字相等
.venv/Scripts/python.exe scripts/canonical_cross_check.py
node scripts/canonical_cross_check.mjs
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
# S3-3 局部重识别验收（离线，不写库；40 张约 3 分钟）
.venv/Scripts/python.exe scripts/reread_eval.py --samples 40
# S3-4 识别产物契约：重新生成/校验 JSON Schema（提交物 docs/schemas/ocr_result.schema.json）
.venv/Scripts/python.exe scripts/export_ocr_schema.py --check
# 轮询识别进度（stage/progress/起止时间）
curl -s localhost:8000/api/v1/invoices/1/ocr-status -H "Authorization: Bearer <token>"
# S3-5 影像/行框（明文影像仅本地；密文或 prod 返回 409）
curl -s -o /tmp/inv.jpg -w "%{http_code} %{content_type}\n" \
  "localhost:8000/api/v1/invoices/1/image?page=1&variant=original" -H "Authorization: Bearer <token>"
curl -s "localhost:8000/api/v1/invoices/1/lines?page=1" -H "Authorization: Bearer <token>"
# S3-6 整单确认（失败 422/42201 + data.fields；成功 200 confirmed）
curl -s -X POST "localhost:8000/api/v1/invoices/1/confirm" \
  -H "Authorization: Bearer <token>" -H "Content-Type: application/json" -d '{"version":1}'
# S3-7 重复检测（strong 时 confirm 会 409）
curl -s "localhost:8000/api/v1/invoices/1/duplicate-check" -H "Authorization: Bearer <token>"
# S3-8 批量上传 / 汇总 / 重试（<id> 换成 batch_id）
curl -s -F "files=@a.jpg" -F "files=@b.jpg" localhost:8000/api/v1/invoices/batch -H "Authorization: Bearer <token>"
curl -s "localhost:8000/api/v1/invoices/batch/<uuid>" -H "Authorization: Bearer <token>"
curl -s -X POST "localhost:8000/api/v1/invoices/batch/<uuid>/items/1/retry" -H "Authorization: Bearer <token>"
# S3-9 审核人复核（替换为 reviewer token；复核后字段修改会 409）
curl -s -X POST "localhost:8000/api/v1/invoices/1/review" \
  -H "Authorization: Bearer <reviewer-token>" -H "Content-Type: application/json" -d '{}'
# 待复核 / 已复核筛选
curl -s "localhost:8000/api/v1/invoices?review_status=pending" -H "Authorization: Bearer <token>"
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
| D-059 | 队列用 `extract_status=need_confirm`（S3-2 时的定义）；**S3-9 起 reviewer 入口改为 `review_status=pending`（待复核）**；auto ≠ confirmed |
| D-060 | 重识别按契约异步 `reocr`（202）；只改 raw_text/confidence/source=ocr_retry，值不动；字段 bbox 改原图坐标 |
| D-061 | 外扩 max(8px,10%行高)、2× 放大（长边 1024）、1.5s 预算；审计只加 `field_reread` 枚举不写行 |
| D-062 | 识别产物契约唯一来源 `schemas/ocr.py`（types 转 re-export）；`field_value=raw_value`、`normalized_value=归一`；`GET /fields` conflicts 只回候选冲突；本卡无迁移 |
| D-063 | `GET /image` 按契约实现；明文仅 `STORAGE_ALLOW_PLAINTEXT`（密文/prod 409 待 S4）；PDF 固定 250 DPI；前端 blob 取图 |
| D-064 | 新增 `GET /lines`（服务端映射原图坐标、不返回文本）；canvas 字段/行框分级 + culling；缩放改 CSS 尺寸而非 transform |
| D-065 | 整单确认闸门顺序 必填→冲突→低置信未确认→validators；本人权限（reviewer 403）；幂等 + `version+1`；审计行留 S4-7；PATCH 不重写 |
| D-066 | 新增 `GET /duplicate-check` 覆盖 D-013；强重复 confirm 409；弱重复只提示；`duplicate_of` 由流水线写；影像重复纵深防御 |
| D-067 | 批量：迁移 0006（`invoices.batch_id`/`ocr_jobs.dispatched`）；服务端排队 ≤3 + `after_job_end` 补位；重试只建新 job；去重项只在 POST 响应 |
| D-068 | 双人复核（取代 D-017 金额限制）：提交人可改全部字段（金额须勾选）；审核人 `POST /review` 二审；`reviewed_at` 锁定修改；不加 status 枚举 |
| D-069 | 横版票金额规则排除"价税合计"、税号 O→0/I→1 纠偏（`field_value` 用纠正值、`raw_text` 留原样）、重识别只刷新详情不重拉影像 |
| D-070 | 字段表去掉"操作"列（无重识别/逐字段确认）；整单确认弹窗核对，服务端把全部字段置 confirmed；reocr API 保留但无前端入口 |
| D-071 | DELETE 改硬删除（仅 draft）：级联字段/行/作业/文件 + MinIO 对象，释放 sha256 可重传；删除后 404 不幂等 |
| D-072 | 业务状态前端派生：待确认(draft)/待复核(confirmed 未复核)/已复核(confirmed 已复核)；不加 DB 枚举；reviewer `/review` 入口同步 |
| D-073 | `gmssl==3.2.2` 落地（纯 Python）+ 传递依赖 `pycryptodomex==3.24.2` 显式锁；**不引 gmssl-python**；不用它的随机源；装完 numpy/paddle 未变 |
| D-074 | canonical JSON 11 条硬规则：码点序、无空格、拒 float/Decimal/非 str 键/`|int|>2^53-1`/循环/代理项；金额必须字符串；**JS 默认按 UTF-16 码元排序会算出不同摘要**（`1504e4f3…` vs 正确 `ae5c73b0…`） |
| D-075 | SM2 裁决：**只走国标 SM2-SM3**（`e=SM3(ZA‖M)`，ID=`1234567812345678`），`sign_bytes(data)` 传入参是 canonical 字节而非摘要；签名 DER 存 base64；K 只用 `secrets`；逐条封堵 gmssl 七个坑（`mode` 默认 0、`lstrip("04")`、不重试、不查 r/s 范围、畸形抛异常、默认 ID、`func.random_hex`） |
| D-076 | 对称层 EtM：`mac=HMAC-SM3(mac_key, aad‖IV‖密文)`、`mac_key=SM3(key‖"mac")`、`compare_digest`；**CBC/PKCS7 自实现**（gmssl 的 `pkcs7_unpadding` 不校验）；失败归类 `mac_mismatch/bad_padding/bad_ciphertext`；HMAC-SM3 用 OpenSSL 交叉向量（无国标 HMAC 向量） |
| D-077 | 性能改造：SM3/HMAC 走 `hashlib`（OpenSSL，2MB 0.008s，gmssl 回退）、SM4 走 `cryptography`（OpenSSL，2MB 0.03s）；`cryptography` 升为直接依赖；SM2 加密自组合（gmssl `encrypt` 的 K 不可注入）；信封 MAC 多绑 `wrapped_key`（不依赖 C3） |
| D-078 | 宿主机没有 openssl CLI：用 Docker 内 OpenSSL 3.5.9（`scripts/openssl.sh`，固定镜像）生成密钥/证书；不装系统软件、不用 `_kg` 造假密钥 |
| D-079 | 私钥口令保护：OpenSSL 生成 PKCS#8 → PBKDF2-HMAC-SHA256(200k,16B 盐)→AES-256-GCM，AAD 绑元数据；`key_fp=SHA-256(0x04‖X‖Y)`；解锁失败统一"口令错误"；`key_records` 落库属 S5（SCHEMA 无 key_fp/status 列） |
| D-080 | 证书：OpenSSL 签发（CSR 经 stdin，私钥不落盘）；**运行期不做链验签**（只指纹/有效期/公钥/吊销/OU 角色）；角色取自 OU（SCHEMA 无 role 列）；失败取值 `cert_expired/cert_not_yet_valid/cert_revoked/cert_public_key_mismatch/cert_role_mismatch` |

## 7. 下一步：S4-7 审计日志哈希链（★关键）

- 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 第 7 章 S4-7；
- 流程（AGENTS §11）：spec（`docs/spec/s4-7-*.md`）→ plan → 写码（用户已授权"不逐步确认"）；
- **⚠️ 本卡是阶段 4 唯一跨出 `crypto/` 的卡**：任务卡明确要求
  `backend/app/services/audit.py`、`GET /api/v1/admin/audit/verify`、`scripts/verify_chain.py`，
  必然要动 services + API + 测试；按任务卡执行（S4-8 回到 `crypto/`）；
- **S4-7 范围**（任务卡原文）：
  1. `audit.py`：字段 `prev_hash / entry_hash / ts / actor_id / action / object_type / object_id /
     payload_sha256 / trace_id`；`entry_hash = HMAC_SM3(prev_hash ‖ ts ‖ actor ‖ action ‖ payload_sha256)`，
     HMAC key 从环境变量读（进 `Settings` + `.env.example`），拼接用 **canonical 编码**固定；
     创世条目 `prev_hash` = 32 字节 0；
  2. **写入串行**：`pg_advisory_xact_lock` 锁链尾（保证不分叉）；`verify_chain()` 顺序复算，
     返回首个断链 `index` 与原因；暴露到 `GET /api/v1/admin/audit/verify`（admin）与 `scripts/verify_chain.py`；
  3. 单测：正常链通过；改中间一条 payload 报断链（正确 index）；**并发 50 条写入后仍为单链**；
- **必须先定的口径（spec 里写死）**：
  1. `ts` 用 UTC + 固定格式（如 `YYYY-MM-DDTHH:MM:SS.ffffff+00:00`，参与哈希的字符串要与存储一致）；
  2. HMAC key 从 `Settings` 读（新增 `AUDIT_HMAC_KEY`），缺失时启动即 `ConfigError`（同既有配置口径）；
  3. `audit_logs.action` 现有枚举（0001/0002/0005）：`login/login_failed/logout/forbidden/
     template_*/upload/ocr/extract/confirm_field/field_reread/…`；**`template_create`/`report_create`/
     `issue_cert` 仍缺**（遗留 #20/#37）——是否本卡补枚举 + 迁移 0008、以及是否本卡接入各业务审计行，
     在 spec 里决定；建议：**本卡只做"链 + 校验 + API + 脚本 + 测试"**，业务行接入随 S5 各接口
     （否则要改多个 service，超出本卡边界），并在 PROGRESS 写明；
  4. 并发测试需要真实 PostgreSQL（`@pytest.mark.integration`），单测用纯函数 + 假连接；
- **坑**（任务卡原文）：不加锁并发写会让两条记录共用同一 `prev_hash`（链分叉且无法检出）；
  `ts` 必须 UTC 且哈希里的格式固定，别用本地时间字符串；`prev_hash` 创世值不要写成空串；
- **验收**：篡改中间一条 payload 后 verify 报出正确 index；50 并发写入后 verify 通过。

## 8. 未闭合 / 需要用户参与的点

1. **push**：本地领先 origin **77 个 commit**（S3-1 ~ S3-9 + D-069~D-080 + S4-1~S4-6），AI 不推，由用户 `git push`；
2. ✅ 已闭合（S4-1）：`gmssl==3.2.2` 已装并锁进 `requirements.txt` / `requirements.lock.txt`（D-073）；
   装完 numpy/paddle/paddleocr 未变、全量单测 631 passed；`cryptography==50.0.2` 已在环境与 lock，S4-4 起升为直接依赖（D-077）；
3. **人工抽查**（AI 看不了图）：`backend/eval/results/preprocess/*_compare.jpg`、`layout/*_layout.jpg`、
   `s37-dup.png`、`ui-111-new.png` 等；真实 PDF 的框选对齐需要人眼确认；
4. `taxi_ticket` 整单 0.200：S3-3 已试过低置信二次识别（整体 97.3% 提升，但出租车票字太少，提升有限）；
   论文如实写"出租车票是已知短板"，不要在 S1 继续调；
5. 真实样本：`E:\BiSheXZY\发票样本集` 有 1 份 PDF（数电票）+ 1 张 PNG；论文写"合成集为主 + 真实件定性校验"；
6. 审计动作：`login_failed`/`logout`/`forbidden`/`field_reread` 枚举已在迁移里，但**都没有写审计行**（S4-7 哈希链）；
   `template_create`/`report_create`/`issue_cert` 也缺；S4-7 前一起补；
7. `GET /invoices/{id}/image` 已实现（S3-5），但**明文仅本地**（`STORAGE_ALLOW_PLAINTEXT=true` 且 `is_cipher=false`）；
   密文/生产返回 `409/40900`，页面显示"待 S4"——S4 接入信封后在此处解密，不做明文后门；
8. **D-071 硬删除是不可恢复的**（仅草稿可删）：原影像/识别结果/作业都删；论文需写明取舍；
9. `POST /reocr` 接口还在但**前端已无入口**（D-070）；`PATCH /fields` 的 `confirm` 参数保留（API 兼容）；
10. 管理端模板页已补（D-052），但**新建票种入口已按用户要求移除**（D-054）：票种固定 5 类 + unknown；
    新增票种必须改 SCHEMA/迁移/模板/样本并发版，不能从管理端新建；
11. 前端联调/回归走查：`docs/UI_TEST_FLOW.md`（含 test_sub/test_rev/test_admin 账号与种子数据状态）。

## 9. 工作方式约定（用户偏好，新会话请沿用）

- 用户是"放手型"：希望**替他做决定并给理由**，事后复核，不要每步都问；
- 但**功能级任务卡要走 spec → plan → 确认**（AGENTS §11），小修/bugfix 可只在对话里给设计；
- **2026-11-20 起**用户把审批策略改成"从不询问"：spec/plan 仍要落盘，但**不再等用户逐步确认**，
  拍板理由写进 spec/plan/DECISIONS，做完一次性汇报供事后复核（本轮 S4-1 即按此执行）；
- 汇报风格：先给**实测数字与命令输出**，再解释"为什么这样做"，**不要粉饰**（做不到就直说）；
- 每次收口都要更新 `docs/PROGRESS.md`，新增取舍写 `docs/DECISIONS.md`；
- 提交信息用 Conventional Commits（含任务号，如 `feat(s2-1): ...`）；**push 永远由用户做**。