# S1-1 实现计划（plan）

> 对应 spec：`docs/spec/s1-1-samples.md`（待确认后执行）
> 每一步都有**可执行的验证命令**；任一步验证不过就停下来修，不往下推。

## 前置检查

| 项 | 要求 |
|---|---|
| Python | 用仓库根 `.venv`（3.11） |
| 依赖 | `Pillow`（已随 `backend/requirements.txt` 安装？**否 —— Pillow 尚未加入**，本计划第 1 步补） |
| 字体 | Windows 自带 `msyh.ttc` / `simsun.ttc`，脚本要探测 |
| 系统 OCR | `powershell -File scripts/ocr_probe.ps1`（S1-1 已验证可用，用于自动验收） |

---

## Step 1 — 依赖与字体探测

- 在 `backend/requirements.txt` 增加 `Pillow==12.3.0`（当前 venv 里的实际版本，需 `pip install` 后确认）
  与 `numpy==2.3.5`（扰动要用；同样是已装版本）。
- 新建 `scripts/sample_fonts.py`：探测中文字体（`C:/Windows/Fonts/msyh.ttc` → `simsun.ttc` → 报错），
  返回 `(regular_path, title_path)`；找不到时输出安装建议并退出 2。

**验证**

```bash
.venv/Scripts/python.exe -c "from PIL import Image, ImageDraw, ImageFont; print('PIL ok')"
.venv/Scripts/python.exe scripts/sample_fonts.py      # 打印实际使用的字体路径
```

## Step 2 — 版式原语（`scripts/invoice_layout.py`）

把"画字/画线/画表格/画章/画二维码占位"收敛成 4 个函数，**所有绘制都返回记录**：

```python
@dataclass
class DrawnText:
    label: str          # 字段名或版式标签
    text: str
    bbox: tuple[int,int,int,int]   # [x1,y1,x2,y2]

def draw_text(draw, xy, text, font, label, fill=(0,0,0)) -> DrawnText
def draw_table(draw, box, headers, rows, font) -> list[DrawnText]
def draw_seal(img, center, radius, text) -> None
def draw_qrcode_placeholder(draw, box) -> None
```

**关键**：`bbox` 由 `font.getbbox(text)` + 绘制原点算出，**不是事后估**。

**验证**：单测 `backend/tests/test_sample_layout.py` —— 画一个字符串，断言返回的 bbox 宽高 ≥ 字号的一半、
且 `bbox` 落在画布内。

## Step 3 — 五种票种模板（`scripts/invoice_templates.py`）

每个模板一个函数，签名统一：

```python
def render_vat_general(spec: TicketSpec) -> RenderedTicket
```

`TicketSpec` 里带：票种、画布尺寸、字段值（dict）、随机种子、可选扰动列表。
`RenderedTicket` 里带：`Image`、`list[DrawnText]`、`fields: dict[str, str]`。

**验证**：对每个模板生成 1 张干净图 + 打印字段数与框数（`vat` 应 ≥ 10 个框，`taxi` ≥ 5）。

## Step 4 — 扰动模块（`scripts/perturb.py`）

纯函数，输入 `Image` + 参数，输出新 `Image`：

```python
def rotate(img, deg) -> Image
def perspective(img, jitter_ratio) -> Image
def illumination(img, gradient_ratio, shadow_count) -> Image
def color_cast(img, red_gain, blue_gain) -> Image
def noise(img, sigma) -> Image
def blur(img, radius) -> Image
def overprint_seal(img, cover_ratio) -> (Image, tuple)   # 返回章的区域
```

**注意**：几何扰动会**改变坐标**，因此扰动必须在"排版完成、边界框已知"之后执行，并**同步变换 bbox**：
旋转/透视用同一个变换矩阵作用于四个角点，再取外接矩形。

**验证**：单测 —— 旋转 90° 后，一张已知位置的白块应出现在预期象限（用像素断言，不靠眼睛）。

## Step 5 — 生成与标注（`scripts/make_samples.py`）

流程：

```
for i in range(n):
    spec  = build_spec(ticket_type, rng)          # 字段值（含金额三件套等式、合法税号）
    img, drawn = template.render(spec)             # 干净图 + 框
    img, drawn = apply_perturbations(img, drawn, rng)   # 图与框同步变换
    img.save(out / f"{type}_{i:04d}.jpg", quality=88)
    ann.append(build_annotation(...))              # fields 的 box_index 指向 boxes 下标
write_jsonl(annotations)
```

