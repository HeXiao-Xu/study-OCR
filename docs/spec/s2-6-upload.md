# S2-6 文件上传 + MinIO + sha256 去重 — 设计（spec）

> 任务卡：S2-6 ｜ 契约：`docs/API.md` §2 `POST /invoices/upload` + SCHEMA §3.1（storage_key 形态）｜ 上游：S2-5 `create_draft`、S1 intake、MinIO 容器 ｜ 下游：S2-7 ARQ、S3 识别
> 状态：**已实现（2026-11-19）**；§7 的加密取舍按方案 A 落地，实测见 §8。

## 1. 目标与范围

打通"上传 → 校验 → sha256 去重 → 存 MinIO → 落库（文件 + 票据 + 作业）→ 返回 job_id"这条链，
重复影像在**写对象存储之前**就被拦住。

**做**：
- `services/storage.py`：MinIO 封装（put/delete/exists），对象名按 SCHEMA 生成；
- `services/upload_service.py`：校验、去重、存储、落库、补偿；路由保持薄（AGENTS §4.1）；
- `api/v1/files.py`：**`POST /api/v1/invoices/upload`**（契约路径；任务卡的 `/{id}/files` 不采用，见 §6）；
- `schemas/invoice.py` 补 `UploadData`；
- `tests/test_upload.py`：临时库 + 假 MinIO + 假设置（不依赖真实 MinIO 服务）；
- `.env.example` / `core/config.py`：明文调试开关（见 §7）。

**不做**：
- 真实 ARQ 投递（S2-7）；本卡只落 `ocr_jobs` 一行 `pending` 并返回 `job_id`；
- 信封/SM4 加密链路（S4-3/S4-4）——§7 决定本卡的临时路径；
- 影像下载 / 解密（S3-5 / S5-4）；
- 识别流水线（S3-1）。

## 2. 契约口径（API.md §2）

| 项 | 落地 |
|---|---|
| 路径 | `POST /api/v1/invoices/upload`，`multipart/form-data`，字段 `file` |
| 权限 | `submitter`；`reviewer` 403；`admin` **404**（权限矩阵） |
| 大小 | `MAX_UPLOAD_MB=20`（D-018），超限 `413/41300` |
| 类型 | `image/jpeg` / `image/png` / `application/pdf`；**按魔数判定**，不信任客户端 `Content-Type` |
| 图片 | Pillow `verify()` 确认真图（防改扩展名）；PDF 用 `pypdf` 校验且 **≤5 页** |
| 去重 | 先算 sha256 → 查 `invoice_files.sha256`；命中返回 `409/40901`，`data.invoice_id` 为既有票据；**不写 MinIO、不建新行** |
| 存储 | 对象名 `invoices/{yyyy}/{mm}/{uuid}.bin`（SCHEMA §3.1）；原文件名只做展示、绝不拼路径 |
| 落库 | `invoice_files` + `invoices` + `ocr_jobs` 同一事务；失败补偿删除已写对象 |
| 出参 | `202`，`{invoice_id, file_id, job_id, sha256, status:"draft", ocr_status:"pending", dedup:{hit:false, invoice_id:null}}` |
| 安全 | 响应/日志不得出现 `storage_key`、MinIO 路径；文件名去掉目录部分并做长度/控制字符清洗；错误不回显文件内容 |
| 幂等 | 同文件重复上传 → 409 + 既有 `invoice_id`（客户端直接跳详情） |

## 3. 设计

```
api/v1/files.py            POST /invoices/upload（薄路由：Depends → service → 202 壳）
services/storage.py        StorageService（MinIO put/delete/exists；key 生成）
services/upload_service.py 校验/去重/存储/落库/补偿；唯一业务入口
services/../ocr/intake.py  复用魔数判定（PDF_MAGIC）
```

- **流式 sha256**：按 1MB 块读 `UploadFile.file` 计算 sha256 与 size，不把整文件读进内存；
  之后 `seek(0)` 让 Pillow / pypdf / MinIO 复用同一个 spooled 文件对象
  （Starlette 超过 1MB 自动落磁盘临时文件，明文只在进程内存与临时文件之间短暂存在）。
- **先哈希后写对象**：去重判断必须在 `storage.put` 之前（任务卡坑③）。
- **补偿**：`storage.put` 成功而 DB 事务失败（含并发同 sha 的 `IntegrityError`）→ `storage.delete(key)`，
  再抛 `DuplicateInvoiceError` / `ServiceUnavailableError`；删除失败只告警，不掩盖原始错误。
- **并发同 sha**：唯一索引兜底；`IntegrityError` 后回查既有行并转 409，避免两个对象堆积。
- **job**：`ocr_jobs(stage=preprocess, status=pending, engine=paddleocr, retry_count=0)`；
  S2-7 接 ARQ 时在事务提交后入队（AGENTS §10.5 的"先落库再入队"）。

## 4. 配置与开关

`core/config.py` 新增：
- `storage_allow_plaintext: bool = True`（仅本地调试路径；`environment='prod'` 时为 true 直接 `ConfigError`）；
- `upload_max_pdf_pages: int = 5`（契约写死的 5 页，做成常量/配置均可，默认 5）；
- 复用已有 `max_upload_mb`、`minio_*`。

`.env.example` 同步说明：S4 接入前，本地可临时明文存储，`invoice_files.is_cipher=false`；
**prod 启动即拒绝**，防止把调试路径带上线。

