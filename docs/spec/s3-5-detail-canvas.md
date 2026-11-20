# S3-5 详情页图像与字段并排 + 框选高亮 — 设计（spec）

> 任务卡：S3-5 ★关键 ｜ 契约：`docs/API.md` §2 `GET /invoices/{id}/image`（已定义）、§2 详情、
> `SCHEMA.md` §3.3/§3.4 ｜ 上游：S3-3（bbox 原图坐标）、S3-4（字段契约）、S1-2（预处理 `to_original`）
> 状态：**已实现（2026-11-20）**；实测：`GET /image` 260403B JPEG + 缓存头正确、`GET /lines` 40 行框、
> 显示 532px 时 `scale=0.338854`、放大两次 `0.487898`（×1.44）、`drawMs 0.39/0.48ms`；单元 422 / 集成 211 全绿。

## 1. 目标与范围

票据详情页从"占位图 + 字段表"升级为**左图右表**：图上画字段框与行框，hover 字段高亮框、点框定位字段，
滚轮缩放/拖拽平移后框仍对齐；低置信黄底、必须人工红底并给出可解释文案。

**做**：

| # | 产出 | 说明 |
|---|---|---|
| 1 | `backend/app/services/image_service.py`（新） | 按契约渲染原图/预处理图：照片原字节、PDF 第 N 页按 **250 DPI** 渲染 PNG |
| 2 | `backend/app/api/v1/invoices.py` | `GET /{id}/image`（契约已定义）+ `GET /{id}/lines`（**新增**，行框数据） |
| 3 | `backend/app/services/line_service.py`（新） | 行框 DTO：`ocr_lines` 四点 → 原图 `[x1,y1,x2,y2]`，**不返回文本** |
| 4 | `web/src/components/ImageCanvas.vue`（新） | `<img>` + canvas 叠层；缩放/平移/框选/高亮/culling/绘制耗时自测 |
| 5 | `web/src/components/FieldTable.vue`（新） | 从详情页抽出的字段表：行底色、hover/点击联动、冲突候选文案、重识别按钮 |
| 6 | `web/src/utils/bbox.ts`（新） | 纯函数：缩放系数、框换算、视口裁剪、命中测试（唯一换算实现） |
| 7 | `web/src/views/InvoiceDetail.vue` | 左图右表布局；影像 blob 加载/失败态；页码切换；字段-框双向联动 |
| 8 | `web/src/api/{http,invoices}.ts` | `getBlob`（二进制不过 JSON 解包）+ `fetchInvoiceImage` + `fetchInvoiceLines` |
| 9 | 测试 | 后端 image/lines 单测+集成；前端 `typecheck/build` + `ui_smoke --eval` 数值断言 |
| 10 | 文档 | `API.md` §2（image 实现/限制 + lines 新接口）、`PROGRESS`、`DECISIONS D-063/D-064`、`HANDOFF`、`AGENTS` 锚点 |

**不做**：字段值"校验失败错误码文案"的实时计算（留给 S3-6，见 §2 C2）；整块区域重识别（契约无入口）；
影像水印/脱敏打码（S6/S7 再说）；PDF 多页同时并排（只按 `page` 翻页）。

## 2. 已拍板与偏离（用户 2026-11-20 确认）

| # | 问题 | 裁决 |
|---|---|---|
| Q1 | 影像接口安全口径 | **按契约实现** `GET /{id}/image`（`page`/`variant`、ETag、`no-store`）；**明文仅当 `STORAGE_ALLOW_PLAINTEXT=true`**（本地 dev），密文/生产返回 `409/40900`"加密影像解密尚未接入（S4）"；前端带 Bearer 取 **blob** 再 `objectURL`，不用 `<img src>` 直链 |
| Q2 | 校验失败红底文案 | **用已有数据**：`review_level=manual` → 红底 + "必须人工核对"；`conflict` → 红底 + 候选文案（`field_value` / `conflict_value`）。不新增实时校验接口 |
| Q3 | 框的范围 | **字段框 + 行框**：新增 `GET /{id}/lines?page` 返回行框（**不含文本**），点击行框定位所属字段 |

