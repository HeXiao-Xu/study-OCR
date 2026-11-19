# S2-11 实现计划（plan）

> 对应 spec：`docs/spec/s2-11-invoice-ui.md` ｜ 契约：`docs/API.md` §2 ｜ 状态：**已实现（2026-11-19）**，计划与实现同步落盘

## Step 1 — 后端补漏 + 类型/API

- `schemas/invoice.py::InvoiceListQuery.invoice_type` 加 `vat_digital`；`tests/test_invoices.py` 增加
  `invoice_type=vat_digital` 的列表 200 用例（不再 422）。
- `web/src/types/invoice.ts`：扩成完整 `InvoiceOut/InvoiceField/LatestJob/InvoiceDetail/InvoiceListData/InvoiceListParams`。
- `web/src/api/invoices.ts`：`listInvoices`、`updateInvoiceFields`；`waitForOcr` 保持不变。
- `web/src/utils/format.ts`：`formatMoney`、`formatDateTime`、票种/状态/识别状态标签与 tag 类型。

**验证**：`pytest -q tests/test_invoices.py`；`npm run typecheck`。

## Step 2 — 列表页

- `views/InvoiceList.vue`：筛选栏（状态/识别状态/票种/关键字/日期区间/排序）、表格、分页；
- URL query 为唯一状态源：控件变化只改 query，`watch(route.query)` 拉数据（支持刷新/前进后退）；
- 空/加载/失败三态；行内"查看"进 `/invoices/:id`。
- `MainLayout` reviewer 菜单加"票据查询"；`router` 把 `/invoices` 换成列表页。

**验证**：typecheck/build；无头 Edge 打开列表，筛选项变化后 URL 带参。

## Step 3 — 详情页

- `views/InvoiceDetail.vue`：头信息、字段表、预览占位（按拍板方案）、识别状态；
- 草稿编辑：行内输入 + 保存；权限/金额确认；409/42201 处理；保存后重拉详情；
- 字段中文标签表集中在前端 `utils/format.ts`。

**验证**：真实上传一张票据 → 详情渲染；用测试字段验证修改落库（结束后清理）。

## Step 4 — 浏览器冒烟与回归

- 起后端 + Vite + 无头 Edge；创建 submitter/reviewer；
- 冒烟：列表 → 详情 → 修改字段 → 刷新可见；reviewer 列表；URL 筛选保持；admin 拒绝；
- `npm run typecheck` / `npm run build`；`pytest` / `pytest -m integration`；
- 清理测试数据。

## Step 5 — 收口

- `DECISIONS` D-049；`PROGRESS` 勾 S2-11；`HANDOFF` 下一步 S2-12；`AGENTS` 锚点；
- 提交 `feat(s2-11): 票据列表与详情（筛选/URL 同步/字段编辑）`；**不 push**。

## 回滚与降级

| 情况 | 处理 |
|---|---|
| 影像接口缺失 | 按拍板方案；不临时绕过 S4 |
| 字段版本冲突 | 提示 + 重新拉详情，保留用户输入 |
| 列表筛选过多 | 白名单参数由后端校验，前端只发契约字段 |
| 金额修改误操作 | 仅 reviewer + `confirm_amount` 勾选；submitter 前端置灰 + 后端 42202 |