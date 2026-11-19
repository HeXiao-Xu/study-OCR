# S1-2 实现计划（plan）

> 对应 spec：`docs/spec/s1-2-preprocess.md`（确认后执行）
> 每步都有可执行的验证命令；验证不过就停下修，不往下推。

## 前置

| 项 | 状态 |
|---|---|
| opencv | `opencv-python==4.6.0.66` ✅ 已装（paddleocr 2.7.3 的硬 pin） |
| numpy | `numpy==1.26.4` ✅ 已降（opencv 4.6 不兼容 numpy 2，见 D-025） |
| 数据集 | 60 张合成集 ✅（S1-1） |
| 系统 OCR | `scripts/ocr_probe.ps1` ✅（用于"预处理前后"对比） |

---

## Step 1 — 模块骨架与数据契约

- 新建 `backend/app/ocr/preprocess.py`：
  - `PreprocessOptions`（dataclass，8 个开关 + `max_side`）
  - `QualityMetrics`、`PreprocessResult`（字段严格对齐 spec §2）
  - `preprocess()` 主函数骨架：参数归一化（路径/bytes/ndarray）→ 逐步调用 → 组装结果
- 新建 `backend/app/ocr/__init__.py` 导出 `preprocess`、`PreprocessResult`、`PreprocessOptions`。

**验证**

```bash
cd backend && ../.venv/Scripts/python.exe -c "
from app.ocr.preprocess import preprocess, PreprocessOptions
r = preprocess('nonexistent.jpg')  # 应抛出可读异常（文件不存在），不是 KeyError
" 2>&1 | tail -2
```

## Step 2 — EXIF + 缩放 + 灰度

- `_load(source)`：读 bytes / PIL / ndarray → `ImageOps.exif_transpose` → BGR。
- `_resize_max_side(img, max_side)`：只缩不放，返回 `(img, scale)`。

**验证**：单测 `test_preprocess_exif.py`
- 造一张 100×200 的图，写入 EXIF Orientation=6 存成临时 JPEG → `preprocess` 后 `image.shape` 的高宽关系应为 200×100（宽高互换）。
- 超小图 80×60 → 输出不被放大。

## Step 3 — 票据定位（四角点）

- `_find_quad(img) -> np.ndarray | None`：Canny → 闭运算 → 轮廓 → `approxPolyDP` → 几何校验（凸 / 面积 ≥35% / 内角 ∈[50,130]）。
- `_order_quad(pts)`：顺时针排序（左上起）。
- 返回 `(quad, meta)`，`meta` 记录候选数与被拒原因（进 `warnings` 便于排查）。

**验证**：在 S1-1 数据集的带背景样本上跑，打印成功率；对 3 张失败样本单独调试（记录到 PROGRESS）。

## Step 4 — 透视 + 降级链 + 变换矩阵（🔴 核心）

- `_warp_perspective(img, quad)`：按边长估算输出尺寸，返回 `(warpped, M)`。
- `_warp_min_area_rect(img)`：`minAreaRect` 旋转校正，返回 `(img, M)`。
- `_compose_maps(*matrices)`：把"缩放矩阵 × 透视矩阵 × 旋转矩阵"合成为一个 `to_original`（3×3）。
- 降级链：① 轮廓 → ② minAreaRect → ③ 原图 + `quality_flag="low"`。
- `to_original` 的语义：`校正图齐次坐标 → 原图齐次坐标`（内部先乘回 `1/scale`）。

**验证**（单测 `test_preprocess_geometry.py`）
- 造一张白底黑框图，把"票面"画成已知梯形（用 `warpPerspective` 反向制造）→ `preprocess` 后：
  - `quad` 与真值四角误差 ≤ 2% 边长；
  - 用 `to_original` 把校正图四角映回原图，与 `quad` 误差 ≤ 2% 边长；
- 纯色图（无线条）→ `quad is None`、降级、`quality_flag="low"`、不抛异常。

## Step 5 — 残差旋转 + 照度 + 印章

- `_deskew(img)`：`minAreaRect` 主方向，`|角|>1.5°` 才转；
- `_normalize_illumination(img)`：大核闭运算估背景 → 除法归一化；
- `_detect_seal(img)`：HSV 双区间 → 开运算 → 连通域 → `(boxes, ratio)`，**不 inpaint**。

**验证**
- 造一张"左暗右亮"的渐变图 + 黑字 → 校正后左右两端的"字/背景"对比度差 ≤ 30%；
- 造一张盖了红色实心圆的图 → 检出的 `seal_boxes` 与真值 IoU ≥ 0.4。

## Step 6 — 质量标记与计时

- `_assess_quality(img, seal_ratio, rectify_failed)` → `QualityMetrics`（亮度/对比度/清晰度/印章覆盖）。
- 每步计时写 `timings_ms`（用 `perf_counter`）。

**验证**：对 60 张跑一遍，打印 `quality_flag` 分布与耗时 P50/P95，确认 P95 ≤ 300ms。

## Step 7 — Demo 脚本

`scripts/preprocess_demo.py`：
- `--dir samples/synth_v1 --limit 20 --out backend/eval/results/preprocess/`
- 每张输出一张**对照拼图**（左原图 / 右校正图，下方标注 quad、quality、耗时），便于人工抽查；
- 打印汇总表（成功率、quality 分布、耗时分位数）。

**验证**：`python scripts/preprocess_demo.py --limit 6` 生成 6 张拼图；用 `read_image` 看不了图，
因此额外断言：输出文件存在且尺寸 > 0（拼图仅用于**你**抽查，不参与自动判定）。

## Step 8 — 端到端增益（最硬的一条）

`scripts/preprocess_eval.py`：
- 从标注里挑 N 张（困难/干净各半），对每张：
  1. 原图 → 系统 OCR → 字段命中率；
  2. 预处理后 → 系统 OCR → 字段命中率；
- 输出对照表（每张 + 平均），**要求平均不下降**（下降即视为预处理损伤了文字，必须修）。

**验证**：`python scripts/preprocess_eval.py --n 8` 输出两列命中率，平均提升或持平。

## Step 9 — 收口

- `AGENTS.md` §10.8 回填"OCR 预处理"锚点；
- `docs/PROGRESS.md`：勾选 S1-2、记录 quality 分布/耗时/定位成功率/OCR 增益；
- `docs/DECISIONS.md`：补 D-025（opencv/numpy 版本锁的来龙去脉）；
- `backend/requirements.txt` 增加 `opencv-python==4.6.0.66`；`requirements.lock.txt` 重生成；
- 提交 `feat(s1-2): ...`。

## 回滚与降级

| 情况 | 处理 |
|---|---|
| 合成集定位成功率 < 70% | 先看是不是"桌面与纸面同色"（灰桌/白桌）→ 调整 Canny 参数或面积阈值，而不是放宽到无意义 |
| OCR 增益为负 | 大概率是照度过校正 → 先把 `illum` 关掉对比，确认后再调核大小 |
| 耗时超标 | 降 `max_side` 到 1280；再不行把照度改成"仅当亮度不均时触发" |
| 时间不够 | 先交付 EXIF + 缩放 + 透视 + 兜底链（这四步是主线），照度/印章可各自独立后补 —— 它们本来就是可关开关 |