# S1-4 实现计划（plan）

> 对应 spec：`docs/spec/s1-4-layout.md` ｜ 已实现完毕，计划与实现同步落盘

## Step 1 — 票种模板（`backend/app/ocr/templates/*.yaml`，5 个）

每个模板含：`type_code` / `type_name` / `aspect_ratio`（长宽比先验区间）/ `keywords`（带权重）/
`exclusive_keywords`（互斥词，命中扣分）/ `regions`（header/body/footer 归一化矩形），
`vat_general` 另有 `regions_portrait`（竖屏截图形态）。

**验证**：`load_templates()` 返回 5 个，`max_score` 均 > 0；单测 `test_templates_load_all_five_types`。

## Step 2 — 分类器（`layout.py::classify_ticket`）

- 标题区（上部 15%）关键字权重 ×3；标题区所有行拼接后再匹配一次（解决标题被切断）；
- 互斥词扣分；长宽比落在区间内 +1；
- 归一化到 0–3，`<0.25` 或与第二名差距 `<0.08` → `unknown`。

**验证**：5 类各一条用例 + 空列表 + 无关文本 + 真歧义（两个同构模板）→ 13 条单测全过。

## Step 3 — 区域划分（`layout.py::assign_regions`）

- **按内容框归一化**（`_content_box`）：用所有行的纵向范围做基准，而不是整图高度；
- 行 → header/body/footer（矩形内含判定，缝隙归 body）；
- block 聚类：垂直间距 ≤1.5×行高且同区域；
- 未知票种全部归 body + warning。

**验证**：区域覆盖每行、header/footer 单调、放大 2 倍归属不变、block 行数守恒。

## Step 4 — debug 可视化（`scripts/layout_demo.py`）

区域带 + 行框（按区域着色）+ 行号标签 + 顶部信息条 → `backend/eval/results/layout/*_layout.jpg`。
默认先跑 S1-2 预处理（区域是按纸面归一化的，带桌面背景会让 header/footer 落空）。

## Step 5 — 评估（`scripts/layout_eval.py`）

真实链路（预处理 → OCR → 分类）跑全量 60 张，输出判对率 / unknown 数 / 混淆矩阵 / 判错样本清单，
低于 0.95 返回非零退出码。

**实测**：**60/60 = 100%**（修完下面 Step 6 的几何缺陷之后）。

## Step 6 — 定位并修掉三个预处理几何缺陷（见 spec §7）

| 缺陷 | 发现方式 | 修法 |
|---|---|---|
| 退化四边形（其实是三角形） | 行程单 `007` 预处理后 OCR 20 → 0 行 | 最小边 + 对边长度均衡校验 |
| 四角点自交 | 同一张图的四角点排成"蝴蝶结" | 四个连续叉积同号校验 |
| 单边裁切 | 竖屏截图被裁成 611×1919，标题被裁掉 | 一边 >85% 另一边 <65% → 判可疑，只修旋转 |

**连带优化**：检测在小图（1000px）做、重采样在原图（先压到 `warp_max_side`）；
耗时 P50 77ms / P95 195ms（回到预算内）。

## Step 7 — 收口

- AGENTS §10.8 回填"票种识别与版面分块"锚点；
- `docs/DECISIONS.md` 补 D-028（关键字规则 vs 分类器）、D-029（四角点四项校验）；
- `docs/PROGRESS.md` 勾 S1-4、更新实测数字、记录 S1-2 指标口径变化；
- 提交 `feat(s1-4)`。

## 回滚与降级

| 情况 | 处理 |
|---|---|
| 判对率 < 0.95 | 先看混淆矩阵：若集中在 vat_general↔vat_digital，通常是标题被 OCR 读残 → 检查预处理是否把标题裁掉 |
| 区域分布异常（footer 恒为 0） | 检查是否漏跑预处理：区域是按纸面内容归一化的，带桌面背景会整体偏移 |
| 未知票种比例高 | 看 `warnings`：`score_too_low` 说明标题没读出来，`ambiguous` 说明两类模板关键字重叠 |