其余我定的取舍：

| # | 冲突/风险 | 处理 |
|---|---|---|
| C1 | 行框在 `ocr_lines` 是**校正图**四点坐标，字段 bbox 是**原图**坐标 | `GET /lines` 在**服务端**用 `preprocess.to_original` 映射成原图外接框（PDF 恒等），前端只用一种坐标；**不改表结构**（不新增列） |
| C2 | 任务卡要"校验失败红底 + 错误文案"；校验错误目前不落库 | 用 `conflict` 候选 + `manual` 档位文案代替（Q2）；真正的 `FieldError` 文案在 S3-6 confirm 返回 |
| C3 | 行框接口 API.md 没有 | 本卡新增并**先写契约**（`GET /invoices/{id}/lines`，权限/入参/出参/错误码）；不返回行文本，减少票面明文暴露面 |
| C4 | PDF 渲染 DPI | 固定 **250 DPI**（与 S3-1 `prepare_pages(pdf_dpi=250)` 一致），否则框会按比例全偏 |
| C5 | AI 无法目视核对"框是否对齐" | 用 `ui_smoke --eval` 做**数值断言**（框坐标 = bbox×缩放比、缩放后仍一致、绘制耗时 <16ms）+ 截图交用户抽查 |
| C6 | `total_amount_upper` 没有中文标签 | 顺手补 `FIELD_LABELS` 与字段排序（S3-4 才进白名单） |

## 3. 接口契约

### 3.1 `GET /api/v1/invoices/{id}/image`（API.md 已定义，本卡实现）

- 权限：本人或 `reviewer`；`admin` → 404；类型：二进制流；幂等：是；
- 入参：`page`（int，默认 1，PDF 页码，从 1 开始）、`variant`（`original`｜`preprocessed`，默认 `original`）；
- 出参：**200**，图片字节：
  - `original`：JPEG/PNG 原样返回 `Content-Type=image/jpeg|image/png`；PDF 按 `page` 以 **250 DPI** 渲染为 PNG；
  - `preprocessed`：照片走 `intake.prepare_input`（与流水线同一套预处理）输出 PNG；PDF 与 `original` 相同（D-035 不预处理）；
  - 响应头：`Content-Type`、`ETag="<sha256(response bytes)>"`、`Cache-Control: private, no-store`、
    `X-Content-Type-Options: nosniff`；
- 错误：`404/40400`（不可见/不存在）；`409/40900` 密文影像/加密未接入（**不返回任何明文片段**）；
  `422/42200` `page` 越界或 `variant` 非法；
- 备注：明文仅本地调试（`STORAGE_ALLOW_PLAINTEXT=true` 且 `file.is_cipher=false`）；
  S4 接入信封后在此处解密，响应结束立即释放；**绝不返回 `storage_key`/MinIO 路径/预签名 URL**。

### 3.2 `GET /api/v1/invoices/{id}/lines`（**本卡新增**）

- 权限：本人或 `reviewer`；`admin` → 404；类型：同步；幂等：是；实现：S3-5
- 入参：`page`（int，默认 1）
- 出参：**200**，`data` = `{invoice_id, page_no, lines: [{line_no, bbox: [x1,y1,x2,y2], score, field_name}]}`

```json
{"invoice_id": 101, "page_no": 1,
 "lines": [{"line_no": 3, "bbox": [423, 357, 900, 395], "score": 0.973, "field_name": "total_amount"}]}
```

- `bbox` 是**原图**坐标（服务端已从校正图四点映射）；`score` 三位小数；
- `field_name`：若有字段的 `(source_page_no, source_line_no)` 命中该行则给出，否则 `null`；
- **不返回行文本**（票面原文只经字段 `raw_text`/`field_value` 暴露，缩小明文面）；
- 错误：`404/40400`；`422/42200` `page` 越界；`409/40900` 与 image 相同的加密未接入条件
  （映射坐标需要重跑预处理，照片必须能拿到原图；PDF 不需要但为口径统一同样判定）。

