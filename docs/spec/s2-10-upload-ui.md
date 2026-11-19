# S2-10 前端上传页 — 设计（spec）

> 任务卡：S2-10 ｜ 契约：`docs/API.md` §2 `POST /invoices/upload` / `GET /invoices/{id}` ｜ 上游：S2-6 上传接口、S2-7 作业、S2-9 登录/主框架
> 状态：**已实现（2026-11-19）**；轮询按方案 A（详情接口），实测见 §7。

## 1. 目标

提交人能在一个页面里**拖拽/多选**上传票据（最多 20 个），逐文件看到状态与进度；
重复影像给"已上传"提示并能跳到已有票据；上传成功后跟踪识别状态直到终态或 120s 超时。

**做**：
- `web/src/api/files.ts`：`uploadInvoice(file, onProgress)`（multipart + 进度；409 能读到 `data.invoice_id`）；
- `web/src/api/invoices.ts`：`getInvoiceDetail(id)`（轮询用；S2-11 复用）；
- `web/src/types/invoice.ts`：`UploadData` / `InvoiceDetail`（与后端 schema 一致）；
- `web/src/views/Upload.vue`：
  - `el-upload` 拖拽 + 多选（`limit=20`），`accept` 只放 pdf/jpg/png，`show-file-list=false`（自己渲染表格）；
  - 本地校验：扩展名/MIME、单文件 ≤ **20MB**（D-018；任务卡写的 10MB 已过时）；
  - 缩略图（图片 `URL.createObjectURL`，PDF 用图标占位），移除任务时 `revokeObjectURL`；
  - 逐文件上传（串行，避免同时压 20 个识别任务），`onUploadProgress` 更新单文件进度与总进度；
  - 状态：等待 / 上传中 / 已上传+识别中 / 识别完成 / 识别失败+原因 / 重复（绿色）/ 本地校验失败；
  - 409/40901：**绿色提示 + "查看已上传票据"链接**（`/invoices/{id}`），不弹红色错误；
  - 轮询：详见 §6；120s 到点停止并提示"识别时间较长"；
  - 单文件成功给出"查看"链接；**批量不自动跳转**（会把其余文件的进度页甩掉），单文件时可跳详情（占位页）。
- `router`：`/upload` 从占位换成 `Upload.vue`（角色 submitter 不变）；
- `scripts/ui_smoke.mjs` 增加 `--upload <selector> <file>`（CDP `DOM.setFileInputFiles`），用于真实上传冒烟。

**不做**：后端上传接口改动；发票列表/详情页（S2-11）；识别流水线（S3-1）；MinIO 直传/分片。

## 2. 契约口径

| 项 | 落地 |
|---|---|
| 入参 | `multipart/form-data`，字段 `file`；单个文件 |
| 上限 | `MAX_UPLOAD_MB=20`（前端同值；后端最终裁决） |
| 成功 | `202`，`data = {invoice_id, file_id, job_id, sha256, status, ocr_status, dedup}` |
| 重复 | `409/40901`，`data.invoice_id` = 既有票据；页面显示"该票据已上传"并给跳转 |
| 错误 | 413 超限 / 422 类型或内容非法 / 503 队列或存储不可用；逐文件展示原因 |
| 权限 | 仅 `submitter` 可上传；`reviewer` 403、`admin` 404（后端已实现） |

## 3. 页面结构

```
el-upload (drag, multiple, limit=20, accept, custom on-change)
  └ 拖拽区 + "选择文件"（未上传时）
表格（自维护 tasks）：
  文件 | 大小 | 进度 | 状态 | 识别 | 操作（查看 / 移除）
顶部：总进度、成功/失败/重复计数
```

## 4. 上传与错误处理

- `api/files.ts` 用 `http` 的默认实例发 multipart（`Content-Type` 交给浏览器生成 boundary，**不手写**）。
- 给 http 增加一个 **`silent`** 配置：上传页自己决定提示（409 绿色、其余行内红色），
  避免拦截器再弹一个全局红条；401 仍走自动 refresh/跳登录。