## 5. 测试设计（`tests/test_upload.py`，临时库 + 假存储）

- 成功：JPEG（最小合法魔数 + Pillow 可打开）→ 202；返回字段齐全；`invoice_files` 行
  （sha256/size/mime/storage_key/is_cipher/page_count）与 `invoices`、`ocr_jobs` 一致；
  假存储里恰好 1 个对象，key 形如 `invoices/yyyy/mm/<hex>.bin`，**响应无 storage_key**；
- 去重：同字节二次上传 → 409/40901 + `data.invoice_id` 等于第一次的票据；假存储 put 次数仍为 1；
- 校验：空文件 422；超限 413（把 `max_upload_mb` 覆盖成 1，传 2MB）；改扩展名的文本 → 422；
  PDF 6 页 → 422；PDF 页数 ≤5 接受；
- 权限：reviewer 403、admin 404；
- 补偿：monkeypatch 落库函数抛异常 → 假存储记录到 delete，接口 5xx，DB 无残留行；
- 文件名清洗：`../../etc/passwd` 只保留 `passwd` 展示，不进入 storage_key。

## 6. 与任务卡的冲突（报告，按契约执行）

1. **路径**：任务卡 `POST /invoices/{id}/files`（给已有票据加文件）；契约是 `POST /invoices/upload`
   （上传即建票，返回 `invoice_id`）。按契约。
2. **大小**：任务卡写 10MB；D-018 已定 **20MB**（单一来源 `MAX_UPLOAD_MB`）。按 20MB。
3. **类型**：任务卡允许 TIFF；契约白名单只有 jpeg/png/pdf。按契约（TIFF 拒绝，测试覆盖）。
4. **storage key**：任务卡 `yyyy/mm/{sha256[:2]}/{sha256}.{ext}`；SCHEMA §3.1 定死
   `invoices/{yyyy}/{mm}/{uuid}.bin`。按 SCHEMA（可避免对象名暴露哈希前缀与扩展名）。
5. **流式**：任务卡要求"流式读块算 sha256、不整文件进内存"——已落实为分块哈希 + spooled 文件；
   但后续 Pillow/pypdf/MinIO 仍会从同一个 spooled 文件读取，不额外复制整份 bytes。
6. **ARQ**：任务卡要求"投递 ARQ"，但 ARQ 框架属 S2-7。本卡只落 `ocr_jobs.pending` 并返回 `job_id`；
   真正的入队在 S2-7 接上，届时不改上传接口形状。

## 7. 必须拍板：S4 之前，上传怎么"加密"

契约要求"内存中信封加密后写 MinIO"，但信封/SM4 在 S4-3/S4-4 才实现；SCHEMA §3.1 又允许
`is_cipher=false` 仅出现在本地调试配置。

- **方案 A（推荐）**：S2-6 走**本地调试明文路径**——`is_cipher=false`，prod 启动直接拒绝该配置；
  日志显式打 warning；S4 接入后默认切回密文并删除该开关。代价：本地 MinIO 里是明文票据，
  必须只在本机 Docker 卷中存在（论文"范围限定"如实写）。
- **方案 B**：没有加密就**不允许上传**（上传返回 503，等 S4 完成）。安全最干净，但 S2-6/S2-10/S3
  的上传链路全部停摆，阶段 2 验收做不下去。
- **方案 C**：把 S4-3/S4-4 的 SM4 信封提前到 S2-6。工作量最大，且会打乱 S4 的任务边界。

**已拍板（2026-11-19，方案 A）**：本地明文调试路径，`is_cipher=false`；`prod` 环境该开关为 true 时启动失败；
日志 warning；S4 接入信封后切密文并删除开关（D-044）。

## 8. 验收命令

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m pytest -m integration tests/test_upload.py -p no:warnings
../.venv/Scripts/python.exe -m pytest -p no:warnings
../.venv/Scripts/python.exe -m pytest -m integration -p no:warnings
# 手工（后端起、本地明文开关打开）：
curl -F "file=@样本.jpg" localhost:8000/api/v1/invoices/upload -H "Authorization: Bearer <token>"
curl -F "file=@样本.jpg" localhost:8000/api/v1/invoices/upload -H "Authorization: Bearer <token>"   # 409
```

通过标准：`test_upload.py` 全绿；同文件二次上传 409 且对象数不增加；假图片被拒；
响应与日志不含对象名；临时库/对象清理干净。

**实测（2026-11-19）**：`tests/test_upload.py` **11 passed**；`pytest 311 passed`、`pytest -m integration 129 passed`；
真实 uvicorn + MinIO 手工：上传 **202** → 二次 **409/40901 + 同一 `invoice_id`** → MinIO 只有 1 个对象（643B）
→ `is_cipher=false` → 对象与测试数据已清理；smoke 8 OK / 0 FAIL。

## 9. 收口

- `PROGRESS` 勾 S2-6 + 实测；`DECISIONS` D-044（S4 前明文调试路径、prod 拒绝；storage key 按 SCHEMA；
  ARQ 留 S2-7）；
- `AGENTS` §10.8 回填"文件上传与去重"锚点；
- `HANDOFF` 下一步 S2-7（ARQ + 入队）；
- 提交 `feat(s2-6): ...`；**push 由用户执行**。