## 4. 坐标与渲染

```
原图（浏览器展示的 img）  ← 字段 bbox / 行框 bbox 都用这个坐标系
  ↑ get /image?variant=original            ↑ get /lines
  └─ 照片：原文件字节                        └─ ocr_lines 四点（校正图）→ to_original 映射
  └─ PDF：第 N 页按 250 DPI 渲染 PNG           （PDF：恒等）
```

前端换算（只允许在 `utils/bbox.ts` 一处实现）：

```
scaleX = displayWidth / naturalWidth        // displayWidth 由容器宽度决定
scaleY = displayHeight / naturalHeight      // 等比缩放时 scaleX == scaleY，但按两轴分别算，防以后拉伸
displayBox = [x1*scaleX, y1*scaleY, x2*scaleX, y2*scaleY]
```

缩放/平移**不改变** `img` 像素与 DOM 变换（避免 canvas 被 CSS 放大发虚）：
`zoom` 直接改图片的 CSS 宽高（`displayWidth = baseWidth × zoom`），平移用外层容器的 `scrollLeft/scrollTop`。
canvas 宽度 = `displayWidth × devicePixelRatio`，并按 DPR 缩放上下文；每次重绘只画**与可视矩形相交**的框（culling）。

## 5. 前端组件

### `ImageCanvas.vue`

- props：`src`（objectURL）、`imageWidth/imageHeight`（自然尺寸，可选，默认从 img `naturalWidth`）、
  `fieldBoxes`、`lineBoxes`、`activeField`、`hoveredField`、`page`、`pageCount`；
- emits：`select-field(fieldName)`、`hover-field(fieldName|null)`、`update:page`；
- 交互：
  - 滚轮缩放（1×–4×，以光标位置为锚点保持不跑偏）；拖拽平移（`scrollLeft/Top`）；
  - hover 字段 → 对应字段框加粗/高亮，所属行框淡蓝；hover 行框（有 `field_name`）→ 反向高亮字段；
  - 点击字段框或行框 → `select-field`（行框所属字段为空时只高亮该行框）；
  - `activeField` 变化（来自表格点击）→ 自动把该框滚进可视区并闪烁一次；
- 绘制：字段框按档位着色（auto 绿 / review 黄 / manual 红；冲突红虚框）；行框 1px 灰蓝、选中行框 2px；
- 性能：`requestAnimationFrame` 合帧；`lastDrawMs` 滚动平均暴露在 `data-draw-ms`（DEV/冒烟可读）；
- 调试属性（供 `ui_smoke --eval` 数值断言）：`data-image-size="w×h"`、`data-scale`、
  `data-field-boxes`（JSON：字段名 → 展示坐标框）、`data-visible-lines`、`data-draw-ms`。

### `FieldTable.vue`

- props：`fields`、`editMode`、`editable`、`editableNames`（Set）、`reocring`（Set）、`activeField`、`hoveredField`；
- emits：`hover-field`、`select-field`、`reread`、`update:editable`（输入框双向）；
- 行样式：`manual` 或 `conflict` → 红底（`.row-danger`）；`review` → 黄底（`.row-warning`）；
  `active`/`hovered` → 高亮描边；冲突行在"值"下加一行小字 `冲突候选：A / B，未自动替换`；
  `manual` 行加 `必须人工核对`；
- 表格滚动容器 `ref` 暴露给父组件用于"点框把行滚进视口并闪烁"。

### `InvoiceDetail.vue`

- 布局：`.detail-grid` 左侧影像卡（`minmax(380px, 44%)`）、右侧字段卡；窄屏（<1100px）上下堆叠；
- 影像加载：`fetchInvoiceImage(id, page)` → `URL.createObjectURL`，组件卸载/切页时 `revokeObjectURL`；
  409 时显示"影像暂不可用（加密链路待 S4）"而不是空白；
