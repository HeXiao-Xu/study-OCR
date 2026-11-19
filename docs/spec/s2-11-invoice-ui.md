# S2-11 前端票据列表与详情 — 设计（spec）

> 任务卡：S2-11 ｜ 契约：`docs/API.md` §2（列表/详情/字段修改/image）｜ 上游：S2-5 票据接口、S2-9 主框架、S2-10 上传页
> 状态：**已实现（2026-11-19）**；影像预览按方案 A 占位，实测见 §6。

## 1. 目标

提交人能按条件翻查自己的票据、打开详情核对字段并修改草稿；审核人能看到全量票据。
筛选条件同步到 URL（刷新/前进后退不丢）；金额/状态/票种/识别状态一眼可辨。

**做**：
- `types/invoice.ts` 扩成完整 `InvoiceOut / InvoiceField / LatestJob / InvoiceDetail / InvoiceListItem`
  （字段名 snake_case，与 `schemas/invoice.py` 对齐；金额是字符串）；
- `api/invoices.ts`：`listInvoices(params)`、`getInvoiceDetail`（已有）、`updateInvoiceFields(id, payload)`；
- `views/InvoiceList.vue`：筛选（状态/识别状态/票种/关键字/日期区间/排序）+ 分页 + URL query 同步 + 三态；
- `views/InvoiceDetail.vue`：票据头信息（票种/状态/金额/识别状态/质量标记/文件元数据）、字段表
  （值/归一化值/置信度/来源行/待确认/冲突）、草稿态行内编辑 + `PATCH /invoices/{id}/fields`；
- `utils/format.ts`：金额、日期（`Asia/Shanghai`）、票种/状态标签映射；
- `router`：`/invoices`（列表）与 `/invoices/:id`（详情）替换占位；`MainLayout` 给 reviewer 增加"票据查询"；
- 后端小修：`InvoiceListQuery.invoice_type` 的 Literal **漏了 `vat_digital`**（S2-8 已加票种），补上并加测试。

**不做**：影像预览真实接口（见 §7）；`ocr-status`/`fields` 独立接口；识别框选联动（S3-5）；
报销单/财务页；导出。

## 2. 列表

- 入参（全部走 query，URL 同步）：`status`、`extract_status`、`invoice_type`、`q`、`date_from`、`date_to`、
  `page`、`page_size`、`sort_by`（`created_at`/`invoice_date`/`total_amount`）、`order`。
- 默认：`page=1`、`page_size=20`、`sort_by=created_at`、`order=desc`。
- 列：**单号**（`invoice_code` + `invoice_no`；都为空显示 `INV-{id}`）、票种、价税合计、状态、识别状态、
  上传时间、操作（查看）。
- 状态/票种用 el-tag 配色；金额右对齐、两位小数；日期按 `Asia/Shanghai`。
- 空态/加载/失败三态；筛选变化回第 1 页；重置清空 query。
- 权限：submitter 只看自己（后端已隔离）；reviewer 全量；admin 菜单不出现、接口 404。

## 3. 详情

- 左侧：**影像预览占位**（见 §7）+ 文件元数据（文件名/MIME/大小/sha256 前 12 位）；不显示 `storage_key`。
- 右侧：票据头信息 + 字段表；字段顺序按核心字段表（金额/日期/号码/购销方）。
- 字段表：字段名（中文标签）、值、归一化值、置信度（<0.85 标红）、来源行、待确认/冲突标签。
- 草稿编辑：
  - 仅 `status=draft` 且 `extract_status ∈ {raw, need_confirm}` 可编辑（后端同规则）；
  - submitter **不能改金额类字段**（`amount/tax_amount/total_amount`，前端置灰；后端 42202 兜底）；
  - reviewer 改金额必须勾选"确认修改金额"（`confirm_amount=true`）；
  - 保存发送 `{version, fields:[{field_name, value, confirm:true}], confirm_amount}`；成功后重新拉详情；
  - `409/40900` 版本冲突 → 提示并刷新详情；`42201` 未决冲突 → 展示后端原因，不覆盖；
- 识别状态：显示 `latest_job.status/error`；`pending/running` 时给"刷新"按钮（不自动无限轮询）。

## 4. 类型与格式化

- `InvoiceOut` 按后端 schema 全量（含 `file_id/filename/mime/size/page_count/sha256/version/quality_flag`）；
- `InvoiceField` 含 `field_value/normalized_value/raw_text/confidence/bbox/source/source_line_no/need_review/conflict/conflict_value/confirmed`；
- 金额、日期、标签映射集中在 `utils/format.ts`，组件不各写一套。

## 5. 权限与错误

- 列表/详情 404 一律显示"不存在或无权访问"，不泄露存在性；
- 编辑失败保留用户输入（不清空表单），错误信息来自后端 `message`；
- 保存按钮防重复点击（`loading`）。

## 6. 验证

- `npm run typecheck` / `npm run build`；
- 后端：`pytest`（含新增 `invoice_type=vat_digital` 列表用例）；
- 无头 Edge：submitter 登录 → `/invoices` 看到上传的票据 → 点"查看"进详情 → 字段表渲染；
  修改一个非金额字段 → 保存 → 值落库并刷新可见；reviewer 列表能看到他人票据；admin 访问 `/invoices` → 404/403 体验；
  筛选 `status=draft` 后 URL query 变化，刷新页面筛选保持；
- 测试票据/字段/用户/MinIO 对象清理。

**实测（2026-11-19）**：`npm run typecheck` / `npm run build` 通过；后端 + Vite + 无头 Edge：
列表 `?status=draft&invoice_type=vat_digital` 刷新后仍命中；详情渲染字段表与占位"影像预览待 S3-5"；
`--steps` 点编辑 → 填 buyer_name → 保存 → 页面显示新值，DB `source=manual/confirmed=true/version 0→1`；
reviewer 菜单"票据查询"能看到全量；`smoke.py` 9 OK；测试数据/MinIO/Redis 已清理。
后端补 `vat_digital` 筛选后 `pytest -m integration tests/test_invoices.py -k list_filters` 通过。

## 7. 必须拍板：影像预览怎么办

`GET /invoices/{id}/image` 在 API.md 标注 **S3-5 / S5-4**（依赖 S4 的信封/解密），**当前未实现**。

- **方案 A（推荐）**：详情页先放**占位卡片**："影像预览待 S3-5（加密信封落地后）"，展示文件元数据；
  不改后端、不绕过 S4 的安全边界。S3-5 落地后替换预览组件。
- **方案 B**：现在加一个 dev-only 明文预览接口（`storage_allow_plaintext` 双重开关），只在本机调试可用；
  会引入一条"绕过加密"的代码路径，S4 前必须拆掉，风险高。
- **方案 C**：现在实现 `GET /invoices/{id}/image` 契约（读 MinIO + 返回字节）；但 S4 前没有信封，
  只能返回明文，且接口约定里的 `409/50030` MAC 失败等语义无法满足——属于**提前实现半成品**。

**已拍板（2026-11-19，方案 A）**：详情页用占位卡片"影像预览待 S3-5"；不改后端、不绕过 S4。

## 8. 收口

- `PROGRESS` 勾 S2-11；`DECISIONS` D-049（预览占位/URL 同步/编辑规则/后端 vat_digital 补漏）；
- `AGENTS` 锚点补充（列表/详情页）；
- `HANDOFF` 下一步 S2-12；
- 提交 `feat(s2-11): ...`；**push 由用户执行**。