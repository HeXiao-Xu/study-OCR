# S1-6 实现计划（plan）

> 对应 spec：`docs/spec/s1-6-baseline.md` ｜ 已实现完毕

## Step 1 — 指标函数（`backend/eval/metrics.py`）

纯函数、无外部依赖：`iou` / `containment` / `match_detections` / `coverage_match` /
`edit_distance` / `char_accuracy` / `field_correct` / `field_accuracy` / `document_accuracy` /
`need_review_metrics` / `percentile`。

**验证**：`backend/tests/test_metrics.py` 23 条，全部用手算小样本断言（含 1/3 IoU、kitten→sitting 距离 3）。

## Step 2 — 配置（`backend/eval/configs/baseline.yaml`）

preprocess（5 开关 + max_side）、ocr（unclip_ratio / limit_side_len / angle_cls / drop_score）、
metrics（IoU 与待确认阈值）、rules 路径。S6 复制改参数即可。

## Step 3 — 跑批脚本（`scripts/ocr_baseline.py`）

- 逐张：预处理（可关）→ OCR → 检测指标（标注框经 `to_original` 逆矩阵映射到校正图坐标系）
  → 字符准确率 → 版面 → 抽取 → 字段/整单/待确认指标 → 计时；
- 支持 `--compare-preprocess`（同批跑一次关闭预处理的对照）、`--split`、`--limit`、`--dir`（无标注模式）；
- 输出 `baseline_<ts>.json`（不覆盖）+ `baseline_report.md`（Markdown 表格，可直接贴论文）。

## Step 4 — 三个实现期修正

| 问题 | 现象 | 修法 |
|---|---|---|
| 坐标系不一致 | 标注在原图、OCR 在校正图 → Hmean 0.09 | 用 `to_original` 的逆矩阵把标注映射过去 |
| 标注是元素级、输出是行级 | 合并成行后 Hmean 0.12 | 保持元素级真值 + 采用**覆盖匹配**（IoU≥0.5 或 85% 被包含） |
| 报告口径没写清 | 光看 0.948 不知道匹配方式 | Markdown 里写明"覆盖匹配"及原因 |

## Step 5 — 实测与收口

- 60 张：检测 Hmean 0.948 / 字符 0.816 / 字段 0.856 / 整单 0.500（预处理开）；
  关闭预处理：0.974 / 0.828 / 0.847 / 0.517；
- 耗时 P50：preprocess 111ms + ocr 1422ms + layout 52ms + extract 1ms = 1591ms；
- AGENTS §10.8 回填"评测脚本与指标函数"锚点；DECISIONS 补 D-031；PROGRESS 收口 M1；提交 `feat(s1-6)`。

## 回滚与降级

| 情况 | 处理 |
|---|---|
| 整单正确率 < 0.5 | 按任务卡要求回 S1-2/S1-5 排查，不能直接写进论文 |
| Hmean 突然掉到 0.x | 先查坐标系是否又搞错（标注与预测是否同一张图） |
| 报告数字与旧版不一致 | 指标只在 `metrics.py` 实现，先 `git log` 看它有没有被改 |