- 行框加载：`fetchInvoiceLines(id, page)`；失败/加密未接入时静默降级为"只画字段框"（不阻塞页面）；
- PDF 多页：`page_count > 1` 时显示上一页/下一页（`page` 状态，切换时重载影像与行框）；
- 联动：`hoveredField`（表格 hover → 图上高亮；图上 hover → 表格行高亮）、`activeField`（点击 → 滚动+闪烁）；
- 保留现有编辑/保存/重识别逻辑（抽到 FieldTable 后由父组件继续持有状态）。

## 6. 测试设计

**后端单元（默认 `pytest`，假 storage）**
- `image_service.render_original`：JPEG → 原字节 + `image/jpeg`；PDF → 用真 PyMuPDF 生成的 2 页 PDF，
  page=2 渲染为 PNG 且尺寸符合 250 DPI（A4 → 约 2067×2921，断言 ±2px）；
- `render_preprocessed`：照片走 preprocess（假图）→ PNG；PDF 与 original 相同；
- 加密判定：`is_cipher=true` 或 `storage_allow_plaintext=false` → `ConflictError`；
- `page` 越界/0/负 → `ValidationFailedError`；
- `line_service`：构造 `ocr_lines` + 假 `to_original=2×`，断言 bbox 被映射、`field_name` 命中/未命中、
  **返回结构里没有 `text` 字段**。

**后端集成（临时库 + 假 storage）**
- `GET /image`：本人 200（Content-Type/ETag/no-store/nosniff）、reviewer 200、他人 404、admin 404、
  加密 409、page 越界 422；
- `GET /lines`：本人按页返回、`page` 越界 422、加密 409、他人 404；
- 真实 MinIO 手工冒烟（S3-5 任务卡没有强制，但 plan 里跑一遍本地 dev）。

**前端**
- `npm run typecheck` / `npm run build`；
- `ui_smoke --eval` 数值断言（真实后端 + Vite）：
  1. `data-image-size` 与接口返回图片自然尺寸一致；
  2. 某字段展示框 = bbox × `data-scale`（误差 ≤ 1px）；
  3. 点两次放大后 `data-scale` 翻倍、同一字段框坐标按新比例重算后仍在图片范围内；
  4. `data-draw-ms` ≤ 16（滚动平均）；
  5. `data-visible-lines` ≤ 总行数（culling 生效）；
- 截图 2 张（原始 + 放大）交用户目视抽查（AI 看不了图，如实说明）。

## 7. 验收命令

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m alembic check          # 本卡无迁移（head 0005）
../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/python.exe -m pytest -m integration -q
cd ../web && npm run typecheck && npm run build
# 真实冒烟（uvicorn + worker + Vite + 临时账号）：上传 → 打开详情 → 数值断言 + 截图
node ../scripts/ui_smoke.mjs "http://127.0.0.1:5173/invoices/<id>" --view 1440x900 \
  --eval "JSON.stringify(document.querySelector('canvas')?.dataset)" --shot /tmp/s35.png
```

**验收指标**：抽 20 个框做数值核对（bbox×scale 与 canvas 绘制坐标一致，误差 ≤1px）；
缩放后仍一致；`data-draw-ms < 16`；行框数 = 接口返回数。

## 8. 收口

- `PROGRESS` 勾 S3-5 + 实测（image/lines 接口、数值断言、绘制耗时、截图路径）；关闭遗留 #44/#45（影像可见）；
  新增遗留：明文影像只限本地、S4 后切解密；校验失败文案待 S3-6；行框接口为本卡新增契约；
- `DECISIONS`：**D-063**（影像接口按契约实现 + 明文仅本地 + PDF 250 DPI + blob 取图）；**D-064**（行框接口新增、
  服务端映射原图坐标、不返回文本；前端 canvas + 字段/行框分级渲染与 culling）；
- `API.md` §2（image 实现标记/限制、lines 新接口）；`AGENTS` §10.8 补"详情页影像画框"锚点；
  `HANDOFF` 下一步 S3-6；
- 提交 `feat(s3-5): ...`（push 由用户）。