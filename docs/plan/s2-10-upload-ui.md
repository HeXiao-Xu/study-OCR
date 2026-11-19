# S2-10 实现计划（plan）

> 对应 spec：`docs/spec/s2-10-upload-ui.md` ｜ 契约：`docs/API.md` §2 ｜ 状态：**已实现（2026-11-19）**，计划与实现同步落盘

## Step 1 — 类型与 API

- `web/src/types/invoice.ts`：`UploadData`（invoice_id/file_id/job_id/sha256/status/ocr_status/dedup）、
  `InvoiceDetail`（invoice 状态字段 + fields + latest_job）、`LatestJob`。
- `web/src/api/files.ts`：`uploadInvoice(file, onProgress)`（FormData + `silent: true` + 60s 超时）；
- `web/src/api/invoices.ts`：`getInvoiceDetail(id)`；轮询函数 `waitForOcr(id, onUpdate)`（可选，放这里）。
- `http.ts`：请求配置加 `silent?: boolean`；拦截器在 silent 时**不弹全局 ElMessage**（401 仍按刷新/跳登录处理）。

**验证**：`npm run typecheck`。

## Step 2 — 上传页

- `views/Upload.vue`：
  - `el-upload` 拖拽/多选/limit=20/accept；`on-change` 自己收文件，`show-file-list=false`；
  - tasks 状态机：`pending → uploading → uploaded → recognizing → done/failed/duplicate/local_error`；
  - 本地校验（扩展名 + MIME + ≤20MB）；缩略图 objectURL 与 `beforeUnmount` 清理；
  - 串行上传 + 单文件/总进度；409 绿色提示 + 查看链接；其他错误行内红字（不重复弹全局）；
  - 单文件成功给出查看链接（不自动跳批量的详情）。
- `router`：`/upload` 换组件，保留 `roles: ['submitter']`。

**验证**：typecheck/build；页面渲染冒烟。

## Step 3 — 轮询

- 按拍板方案实现 `waitForOcr`：3s 起指数退避（上限 15s）、总 120s；页面卸载中止；
- 终态：`ocr_status ∈ {succeeded, failed}` 或 `latest_job.status ∈ {succeeded, failed}`；
- 到点标记"识别时间较长"并停止。

**验证**：上传后观察行状态；worker 跑完显示失败原因（S3-1 未接入）。

## Step 4 — 冒烟脚本与实测

- `ui_smoke.mjs` 增加 `--upload <selector> <file>`（`DOM.getDocument` + `DOM.querySelector` + `DOM.setFileInputFiles`）。
- 起后端 + ARQ worker + Vite + 无头 Edge；创建 submitter；
- 步骤见 spec §7；记录实际输出。
- 清理测试数据（DB/MinIO/Redis/用户）。

## Step 5 — 收口

- `DECISIONS` D-048；`PROGRESS` 勾 S2-10；`HANDOFF` 下一步 S2-11；`AGENTS` 锚点；
- 提交 `feat(s2-10): 前端上传页（拖拽/进度/去重提示/识别轮询）`；**不 push**。

## 回滚与降级

| 情况 | 处理 |
|---|---|
| 后端/队列不可用 | 行内显示 503 原因；不无限重试（用户可手动重传） |
| 轮询接口缺失 | 按拍板方案；S3-1 落地后只换一个函数 |
| 大量文件 | 串行上传 + 每文件轮询；上限 20，避免把队列打爆 |
| objectURL 泄漏 | 移除任务/卸载页面时统一 revoke |