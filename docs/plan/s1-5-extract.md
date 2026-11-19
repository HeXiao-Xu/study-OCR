# S1-5 实现计划（plan）

> 对应 spec：`docs/spec/s1-5-extract.md` ｜ 已实现完毕，计划与实现同步落盘

## Step 1 — 规则与类型

- `backend/app/ocr/templates/field_rules.yaml`：13 条规则（12 个业务字段 + 大写金额），
  每条含 anchors/direction/regex/max_distance/zone/value_index/occurrence/exclude/whole_line_anchors/if_multiple/tickets；
- `types.py` 增加 `ExtractedField`（value/raw_text/source_line_no/confidence/rule_id/rule_strength/bbox/candidates）
  与 `ExtractionResult`（fields/missing_required/conflicts/warnings）。

**验证**：`load_field_rules()` 返回 13 条；单测 `test_rules_load_all_fields`。

## Step 2 — 校验器（`validators.py`）

纯函数 + Decimal：金额三件套、中文大写金额解析、日期、发票代码/号码位数、USCC 校验位（GB 32100）。
失败只报告（`ValidationIssue`），由抽取器把该字段置信度折半。

**验证**：`tests/test_validators.py` 38 条，含"税号改一位必须报错"。

## Step 3 — 抽取器（`extractor.py`）

锚点优先级 → 同行右侧 → 邻近行（右取水平距离、下取垂直距离）→ 归一化 → 冲突消解。
锚点匹配忽略 OCR 插入的空格；`exclude` 排除表头陷阱；`whole_line_anchors` 处理无标签的名称行。

**验证**：`tests/test_extractor.py` 10 条（含同优先级冲突保留候选、缺失必填、税号错折半）。

## Step 4 — 演示与评估

- `scripts/field_demo.py`：一张图打印字段/置信度/来源行号/冲突/告警；
- `scripts/field_eval.py`：60 张跑完整链路，出字段级准确率表（金额按 Decimal、日期按 ISO、其余归一化精确比）。

**实测**：总体 85.6%（368/430）；强项 check_code 100%、invoice_code/date 96.7%、invoice_no 95%；
短板 amount 60%、tax 56.7%、total 76.7%（原因与后续见 spec §6）。

## Step 5 — 收口

- AGENTS §10.8 回填"字段抽取器与校验器"锚点；
- `DECISIONS.md` 补 D-030（规则式抽取 + 冲突不静默替换）；
- `PROGRESS.md` 勾 S1-5、记录字段级准确率表与短板；
- 提交 `feat(s1-5)`。

## 回滚与降级

| 情况 | 处理 |
|---|---|
| 某字段准确率突降 | 先看是否有新的表头行命中锚点 → 用 `zone` / `exclude` 收窄，不要改阈值 |
| 冲突数量暴增 | 多为同一字段多处印刷（号码重复），属正常；如果是表头误命中，检查 `exclude` |
| 校验误报（合法票被判错） | 期望值用 `pytest` 固定下来：`tests/test_validators.py` 是唯一口径 |