- 409 判定用 `ApiError.code === 40901`，从 `error.payload?.data?.invoice_id` 取 id。
- 串行上传：一个完成再发下一个，`onUploadProgress` 的 `loaded/total` 换算百分比；
  总进度 = 已完成文件数 / 总数（正在传的文件按比例计入）。

## 5. 轮询（§6 拍板后落地）

- 终态判定：`invoice.ocr_status ∈ {succeeded, failed}` 或 `latest_job.status ∈ {succeeded, failed}`；
- 间隔 3s 起、指数退避上限 15s，总时长 **120s** 上限；到点停止并标记"识别时间较长"（不无限请求）；
- 页面卸载时清理 timer/标记，避免离开后继续请求。

## 6. 需要拍板：识别状态从哪拿

任务卡要求轮询 `GET /invoices/{id}/ocr-status`，但该接口在 API.md 标注 **S3-1 / S2-7**，**目前没有实现**
（S2-7 只做了作业状态机与入队）。

- **方案 A（推荐）**：用已存在的 `GET /invoices/{id}`（S2-5）读 `invoice.ocr_status` + `latest_job.status` 轮询。
  不改后端、不新增半成品接口；S3-1 落地 `ocr-status` 后只换 `api/invoices.ts` 里的轮询函数。
- **方案 B**：现在补一个最小 `GET /invoices/{id}/ocr-status`（读最新 `ocr_jobs`，先不给 stages/progress），
  贴近契约但会动后端、且 S3-1 还要重写。
- **方案 C**：上传成功后不轮询，只给"查看"链接；任务卡的"轮询到终态"不交付。

**已拍板（2026-11-19，方案 A）**：轮询 `GET /invoices/{id}` 的 `ocr_status`/`latest_job`；S3-1 落地 `ocr-status` 后只换
`web/src/api/invoices.ts::waitForOcr`。

## 7. 测试与验收

- `npm run typecheck` / `npm run build`；
- 后端 + Vite + 无头 Edge（`ui_smoke --upload`）：
  1. submitter 登录 → `/upload` 页面渲染拖拽区与表格；
  2. 上传 1 个合法 JPEG → 行状态"已上传/识别中"，ARQ worker 跑完后显示"识别失败（S3-1 未接入）"
     （这是 S2-7 的真实行为，不是假数据）；
  3. 同文件再传 → 行显示"该票据已上传"（绿色）+ 查看链接，不弹红色全局错误；
  4. 上传文本改扩展名的假图 → 行显示本地/后端校验失败原因；
  5. 校验页面在 120s 内停止轮询（用短超时参数或日志观察）。
- 冒烟结束清理测试用户/票据/文件/MinIO 对象。

**实测（2026-11-19）**：`npm run typecheck` / `npm run build` 通过；后端 + ARQ worker + Vite + 无头 Edge：
上传真实 JPEG → 行"失败 / 识别失败：TaskNotReadyError: OCR 流水线未接入（S3-1）"（真实行为）；
同文件重传 → "已上传 / 该票据已上传 + 查看链接"（无红色全局错误）；`.txt` → 本地拒绝"只支持 JPG/PNG/PDF"；
`scripts/smoke.py` **9 OK / 0 FAIL**；测试用户/票据/MinIO 对象/Redis key 已清理。
实现中修掉一个真实 bug：push 后修改原始 task 对象导致界面不刷新（改为操作数组响应式代理）。

## 8. 收口

- `PROGRESS` 勾 S2-10；`DECISIONS` D-048（轮询用 detail 接口的取舍、串行上传、409 绿色提示）；
- `AGENTS` 锚点补充（上传页 + api/files）；
- `HANDOFF` 下一步 S2-11；
- 提交 `feat(s2-10): ...`；**push 由用户执行**。