- CLI：`--n 40 --out samples/synth_v1 --seed 20261118 --split dev:24,test:16`
- 每张票在 `perturbations` 里记录实际生效的扰动，便于 S6 做消融分组。

**验证**

```bash
.venv/Scripts/python.exe scripts/make_samples.py --n 40 --seed 20261118
ls samples/synth_v1 | wc -l          # 40
wc -l backend/eval/datasets/annotations_synth_v1.jsonl   # 40
```

## Step 6 — 标注校验器（`scripts/check_annotations.py`）

校验项（任一不过即 exit 1 并打印明细）：

1. JSONL 可解析、必填键齐全、`image` 文件存在；
2. `bbox` 为 4 个整数、满足 `0 ≤ x1 < x2 ≤ width`、`0 ≤ y1 < y2 ≤ height`；
3. `fields[*].box_index` 合法，且 `boxes[box_index].text` 与 `fields[*].text` **相等**；
4. 12 个字段名合法（与 spec §4 的表一致）；无重复字段；
5. `total_amount == amount + tax_amount`（容差 0.01，Decimal）；
6. 18 位税号校验位通过（复用 GB 32100 算法，S1-5 会再实现一遍到 `validators.py`，此处独立实现以免依赖尚未存在的模块）；
7. `dev` / `test` 的图片哈希不重叠。

**验证**：`python scripts/check_annotations.py --file backend/eval/datasets/annotations_synth_v1.jsonl` 全过；
再**故意改坏一行**（把 bbox 改成越界）确认它报错（证明校验器不是摆设）。

## Step 7 — 自动验收（spec §9 的五条）

新建 `scripts/verify_samples.py`：

| 检查 | 做法 |
|---|---|
| ① 渲染一致性 | 每个 `fields` 框内暗像素占比 vs 同图随机框，要求 ≥ 3× |
| ② 可读性 | 对 8 张（干净/扰动各半）跑系统 OCR，算与标注文本的字符相似度，干净 ≥ 0.85、扰动 ≥ 0.5 |
| ③ 标注自洽 | 直接调 `check_annotations.py` 的 `main()` |
| ④ 扰动真实性 | 统计灰度均值/σ/红色占比，与真实样本（129 / 64 / 33.5%）同量级（容差 40%） |
| ⑤ 划分完整 | dev 24 / test 16、每类都有、哈希不重叠 |

**验证**：`python scripts/verify_samples.py` 输出五项 PASS/FAIL 表；FAIL 时必须给出是哪几张图。

## Step 8 — 文档与收口

- `samples/MANIFEST.md`：真实样本登记（来源、授权、脱敏状态、**票种待人工确认**）+ `synth_v1` 生成参数与命令；
- `backend/eval/datasets/README.md`：标注格式、字段表、划分、如何重生成；
- `docs/EVAL.md` §1 数据集登记表：`synth_v1` 状态从"计划"改"已就绪"（数字留空，等 S1-6 填）；
- `docs/PROGRESS.md`：勾选 S1-1、记录产出与遗留（真实样本票种未确认）；
- `AGENTS.md` §10.8：若有可复用的锚点（如 `scripts/invoice_layout.py` 的绘制-记录模式）则回填；
- 提交：`feat(s1-1): 票据样本集与标注规范`。

## 回滚与降级

| 情况 | 处理 |
|---|---|
| Pillow 画中文仍出方块 | 换 `simsun.ttc`；再不行用 `ImageFont.truetype(..., index=1)` 指定 TTC 字体索引 |
| 系统 OCR 对合成图识别率过低（< 0.85） | 说明画的字太小或对比度不足 → 提高字号/加粗，而不是降低验收阈值 |
| 扰动后标注框漂移 | 统一用"角点变换 + 外接矩形"，并在 Step 4 的单测里锁死旋转 90° 的期望结果 |
| 时间不够 | 先做 `vat_general` + `train_ticket` 两类（覆盖横/竖两种形态）上线闭环，其余三类在 S1-2 前补齐；**但 spec 的 5 类仍保留在计划里** |

## 预计产出

7 个新文件 + 2 处依赖/文档改动；40 张图（本地，不入库）+ 1 份 JSONL 标注；三支可复用脚本
（`make_samples` / `check_annotations` / `verify_samples`）与一个布局模块。