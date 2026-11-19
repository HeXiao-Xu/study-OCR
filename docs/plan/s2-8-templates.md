# S2-8 实现计划（plan）

> 对应 spec：`docs/spec/s2-8-templates.md` ｜ 契约：`docs/API.md` §3 ｜ 状态：**已实现（2026-11-19）**，计划与实现同步落盘

## Step 0 —（若选方案 A）补 `vat_digital`

- `SCHEMA.md` §9.6 加 `vat_digital`（用户拍板后）；`models/enums.py::TicketType` 同步；
- `alembic/versions/0003_ticket_type_vat_digital.py`：drop/recreate `ck_ticket_templates_type_code` 与
  `ck_invoices_invoice_type`（只放开 CHECK，不写数据）；`alembic check` 零差异。

**验证**：`alembic upgrade head`；`pytest -m integration tests/test_models.py`（枚举断言同步）。

## Step 1 — 出入参（`schemas/template.py`）

- `TemplateOut`（列表：无 field_specs_json）、`TemplateDetail`（含）、`RuleOut`、
  `TemplateCreate`（type_code/type_name/match_keywords/field_specs_json/active/rules?）、
  `TemplateUpdate`（同上 + version）、`TemplateTestData`/`TestFieldOut`。
- Pydantic 校验：`type_code` 正则、keywords 1–10、field_specs 结构、strategy 白名单、pattern 长度。

**验证**：单元测试参数边界。

## Step 2 — 服务（`services/template_service.py`）

- `list_templates`：分页（S2-4）+ 白名单排序 + active/type_code 过滤；列表不返回 specs。
- `get_template`：模板 + rules（`template_id` 精确匹配）。
- `create_template`：版本 v1、active 互斥、规则写入；重复 type_code → 409。
- `update_template`：版本冲突 409；新行 v+1；复制旧规则 + upsert `rules[]`；active 互斥；
  写后 `registry.invalidate()`。
- `test_template`：active 校验 → 图片校验（JPEG/PNG、≤20MB、Pillow）→ `_recognize_image` →
  DB specs 转 `FieldRule` → `extract_fields` → 组装出参。
- 正则/结构校验函数 `validate_field_specs`（递归找 `pattern/regex`）。

**验证**：`pytest -m integration -q tests/test_templates.py -k "create or update or list"`。

## Step 3 — 注册表与接口

- `services/template_registry.py`：60s TTL 缓存 active 模板快照；`invalidate()`。
- `api/v1/templates.py`：5 条路由；`main.py` 挂载；校验错误码按 API（403/404/409/422/50010）。
- `50010` 新异常 `OcrEngineError`（500，`500/50010`）。

**验证**：HTTP 权限/状态码用例。

## Step 4 — seed 脚本

- `scripts/seed_templates.py`：读 6（或 5）个 layout YAML + `field_rules.yaml`；
  幂等（表非空则跳过，`--check` 只打印）；生成 v1 active 模板 + 每模板规则；
  `field_specs_json` 由字段规则推导（direction→strategy 映射；tickets 过滤）。
- 不覆盖已有数据；不调用服务层版本逻辑（seed 是受控的初始写入）。

**验证**：临时库跑 seed → `--check` 显示 6/5 个模板、规则数正确；二次跑不重复插入。

## Step 5 — 测试与回归

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m pytest -m integration tests/test_templates.py
../.venv/Scripts/python.exe -m pytest
../.venv/Scripts/python.exe -m pytest -m integration
../.venv/Scripts/python.exe -m alembic check
```

- 覆盖 spec §5；`/test` 用假 OCR（不加载模型）。
- 手工（可选）：起后端，admin 用 curl 创建模板/升版本/测试。

## Step 6 — 收口

- `DECISIONS.md` D-046；`PROGRESS.md` 勾 S2-8；`AGENTS.md` 锚点；`HANDOFF.md` 下一步 S2-9；
- 提交 `feat(s2-8): 模板/规则管理与 60s 热更新`；**不 push**。

## 回滚与降级

| 情况 | 处理 |
|---|---|
| `vat_digital` 不加 | 跳过 0003，seed 5 类；`vat_digital.yaml` 留到契约补齐 |
| seed 重复执行 | 幂等：已有模板即跳过；不覆盖手工编辑 |
| 版本并发 | 唯一 `(type_code, version)` + 部分唯一 active 兜底；IntegrityError → 409 |
| `/test` OCR 太重 | 测试注入假识别；生产端点只跑单张、不落库 |