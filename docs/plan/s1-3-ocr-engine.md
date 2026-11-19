# S1-3 实现计划（plan）

> 对应 spec：`docs/spec/s1-3-ocr-engine.md`
> 本轮已实现完毕（用户指示"配好 pip、开始 S1-3"），计划与实现同步落盘，便于回溯。

## 前置

| 项 | 状态 |
|---|---|
| pip 源 | 已配阿里云镜像（`pip.ini`：index-url + trusted-host + timeout 60） |
| paddlepaddle / paddleocr | ✅ `2.6.2` / `2.7.3`（首次运行下载 PP-OCRv4 模型 ~18MB 到 `~/.paddleocr`） |
| numpy | ✅ 1.26.4（被 `scipy/scikit-image/pandas` 顶到 2.4.6 后手动压回，见 D-027） |
| opencv | ✅ `opencv-python==4.6.0.66` + `opencv-contrib-python==4.6.0.66`（清掉了误装的 headless 5.1） |

## Step 1 — 依赖三角与安装

```bash
pip config set global.index-url https://mirrors.aliyun.com/pypi/simple/
pip install paddlepaddle==2.6.2 paddleocr==2.7.3
# 安装后必然发现 numpy 被顶到 2.x（scipy/scikit-image/pandas 的最新版要求 numpy>=2）
pip uninstall -y opencv-python opencv-python-headless
pip install numpy==1.26.4 scipy==1.11.4 scikit-image==0.21.0 pandas==2.1.4 opencv-python==4.6.0.66
```

**验证**：`import cv2, paddle, paddleocr` 全部成功（numpy 1.26.4 / cv2 4.6.0 / paddle 2.6.2）。

## Step 2 — 输出契约（`backend/app/ocr/types.py`）

`OcrLine`（text / score / box 四点 / line_no / y_center / x_left / height / `bbox()`）与 `OcrResult`
（lines / image_size / engine / engine_version / elapsed_ms / det_params / `mean_score` / `find_by_keyword`）。
用 Pydantic：可序列化进 `ocr_lines`，也可直接做 FastAPI 出参。

## Step 3 — 引擎封装（`detector.py` / `recognizer.py`）

- `OcrEngine`：懒加载 + 互斥锁的**进程内单例**（模型加载 5–10s，且 PaddleOCR 非线程安全）；
- `parse_paddle_result()`：把 2.7 的 `[[[box,(text,score)]]]` 解析成契约结构，**按 y 分桶再按 x 排序**给 `line_no`；
  遇到 3.x 的 dict 结构抛 `OcrContractError`（绝不静默返回空）；
- `recognize()`：整图检测+识别，空结果返回 `lines=[]`；
- `recognize_line()` / `recognizer.recognize_line()`：只识别不检测，供 S3-3 局部二次识别；
  空裁剪返回 `("", 0.0)` 不抛异常。

## Step 4 — 参数与配置

`OcrOptions`（lang / use_angle_cls / det_db_thresh / det_db_box_thresh / **det_db_unclip_ratio=1.6** /
det_limit_side_len=1600 / **drop_score=0.0** / device / model_root / show_log），
默认值可由 `Settings` 的 `ocr_*` 字段覆盖。`drop_score=0` 是刻意的：引擎层丢行会让低置信字段无从标记。

## Step 5 — 契约测试（`backend/tests/test_ocr_contract.py`，7 条）

排序与行号、`bbox()` 换算、空结果、**3.x 结构必须报错**、畸形行必须报错、单例、空裁剪降级。
全部不加载真实模型，秒级完成。

## Step 6 — 演示与统计（`scripts/ocr_demo.py`）

- `--image`：打印每行的 score / y / bbox / 文本，并报告中文占比、乱码字符、确定性；
- `--dir`：批量统计行数均值 / 平均置信度 / 推理耗时 P50·P95 / 零行样本告警；
- `--preprocess`：先跑 S1-2 预处理再识别（验证两段链路能拼起来）；
- `--out`：结果写 JSON（供 S1-6 的指标计算复用）。

## Step 7 — 实测数据（写进 PROGRESS）

| 场景 | 结果 |
|---|---|
| 单张 `vat_digital_landscape_001` | 41 行、平均置信度 ≈0.99、无乱码、两次结果完全一致、推理 1.99s（1924px 原图） |
| 批量 20 张（flight+taxi） | 行数均值 14.5（8–20）、平均置信度 0.958、推理 P50 944ms / P95 1541ms |
| 困难样本 `taxi_ticket_receipt_002` + 预处理 | 预处理 365ms → **7 行、置信度 0.92–0.99**；同一张图用 Windows OCR 只读出 0–2 个字段 |

> 最后一行是个重要发现：S1-2 里"远景小票读不出"的结论是**特定工具**（Windows OCR）的能力上限，
> 换成真正的引擎（PaddleOCR）+ 预处理后是可读的。这条已记进 PROGRESS 遗留项，S6 的评测基线要换成 PaddleOCR。

## 回滚与降级

| 情况 | 处理 |
|---|---|
| 模型下载失败（离线） | `OcrOptions.model_root` 指向预置目录；PROGRESS 里写明所需模型与放置路径 |
| 推理超预算 | 降 `det_limit_side_len` 到 1280（S6 做精度/耗时消融） |
| 相邻行粘连（识别结果串行） | 先确认 `det_db_unclip_ratio` 没被调大（>1.7 就会粘连），再考虑按区域切分检测 |