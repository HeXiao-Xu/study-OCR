# backend/eval/datasets/ — 数据集与标注

本目录只放**标注与配置**（小文件、可复现）；图像本体在 `samples/synth_v1/`，被 `.gitignore` 排除。

## 文件

| 文件 | 说明 |
|---|---|
| `annotations_synth_v1.jsonl` | 40 张合成样票的标注，一行一张 |
| `README.md` | 本文件 |

## 标注格式（JSONL，一行一张票）

```json
{
  "image": "samples/synth_v1/vat_general_landscape_001.jpg",
  "ticket_type": "vat_general",
  "form_factor": "landscape",
  "width": 1372,
  "height": 1002,
  "split": "dev",
  "annotator": "synth",
  "seed": 20261118001,
  "perturbations": ["background", "rotate", "illumination", "color_cast", "seal"],
  "boxes": [{"label": "invoice_code", "text": "0264369562", "bbox": [312, 268, 528, 300], "line_no": 0}],
  "fields": {
    "invoice_code": {"text": "0264369562", "value": "0264369562", "box_index": 0, "bbox": [312, 268, 528, 300]}
  }
}
```

### 字段说明

| 键 | 含义 |
|---|---|
| `image` | 相对仓库根的路径（换机器也能定位） |
| `ticket_type` | `vat_general` / `train_ticket` / `flight_itinerary` / `taxi_ticket` |
| `form_factor` | `landscape` / `portrait_strip` / `portrait_screenshot` / `receipt` |
| `width` / `height` | **扰动后**的图像尺寸（贴背景会改变尺寸，所以不能写模板尺寸） |
| `split` | `dev`（开发调参）/ `test`（只在 S6 报告指标时用） |
| `seed` | 该张票的随机种子 = `主种子*1000 + 序号`，可单张复现 |
| `perturbations` | 本张实际生效的扰动名，S6 可按此分组做消融 |
| `boxes` | 所有文本框：`label` 为字段名或版式标签，`bbox` 为 `[x1,y1,x2,y2]`（原图坐标，左上-右下） |
| `fields` | 12 个核心字段：`text` 票面原样、`value` 归一值、`box_index` 指向 `boxes` 下标、`bbox` 冗余一份便于直接用 |

### bbox 约定（与 AGENTS.md §5 一致）

- 字段层统一 **`[x1, y1, x2, y2]` 两点外接框**，原图坐标系；
- 检测训练所需的四点坐标由 `box` 派生（合成时字是水平的，几何扰动后用外接矩形即可）；
- 后端返回给前端的坐标也用这个格式，前端按 `scaleX/scaleY` 换算后画框。

### 版式标签（不是字段，但会出现在 `boxes` 里）

`title`、`label`、`status_bar`、`table_header`、`table_row`、`qrcode`、`footer`、`seal_zone`、
`crease`、`paper_zone`、`station_from`、`station_to`、`station_arrow`、`depart_time`、`seat`、
`id_card_masked`、`train_no`、`flight_no`、`cabin`、`plate`、`total_amount_upper`

## 字段与票种的对应

| 字段 | vat_general | train_ticket | flight_itinerary | taxi_ticket |
|---|---|---|---|---|
| `invoice_code` | ✅ | — | — | ✅ |
| `invoice_no` | ✅ | — | — | ✅ |
| `invoice_date` | ✅ | ✅（乘车日期） | ✅（填开日期） | ✅ |
| `buyer_name` / `buyer_tax_no` | ✅ | — | — | — |
| `seller_name` | ✅ | — | ✅（承运人） | ✅（出租公司） |
| `seller_tax_no` | ✅ | — | — | — |
| `amount` / `tax_amount` | ✅ | — | — | — |
| `total_amount` | ✅ | ✅（票价） | ✅（合计） | ✅（金额） |
| `check_code` | ✅ | — | — | — |
| `passenger_name` | — | ✅ | ✅ | — |

> 行程单的"票价 + 民航发展基金"没有拆成 `amount`/`tax_amount`：那会把语义扭成"税额"。
> 只标 `total_amount`，保持字段语义干净。

## 重新生成与验收

```bash
rm -rf samples/synth_v1
.venv/Scripts/python.exe scripts/make_samples.py --seed 20261118
.venv/Scripts/python.exe scripts/check_annotations.py --strict
.venv/Scripts/python.exe scripts/verify_samples.py
```

验收脚本检查五项：渲染一致性（框真的压着字）、OCR 可读性（字不是方块）、标注自洽、
成像覆盖（能覆盖真实样本所在工况）、划分完整（dev/test 数量与票种覆盖、无重复图）。