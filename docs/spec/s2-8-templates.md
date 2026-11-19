# S2-8 票种模板与规则管理 — 设计（spec）

> 任务卡：S2-8 ｜ 契约：`docs/API.md` §3 / §11 TemplateOut ｜ 上游：S2-1 模型、S1 的 layout/extractor/YAML ｜ 下游：S3-1 流水线从 DB 读模板
> 状态：**已实现（2026-11-19）**；§7 的 `vat_digital` 按方案 A 补齐，实测见 §6。

## 1. 目标与范围

让财务（admin）在后台维护票种模板与字段规格，`reviewer` 只读；`/templates/{id}/test` 能用某版模板
对一张图**即时**抽取字段（不建票、不落 MinIO）；运行时（S3-1）通过带 60s TTL 的注册表读 DB。

**做**：
- `schemas/template.py`：列表/详情出参、创建/更新入参、测试出参；
- `services/template_service.py`：列表（默认 `active=true`）、详情、创建、**升版本**（新行、旧行保留）、
  active 互斥、`field_rules` 随版本复制、正则/结构校验、`/test` 的抽取；
- `services/template_registry.py`：`active_templates()` 60s 缓存 + 写操作后 `invalidate()`（S3-1 复用）；
- `api/v1/templates.py`：5 条接口（列表/详情/创建/更新/测试）；
- `scripts/seed_templates.py`：用 S1 的 `ocr/templates/*.yaml` 做**一次性 seed**（幂等，已有数据则跳过）；
- `tests/test_templates.py`：CRUD/权限/版本/active 互斥/正则校验/缓存失效/`/test`（OCR 注入假结果）。

**不做**：
- 不改 `layout.py` 的版面区域（regions/aspect_ratio 不在 DB 契约里；S3-1 决定如何组合）；
- 不写 `audit_logs`（`template_create` 的枚举值也还没加，见 §7；D-040 原则）；
- 不做 `field_rules` 独立 CRUD（S0-3 §12.1 已定：随模板 PUT 的 `rules` 数组维护）；
- 不做模板前端（S2-12 之后）。

## 2. 契约口径

| 项 | 落地 |
|---|---|
| 列表 | `GET /templates`，reviewer/admin；`submitter` **403**；`active` 默认 true、`type_code` 过滤、分页；列表**不含** `field_specs_json` |
| 详情 | `GET /templates/{id}`：`{template: TemplateOut（含 field_specs_json）, rules: RuleOut[]}` |
| 创建 | `POST /templates`：admin；`type_code ^[a-z][a-z0-9_]{2,31}$` 且同 type 只能 v1；`match_keywords` 1–10 个非空；`field_specs_json` 对象；`active` 默认 false；201 |
| 更新 | `PUT /templates/{id}`：admin；`version` 必须等于该 type 的**最新版本**，否则 409 + `data.current_version`；生成 `version+1` **新行**返回新 `id`，旧行保留；`active=true` 时旧 active 置 false；`rules[]` 可选（复制旧版本规则后按 `field_name` upsert） |
| 测试 | `POST /templates/{id}/test`：admin；JPG/PNG ≤20MB；模板必须 `active=true`；不落库；返回 `{template_id,type_code,matched_keywords,overall_confidence,elapsed_ms,fields[]}`；OCR 异常 → `500/50010` |
| 校验 | 所有 `pattern`/`regex` 字符串 ≤500 且 `re.compile` 通过，否则 422；`field_specs_json` 必须是 `字段 → 对象`，`keywords` 非空；`strategy` 只允许契约的 5 值 |
| 缓存 | `active_templates` 60s TTL；任何人写模板后立刻 `invalidate()`（读到的仍是新数据） |

## 3. 设计

```
schemas/template.py        TemplateOut / TemplateDetailData / RuleOut / TemplateCreate / TemplateUpdate / TemplateTestData
services/template_service.py
  list_templates / get_template / create_template / update_template / test_template
services/template_registry.py   TemplateRegistry(ttl=60) + registry 单例
api/v1/templates.py        5 条路由（薄）
scripts/seed_templates.py  幂等 seed
tests/test_templates.py
```

- **field_specs_json 是"字段 → 锚定规格"**（API.md §3）：每项含 `anchors/keywords`、`direction`/`strategy`、
  `regex`、`max_distance`、`zone`、`occurrence`、`required` 等；`extractor.extract_fields(rules=...)`
  已支持注入规则列表，`/test` 直接把 DB 规格转成 `FieldRule`，**不改 extractor 的算法**。
- **field_rules 表**只存校验器的契约列（`pattern/validator/error_msg/priority/active`）；
  task card 的 `anchors/direction/max_distance/required` 放进 `field_specs_json`（SCHEMA 没有这些列）。
- **版本**：`PUT` 永远新建 `version+1` 行；`field_rules` 先复制旧版本，再按 `rules[]` upsert；
  旧 `active` 行在同一事务内置 false（部分唯一索引兜底）。版本冲突/并发 → 409。
- **`/test` 的 OCR 是可注入函数** `_recognize_image(data) -> (lines, image_size)`：
  生产用 `preprocess()` + `detector.get_engine().recognize()`；测试替换成假 `OcrLine`，不加载模型。
