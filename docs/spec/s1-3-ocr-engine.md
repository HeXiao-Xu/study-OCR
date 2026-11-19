# S1-3 PaddleOCR 检测识别最小闭环 — 设计（spec）

> 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` → S1-3
> 上游：S1-2 的 `preprocess()`（输出校正图 + `to_original`）、S1-1 的 60 张标注集
> 状态：已实现（本轮直接实现，spec/plan 事后落盘；要改的点直接说，我回退）

## 1. 目标与边界

**目标**：把"一张图"变成"带坐标与置信度的文本行列表"，作为 **S1-4 版面分块、S1-5 字段抽取、S3 核对页**
共同依赖的**输入契约**。这一层一旦结构不稳定，上面所有模块都会返工。

**边界**：
- 只封装 PaddleOCR 做检测 + 识别，**不做**字段抽取、不做版面理解（S1-4/S1-5）；
- 不做模型训练/微调（微调是可选加分项）；
- 不追求"SOTA 精度"，目标是稳定、可测、可替换。

## 2. 输入输出契约（🔴 最重要）

```python
@dataclass(frozen=True)
class OcrLine:
    text: str
    score: float            # 识别置信度 0–1
    box: list[list[float]]  # 四点坐标 [[x,y]×4]，校正图坐标系，顺时针
    line_no: int            # 排序后的行号（从上到下、从左到右）
    y_center: float
    x_left: float
    height: float

@dataclass
class OcrResult:
    lines: list[OcrLine]
    image_size: tuple[int, int]      # (宽, 高)
    engine: str                      # "paddleocr"
    engine_version: str              # 2.7.3
    elapsed_ms: float
    det_params: dict                 # 实际使用的检测参数（写进 ocr_jobs.params_json）
```

**坐标约定**：`box` 是**四点**（票据经透视校正后文本框可能仍略斜），字段层再由四点取外接矩形
`[x1,y1,x2,y2]`（AGENTS §5）。**坐标属于"校正图"坐标系**，要映回原图必须用 S1-2 返回的 `to_original`。

**为什么 `line_no` 由这一层给**：S1-5 的 `invoice_fields.source_line_no` 要能回查到 `ocr_lines.line_no`，
索引必须**确定性**（同一张图两次跑出来顺序一致），所以排序规则写死：先按 `y_center` 分桶（桶高 = 行高中位数的一半），
桶内按 `x_left`，最后从上到下编号。

## 3. 引擎封装

| 文件 | 职责 |
|---|---|
| `backend/app/ocr/detector.py` | `OcrEngine`（进程内**单例**，懒加载 + 互斥锁）：`recognize(image) -> OcrResult`；参数来自 `OcrOptions` |
| `backend/app/ocr/recognizer.py` | `recognize_line(crop) -> (text, score)`：只跑识别不跑检测，供 S3-3 的"局部二次识别"复用；空裁剪返回 `("", 0.0)` 不抛异常 |
| `backend/app/ocr/types.py` | `OcrLine` / `OcrResult`（Pydantic，保证可序列化、可做契约测试） |

**参数默认值**（全部可配置，见 §4）：

| 参数 | 默认 | 说明 |
|---|---|---|
| `LANG` | `ch` | 中文 |
| `use_angle_cls` | True | 票据常被倒着拍/倒着扫 |
| `det_db_unclip_ratio` | **1.6** | 票据文本密集，**超过 1.7 相邻行框会粘连导致识别串行** |
| `det_db_box_thresh` | 0.3 | 票据小字，适当放宽 |
| `det_db_thresh` | 0.3 | |
| `det_limit_side_len` | 1600 | 与 S1-2 的 `warp_max_side` 对齐；压测可降 1280 换速度 |
| `drop_score` | 0.0 | **不在引擎层丢行**——低分行也要保留，交给 S3-4 的置信度逻辑处理 |
| `device` | cpu | 本课题无 GPU |

> `drop_score=0.0` 是刻意的：引擎层丢行会让"识别率"看起来更好，但字段抽取再也看不到那些行，
> 低置信字段也就无从标记（S3-2 的队列会空）。**丢不丢是业务决策，不是引擎决策。**

## 4. 配置

新增 `OcrOptions`（dataclass，与 `PreprocessOptions` 同风格），默认值来自 `Settings`
（新增 `ocr_*` 几个字段），**禁止把参数写成散落的魔法数字**。S6 做对比实验时直接传参覆盖。

## 5. 版本与依赖（D-027）

- `paddleocr==2.7.3` + `paddlepaddle==2.6.2` 锁死：3.x 的 `predict()` 返回 `rec_texts/rec_scores/dt_polys`，
  与 2.7 的 `ocr.ocr(img, cls=True)` 返回 `[[[[x,y]×4], (text, score)], ...]` **完全不兼容**。
- 依赖三角（D-025）：paddleocr 2.7.3 硬 pin `opencv-python<=4.6.0.66` → 必须 numpy 1.x → 已锁 1.26.4。
- 模型首次运行会下载到 `~/.paddleocr`：**离线机器需预置模型目录**；`OcrOptions.model_root` 可指定。

## 6. 验收（程序化）

1. **结构契约**：`tests/test_ocr_contract.py` 用假引擎验证 `recognize()` 的解析与排序（不依赖真实模型）；
   并断言"若返回结构变化（如 3.x 的 dict）就抛清晰的 `OcrContractError`"——**库升级时先炸测试，而不是悄悄跑错**；
2. **真实推理**：`scripts/ocr_demo.py --image <样本>` 输出 ≥10 行；中文无乱码（无 `\ufffd`、CJK 占比 > 20%）；
3. **确定性**：同一张图跑两次，`[(text, round(score,4), box)]` 完全一致；
4. **批量统计**：`--dir` 跑 20 张，输出平均行数 / 平均置信度 / 单张耗时 P50/P95；
5. **坐标合法**：所有框在图像范围内、`line_no` 连续递增、`y_center` 单调不减（同桶内）；
6. **可替换性**：`OcrEngine` 背后是适配器接口，S6 换 CRNN 只改实现不改调用方。

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| 首次运行下载模型失败（离线/网络受限） | 明确失败信息里给出"手动下载到 `~/.paddleocr` 的方法"；`--model-root` 可指定目录 |
| CPU 推理慢（票据 1600px 长边） | 先测基线；超预算时降 `det_limit_side_len` 到 1280（S6 用消融对比精度损失） |
| 印章遮挡/低对比导致漏检 | 本层不修；交给 S1-5 的"关键字邻域容错"与 S3 的人工确认 |
| 3.x 升级把结构改掉 | 契约测试 + 版本锁，双保险 |

## 8. 不做

- 不做版面分块与票种判定（S1-4）；
- 不做多模型集成/投票；
- 不训练、不微调。