- **seed**：读 `ocr/templates/*.yaml`（5 个票种）+ `field_rules.yaml`，生成 v1 active 模板与每模板规则；
  若 `ticket_templates` 已有数据则跳过（不覆盖线上手工改过的模板）。

## 4. 与任务卡/契约的冲突（报告，按契约执行）

1. **task card 的模板字段**（`name/ticket_type/anchors/region_layout/enabled`）与 SCHEMA 不符：
   SCHEMA 是 `type_code/type_name/match_keywords/field_specs_json/version/active`。按 SCHEMA。
2. **task card 的规则字段**（`anchor_synonyms/direction/max_distance/required`）在 `field_rules` 表不存在：
   按契约放进 `field_specs_json`；`field_rules` 只存 pattern/validator/error_msg/priority/active。
3. **PUT 的 `rules[]`**：POST/PUT 的字段表没列，但 §12.1 明确"规则随模板 PUT 的 rules 数组维护"；
   本卡按 §12.1 接受可选 `rules[]`，不提供独立 rules CRUD。
4. **seed 时机**：契约写"启动时 seed"；本卡用**显式脚本**（幂等），不在 `create_app()` 里连库——
   否则所有测试/健康检查（DB 未起）都会被 seed 拖慢或失败。部署脚本在 S2-12/S7 调用即可。
5. **审计动作**：API 要求写 `action=template_create`，但 SCHEMA 枚举没有该值且哈希链在 S4-7；
   本卡不写审计行，登记遗留（与 D-040 一致）。

## 5. 测试设计（`tests/test_templates.py`，临时库）

- 权限：reviewer 列表/详情 200、写 403；submitter 列表 403；admin 全部 200。
- 创建：201、`version=1`、`active` 默认 false；重复 `type_code` → 409；非法 `type_code`/空 keywords/
  非法正则/未知 strategy → 422。
- 列表：默认只列 active；`active=false` 查历史；`type_code` 过滤；分页；列表无 `field_specs_json`。
- 详情：含 `field_specs_json` 与 `rules`（来自 field_rules）。
- 升版本：`PUT` 生成 v2 新 id、v1 保留、规则复制 + 差异 upsert；`version` 过期 → 409 且带 `current_version`；
  `active=true` 后旧版本 `active=false`；同 type 仍只有 1 条 active（部分唯一索引）。
- 缓存：registry 读到 v1 后直接改库不 invalidate → 仍是旧值；调用 `invalidate()` 后读到新值。
- `/test`：admin 200，响应含 matched_keywords/fields/overall_confidence/elapsed_ms；reviewer 403；
  模板未启用 422；假图片 422；OCR 抛异常 → 50010。

## 6. 验收命令

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m pytest -m integration tests/test_templates.py -p no:warnings
../.venv/Scripts/python.exe -m pytest -p no:warnings
../.venv/Scripts/python.exe -m pytest -m integration -p no:warnings
cd .. && .venv/Scripts/python.exe scripts/seed_templates.py --check
```

**实测（2026-11-19）**：`tests/test_templates.py` 9 单元 + 9 集成；`pytest 332 passed`、`pytest -m integration 146 passed`；
seed 5 模板/37 规则且二次跑跳过；真实 uvicorn 手工：admin 列表 5 个、`vat_digital` 详情 specs=10 rules=10、
`/test` 真 OCR 抽出 10 字段、matched 5 关键字、confidence 0.881、耗时 6.5s（冷启动）；reviewer 写 403；
smoke 8 OK；临时用户已清理（seed 模板保留）。

## 7. 必须拍板：SCHEMA 缺 `vat_digital`

- S1 的 `ocr/templates/vat_digital.yaml` 是数电票模板（D-023 明确数电票是主流形态）；
- 但 SCHEMA §9.6 的 `TicketType` 只有 `vat_general/train_ticket/flight_itinerary/taxi_ticket/unknown`，
  `ticket_templates.type_code` 与 `invoices.invoice_type` 都有 CHECK 约束。
- **方案 A（推荐）**：给 SCHEMA §9.6 补 `vat_digital`，`models/enums.py` 同步，生成 `0003` 迁移只放开 CHECK；
  seed 6 个模板（含数电票）。代价：契约变更一次；收益：与 S1/D-023 一致，数电票模板可维护、可测试。
- **方案 B**：S2-8 只 seed 契约里的 5 类，`vat_digital.yaml` 暂不入库。
  代价：数电票（主流形态）在后台不可维护，`/test` 也调不了它；S3 还要回头补契约。

**已拍板（2026-11-19，方案 A）**：SCHEMA §9.6 补 `vat_digital`，enum 同步，`0003` 迁移只放开两个 CHECK；
seed 5 个模板（含数电票）/37 条规则。实测见 §6。

## 8. 收口

- `PROGRESS` 勾 S2-8 + 实测；`DECISIONS` D-046（模板/规则的 DB 形态、seed 脚本、缓存与版本策略、vat_digital 补齐）；
- `AGENTS` §10.8 回填"模板与规则管理"锚点；
- `HANDOFF` 下一步 S2-9；
- 提交 `feat(s2-8): ...`；**push 由用户执行**。