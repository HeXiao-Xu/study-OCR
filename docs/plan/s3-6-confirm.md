# S3-6 字段修正与整体确认实现计划（plan）

> 对应 spec：[`docs/spec/s3-6-confirm.md`](../spec/s3-6-confirm.md)（已确认）｜
> 契约：`API.md` §2 `POST /invoices/{id}/confirm`（已定义）｜ 状态：**已实现（2026-11-20）**，Task 1–4 全部执行并验证
> 执行方式：Task 1→4 顺序；每个 Task 跑完验证命令再继续。

**目标**：补 `POST /confirm` 服务端闸门（必填/冲突/低置信逐字段确认/validators），通过后 `status` 与
`extract_status` 同事务置 `confirmed`、`version+1`；失败 `422/42201` + `data.fields=[{field_name,error}]`。
**不重写** S2-5 的 `PATCH /fields`。

**架构**：把规则来源提成共享 `rule_source.resolve_rules`（流水线与 confirm 同源）；闸门抽成纯函数
`collect_confirm_errors`（可单测）；`confirm_invoice` 只做权限/幂等/版本/落库；前端加"确认整单"+错误列表。

**技术栈**：Python 3.11 / FastAPI / SQLAlchemy 2.0 / Pydantic v2 / Vue 3 + TS（无新增依赖）。

## Global Constraints

- 命令用 `.venv/Scripts/python.exe`；默认后端目录 `E:\BiSheXZY\study-OCR\backend`。
- **本卡不加迁移**（head 保持 `0005`）；不改已提交迁移。
- 金额 `Decimal`；`version` 乐观锁语义与 PATCH 一致；confirm 成功后 `version += 1`（同一次提交）。
- **不写 `audit_logs` 行**（S4-7 哈希链未实现，现在写=伪造 hash）；只打应用日志且**不含字段值**。
- `confirm` 只信**库内当前值**，不接受前端"已校验"标记；`422` 明细用 `data.fields`。
- 提交 Conventional Commits（`feat(s3-6): ...`）；**push 由用户执行**。

## 文件结构

| 文件 | 职责 |
|---|---|
| `backend/app/services/rule_source.py`（新） | `ResolvedRules` + `resolve_rules`（DB 优先/YAML 回退） |
| `backend/app/services/ocr_pipeline.py` | 改为调用 `rule_source.resolve_rules`（行为不变） |
| `backend/app/services/invoice_service.py` | `collect_confirm_errors`（纯函数）+ `confirm_invoice` |
| `backend/app/schemas/invoice.py` | `ConfirmRequest` |
| `backend/app/api/v1/invoices.py` | `POST /{id}/confirm` |
| `backend/tests/{test_rule_source,test_confirm}.py`（新） | 规则来源 + confirm 全套 |
| `web/src/api/invoices.ts`、`types/invoice.ts`、`views/InvoiceDetail.vue` | 确认按钮 + 422 字段明细 |
| `docs/API.md`、`docs/PROGRESS.md`、`docs/DECISIONS.md`、`docs/HANDOFF.md`、`AGENTS.md` | 契约与收口 |

---

### Task 1: 规则来源共享化（`rule_source.py`）

**Files:**
- Create: `backend/app/services/rule_source.py`
- Modify: `backend/app/services/ocr_pipeline.py`
- Test: `backend/tests/test_rule_source.py`（新）

**Interfaces:**
- `ResolvedRules{rules: list[FieldRule], source: str, template_id: int | None}`
- `resolve_rules(db, ticket_type, *, registry=None) -> ResolvedRules`

- [ ] **Step 1: 写失败测试**

```python
"""S3-6 规则来源：DB active 模板优先、库空回退 YAML；流水线与 confirm 必须同源。"""
from __future__ import annotations

import pytest
from alembic import command
from sqlalchemy import create_engine, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

from app.models.field_rules import FieldRule
from app.models.ticket_templates import TicketTemplate
from app.services import rule_source, template_registry
from app.services.template_seed import build_seed_templates
from tests.db_helpers import alembic_config, temporary_database

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def rule_database() -> URL:
    with temporary_database("study_ocr_rules_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def session_factory(rule_database: URL):
    engine = create_engine(rule_database.render_as_string(hide_password=False), poolclass=NullPool)
    try:
        yield sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    finally:
        engine.dispose()


@pytest.fixture()
def clean_db(session_factory):
    with session_factory() as db:
        db.execute(sa_text("TRUNCATE users RESTART IDENTITY CASCADE"))
        db.commit()


def test_falls_back_to_yaml_when_no_template(session_factory, clean_db) -> None:
    template_registry.registry.invalidate()
    with session_factory() as db:
        resolved = rule_source.resolve_rules(db, "vat_digital")

    assert resolved.source == "yaml"
    assert resolved.template_id is None
    assert {rule.name for rule in resolved.rules} >= {"invoice_no", "total_amount"}


def test_uses_active_db_template(session_factory, clean_db) -> None:
    seed = next(item for item in build_seed_templates() if item.type_code == "vat_digital")
    with session_factory() as db:
        template = TicketTemplate(
            type_code=seed.type_code, type_name=seed.type_name,
            match_keywords=seed.match_keywords, field_specs_json=seed.field_specs_json,
            version=1, active=True,
        )
        db.add(template)
        db.flush()
        for rule in seed.rules:
            db.add(FieldRule(template_id=template.id, field_name=rule.field_name,
                             pattern=rule.pattern, validator=rule.validator,
                             error_msg=rule.error_msg, priority=rule.priority))
        db.commit()
        template_id = template.id

    template_registry.registry.invalidate()
    with session_factory() as db:
        resolved = rule_source.resolve_rules(db, "vat_digital")

    assert resolved.source == "db"
    assert resolved.template_id == template_id
    assert {rule.name for rule in resolved.rules} == set(seed.field_specs_json)
```

- [ ] **Step 2: 确认失败**：`../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_rule_source.py`

- [ ] **Step 3: 实现**

```python
"""抽取/校验规则的唯一来源（S3-6）：DB active 模板优先，库缺该票种才回退 YAML。

为什么单独一个模块：流水线（抽取）与整单确认（必填集/校验）必须用**同一套**规则；
两份实现迟早漂移，而"确认用的必填集和识别用的规则不一样"是最难查的一类不一致。
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.ocr.extractor import FieldRule, load_field_rules, rules_from_specs
from app.services import template_registry

__all__ = ["ResolvedRules", "resolve_rules"]


@dataclass(frozen=True)
class ResolvedRules:
    rules: list[FieldRule]
    source: str                  # "db" | "yaml"
    template_id: int | None


def resolve_rules(db: Session, ticket_type: str, *, registry=None) -> ResolvedRules:
    reg = registry or template_registry.registry
    snapshot = reg.active_templates(db).get(ticket_type)
    if snapshot is not None and snapshot.field_specs_json:
        return ResolvedRules(
            rules=rules_from_specs(snapshot.field_specs_json),
            source="db",
            template_id=snapshot.id,
        )
    return ResolvedRules(rules=load_field_rules(), source="yaml", template_id=None)
```

`ocr_pipeline.py`：删除私有 `_resolve_rules` 的实现，改为：

```python
from app.services.rule_source import resolve_rules

def _resolve_rules(db, deps, ticket_type):
    resolved = resolve_rules(db, ticket_type, registry=deps.registry)
    return resolved.rules, resolved.source, resolved.template_id
```

（保留 `_resolve_rules` 这个薄封装，`run_pipeline` 调用点与 `metrics` 字段不动；`rules_source=="yaml"` 的
warning 逻辑照旧。）

- [ ] **Step 4: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_rule_source.py
../.venv/Scripts/python.exe -m pytest -q tests/test_ocr_pipeline.py
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_ocr_pipeline.py
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/rule_source.py backend/app/services/ocr_pipeline.py backend/tests/test_rule_source.py
git commit -m "refactor(s3-6): 规则来源共享化（流水线与确认同源）"
```

---

### Task 2: `confirm_invoice` + `POST /confirm`

**Files:**
- Modify: `backend/app/services/invoice_service.py`、`backend/app/schemas/invoice.py`、`backend/app/api/v1/invoices.py`
- Modify: `docs/API.md` §2（confirm 实现标记/备注）
- Test: `backend/tests/test_confirm.py`（新）

**Interfaces:**
- `collect_confirm_errors(rows: dict[str, InvoiceField], rules: list[FieldRule], ticket_type: str) -> list[dict]`
- `confirm_invoice(db, user, invoice_id, *, version: int | None = None) -> InvoiceOut`

- [ ] **Step 1: 写失败测试**

```python
"""S3-6 整单确认：闸门（必填/冲突/低置信/校验）、权限、幂等、版本冲突、并发 409。"""
from __future__ import annotations

import secrets
from collections.abc import Iterator
from decimal import Decimal

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text as sa_text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.core.security import TokenType, encode_token
from app.main import create_app
from app.models.invoice_fields import InvoiceField
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.users import User
from app.services import invoice_service
from tests.db_helpers import alembic_config, temporary_database

BASE = "/api/v1/invoices"
pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def confirm_database() -> Iterator[URL]:
    with temporary_database("study_ocr_confirm_") as url:
        command.upgrade(alembic_config(url), "head")
        yield url


@pytest.fixture(scope="module")
def engine(confirm_database: URL):
    eng = create_engine(confirm_database.render_as_string(hide_password=False), poolclass=NullPool)
    try:
        yield eng
    finally:
        eng.dispose()


@pytest.fixture(scope="module")
def session_factory(engine):
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture()
def clean_db(engine):
    with engine.begin() as conn:
        conn.execute(sa_text("TRUNCATE users RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture()
def actors(session_factory, clean_db) -> dict[str, User]:
    with session_factory() as db:
        users = {
            "alice": User(username="alice", real_name="A", role="submitter", status="active"),
            "bob": User(username="bob", real_name="B", role="submitter", status="active"),
            "rita": User(username="rita", real_name="R", role="reviewer", status="active"),
            "ada": User(username="ada", real_name="Admin", role="admin", status="active"),
        }
        db.add_all(list(users.values()))
        db.commit()
        return users


@pytest.fixture()
def client(session_factory, clean_db) -> Iterator[TestClient]:
    app = create_app()

    def _override_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def _auth(user: User) -> dict[str, str]:
    token, _ = encode_token(user_id=user.id, role=user.role,
                            token_type=TokenType.ACCESS, ttl_seconds=60)
    return {"Authorization": f"Bearer {token}"}


def _make_invoice(session_factory, uploader_id: int, *, invoice_type: str = "unknown") -> int:
    with session_factory() as db:
        file = InvoiceFile(uploader_id=uploader_id, filename="a.jpg", mime="image/jpeg",
                           size=1024, sha256=secrets.token_hex(32),
                           storage_key=f"invoices/{secrets.token_hex(8)}.bin", page_count=1)
        db.add(file)
        db.flush()
        invoice = invoice_service.create_draft(
            db, uploader_id=uploader_id, file_id=file.id, invoice_type=invoice_type
        )
        db.commit()
        return invoice.id


def _seed_fields(session_factory, invoice_id: int, fields: dict[str, str], *,
                 need_review: bool = False, confirmed: bool = False,
                 conflict: bool = False) -> None:
    with session_factory() as db:
        for name, value in fields.items():
            db.add(InvoiceField(invoice_id=invoice_id, field_name=name,
                                field_value=value, normalized_value=value,
                                raw_text=value, source="ocr", confidence=Decimal("0.990"),
                                need_review=need_review, confirmed=confirmed,
                                conflict=conflict, conflict_value="999.99" if conflict else None))
        db.commit()


def _complete_fields() -> dict[str, str]:
    return {
        "invoice_no": "12345678",
        "invoice_date": "2026-03-01",
        "amount": "100.00",
        "tax_amount": "13.00",
        "total_amount": "113.00",
    }


def _confirm(client, invoice_id, user, **body):
    return client.post(f"{BASE}/{invoice_id}/confirm", json=body, headers=_auth(user))


def test_confirm_happy_path(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _seed_fields(session_factory, invoice_id, _complete_fields())

    response = _confirm(client, invoice_id, actors["alice"], version=0)

    assert response.status_code == 200
    data = response.json()["data"]
    assert data["status"] == "confirmed" and data["extract_status"] == "confirmed"
    assert data["version"] == 1
    with session_factory() as db:
        assert db.get(Invoice, invoice_id).status == "confirmed"


def test_confirm_is_idempotent(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _seed_fields(session_factory, invoice_id, _complete_fields())
    assert _confirm(client, invoice_id, actors["alice"]).status_code == 200
    second = _confirm(client, invoice_id, actors["alice"])
    assert second.status_code == 200
    assert second.json()["data"]["version"] == 1      # 不再 +1


def test_confirm_missing_required_lists_field(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    fields = _complete_fields()
    fields.pop("invoice_no")
    _seed_fields(session_factory, invoice_id, fields)

    response = _confirm(client, invoice_id, actors["alice"])

    assert response.status_code == 422
    payload = response.json()
    assert payload["code"] == 42201
    assert any(item["field_name"] == "invoice_no" for item in payload["data"]["fields"])


def test_confirm_unresolved_conflict_gate(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _seed_fields(session_factory, invoice_id, _complete_fields(), conflict=True)

    response = _confirm(client, invoice_id, actors["alice"])

    assert response.status_code == 422
    assert any(item["field_name"] == "total_amount" for item in response.json()["data"]["fields"])


def test_confirm_low_confidence_must_be_confirmed_first(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _seed_fields(session_factory, invoice_id, _complete_fields())
    # 只让一个**非金额**字段处于待确认（金额字段提交人不能改，避免权限干扰本用例）
    _seed_fields(session_factory, invoice_id, {"buyer_name": "武汉某某有限公司"}, need_review=True)

    blocked = _confirm(client, invoice_id, actors["alice"])
    assert blocked.status_code == 422
    assert any(item["error"].startswith("低置信") for item in blocked.json()["data"]["fields"])

    patched = client.patch(
        f"{BASE}/{invoice_id}/fields",
        json={"version": 0, "fields": [{"field_name": "buyer_name",
                                        "value": "武汉某某有限公司", "confirm": True}]},
        headers=_auth(actors["alice"]),
    )
    assert patched.status_code == 200, patched.text

    assert _confirm(client, invoice_id, actors["alice"]).status_code == 200


def test_confirm_validator_failure(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    fields = _complete_fields()
    fields["total_amount"] = "999.00"                 # 不等于 100 + 13
    _seed_fields(session_factory, invoice_id, fields)

    response = _confirm(client, invoice_id, actors["alice"])

    assert response.status_code == 422
    assert any(item["field_name"] == "total_amount" for item in response.json()["data"]["fields"])


def test_confirm_permissions(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _seed_fields(session_factory, invoice_id, _complete_fields())

    assert _confirm(client, invoice_id, actors["bob"]).status_code == 404
    assert _confirm(client, invoice_id, actors["rita"]).status_code == 403
    assert _confirm(client, invoice_id, actors["ada"]).status_code == 404


def test_confirm_rejects_non_draft_and_stale_version(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _seed_fields(session_factory, invoice_id, _complete_fields())
    with session_factory() as db:
        db.execute(sa_text("update invoices set status='void' where id=:id"), {"id": invoice_id})
        db.commit()
    assert _confirm(client, invoice_id, actors["alice"]).status_code == 409

    invoice_id2 = _make_invoice(session_factory, actors["alice"].id)
    _seed_fields(session_factory, invoice_id2, _complete_fields())
    assert _confirm(client, invoice_id2, actors["alice"], version=99).status_code == 409


def test_concurrent_patch_same_version_one_conflicts(client, session_factory, actors) -> None:
    """任务卡验收：两个窗口同时改同一字段，第二次必须 409。"""
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    _seed_fields(session_factory, invoice_id, _complete_fields())
    body = {"version": 0, "fields": [{"field_name": "buyer_name", "value": "甲", "confirm": True}]}

    first = client.patch(f"{BASE}/{invoice_id}/fields", json=body, headers=_auth(actors["alice"]))
    second = client.patch(f"{BASE}/{invoice_id}/fields", json=body, headers=_auth(actors["alice"]))

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json()["code"] == 40900
```

- [ ] **Step 2: 确认失败**：`../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_confirm.py`

- [ ] **Step 3: 实现（service）**

`invoice_service.py` 顶部补：

```python
from app.ocr.validators import run_validators
from app.services.rule_source import resolve_rules
```

`invoice_service.py` 追加：

```python
def collect_confirm_errors(
    rows: dict[str, InvoiceField], rules: list, ticket_type: str
) -> list[dict]:
    """整单确认闸门（纯函数）：必填 → 冲突 → 低置信未确认 → 校验器，同字段只留第一条。"""
    errors: dict[str, str] = {}

    def add(name: str, message: str) -> None:
        errors.setdefault(name, message)

    for rule in rules:
        if not rule.required or not rule.applies_to(ticket_type):
            continue
        row = rows.get(rule.name)
        if row is None or not (row.normalized_value or row.field_value):
            add(rule.name, "必填字段缺失")

    for name in sorted(rows):
        row = rows[name]
        if row.conflict:
            add(name, "存在未决冲突，请先选择候选")
        elif row.need_review and not row.confirmed:
            add(name, "低置信字段尚未人工确认")

    values = {
        name: (row.normalized_value or row.field_value)
        for name, row in rows.items()
    }
    for issue in run_validators(values, ticket_type).errors:
        add(issue.field, issue.message)

    return [{"field_name": name, "error": message} for name, message in errors.items()]


def confirm_invoice(
    db: Session, user: User, invoice_id: int, *, version: int | None = None
) -> InvoiceOut:
    invoice = get_visible_or_404(db, Invoice, invoice_id, user)
    if invoice.uploader_id != user.id:
        raise RoleForbiddenError("只有票据本人可以确认整单")
    file = db.get(InvoiceFile, invoice.file_id)
    if file is None:
        raise NotFoundError("票据文件不存在")

    if (
        invoice.status == InvoiceStatus.CONFIRMED.value
        and invoice.extract_status == ExtractStatus.CONFIRMED.value
    ):
        return _invoice_out(invoice, file)          # 幂等：已确认直接返回当前状态

    if invoice.status != InvoiceStatus.DRAFT.value:
        raise ConflictError(f"当前状态（{invoice.status}）不允许确认")
    if version is not None and version != invoice.version:
        raise ConflictError(f"版本冲突：当前 version={invoice.version}")

    rows = {
        row.field_name: row
        for row in db.scalars(
            select(InvoiceField).where(InvoiceField.invoice_id == invoice.id)
        ).all()
    }
    resolved = resolve_rules(db, invoice.invoice_type)
    errors = collect_confirm_errors(rows, resolved.rules, invoice.invoice_type)
    if errors:
        raise UnresolvedConflictError("整单校验未通过", detail={"fields": errors})

    invoice.status = InvoiceStatus.CONFIRMED.value
    invoice.extract_status = ExtractStatus.CONFIRMED.value
    invoice.version += 1
    db.commit()
    db.refresh(invoice)
    logger.info(
        "整单确认",
        extra={"event": "invoice_confirmed", "invoice_id": invoice.id, "user_id": user.id},
    )
    return _invoice_out(invoice, file)
```

- [ ] **Step 4: 实现（schema + route）**

`schemas/invoice.py`：

```python
class ConfirmRequest(BaseModel):
    version: int | None = Field(default=None, ge=0)
```

`api/v1/invoices.py`：

```python
@router.post(
    "/{invoice_id}/confirm",
    response_model=Envelope[InvoiceOut],
    summary="整单确认（服务端重跑校验；失败 422 逐字段）",
)
def confirm_invoice(
    invoice_id: int,
    payload: ConfirmRequest | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    return ok(invoice_service.confirm_invoice(
        db, user, invoice_id, version=payload.version if payload else None
    ))
```

（允许空 body；FastAPI 对 `ConfirmRequest | None = None` 会把缺省 body 当 None。）

`docs/API.md` §2 confirm：实现改 `S3-6 ✅`，备注补：
"闸门顺序 必填→冲突→低置信未确认→validators；`data.fields` 同字段只留第一条；
审计行待 S4-7；`version` 通过后 +1"。

- [ ] **Step 5: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_confirm.py
../.venv/Scripts/python.exe -m pytest -q tests/test_api_docs.py
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_invoices.py
```

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/invoice_service.py backend/app/schemas/invoice.py \
  backend/app/api/v1/invoices.py docs/API.md backend/tests/test_confirm.py
git commit -m "feat(s3-6): 整单确认 POST /invoices/{id}/confirm（服务端闸门+逐字段 422）"
```

---

### Task 3: 前端"确认整单"

**Files:**
- Modify: `web/src/api/invoices.ts`、`web/src/types/invoice.ts`、`web/src/views/InvoiceDetail.vue`

- [ ] **Step 1: API 与类型**

```ts
// types/invoice.ts
export interface ConfirmError {
  field_name: string
  error: string
}

// api/invoices.ts
export async function confirmInvoice(invoiceId: number, version?: number): Promise<InvoiceOut> {
  const envelope = await post<InvoiceOut>(`/invoices/${invoiceId}/confirm`, { version }, { silent: true })
  return unwrap(envelope)
}
```

- [ ] **Step 2: 详情页**

- `confirmErrors = ref<ConfirmError[]>([])`；
- 头部动作区（`canEdit` 时）加按钮：

```vue
<el-button type="success" @click="confirmAll">确认整单</el-button>
```

- 函数：

```ts
async function confirmAll(): Promise<void> {
  const invoice = detail.value?.invoice
  if (!invoice) return
  try {
    await ElMessageBox.confirm('确认后票据将进入「已确认」，字段修改需重新打开草稿。是否继续？',
      '确认整单', { type: 'warning', confirmButtonText: '确认', cancelButtonText: '取消' })
  } catch {
    return
  }
  confirmErrors.value = []
  try {
    await confirmInvoice(invoice.id, invoice.version)
    ElMessage.success('已确认整单')
    await load()
  } catch (cause) {
    if (cause instanceof ApiError && cause.code === 42201) {
      const payload = cause.payload?.data as { fields?: ConfirmError[] } | null | undefined
      confirmErrors.value = payload?.fields ?? []
      ElMessage.error('整单校验未通过，请按提示处理')
    } else if (cause instanceof ApiError && cause.code === 40900) {
      ElMessage.error('版本冲突：票据已被修改，已为你刷新')
      await load()
    } else {
      ElMessage.error(cause instanceof ApiError ? cause.message : '确认失败，请重试')
    }
  }
}
```

- 字段卡上方（或表格上方）渲染错误列表：

```vue
<el-alert
  v-if="confirmErrors.length"
  type="error"
  :closable="false"
  show-icon
  title="整单校验未通过"
  class="confirm-errors"
>
  <ul class="confirm-error-list">
    <li v-for="item in confirmErrors" :key="item.field_name">
      <strong>{{ fieldLabel(item.field_name) }}</strong>：{{ item.error }}
    </li>
  </ul>
</el-alert>
```

- `load()` 成功时清空 `confirmErrors`；`ElMessageBox` 需从 `element-plus` 导入。
- reviewer 不显示该按钮（`canEdit` 已限定 draft；再加 `store.role === 'submitter'`）。

- [ ] **Step 3: 验证**

```bash
cd /e/BiSheXZY/study-OCR/web && npm run typecheck && npm run build
```

- [ ] **Step 4: 提交**

```bash
git add web/src/api/invoices.ts web/src/types/invoice.ts web/src/views/InvoiceDetail.vue
git commit -m "feat(s3-6): 详情页确认整单与逐字段错误提示"
```

---

### Task 4: 冒烟 + 全量回归 + 文档收口

- [ ] **Step 1: 全量验证**

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m alembic check          # head 0005，本卡无迁移
../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/python.exe -m pytest -m integration -q
cd ../web && npm run typecheck && npm run build
```

- [ ] **Step 2: 真实冒烟**

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe ../scripts/create_user.py --username s36sub --role submitter \
  --real-name S36验收 --password 'S36sub!2026'
# 起 uvicorn + worker + Vite + 无头 Edge
```

1. 再建一个 **reviewer** 账号（金额字段只有 reviewer 能改，submitter 确认整单前可能需要先修金额）：
   `../.venv/Scripts/python.exe ../scripts/create_user.py --username s36rev --role reviewer --real-name S36审核 --password 'S36rev!2026'`
2. 上传一张（vat_general）→ 打开详情 → 点"确认整单"：预期弹 **422 逐字段错误**（缺必填/低置信/校验失败之一）；
3. 用 reviewer 改金额字段（`PATCH /fields` + `confirm_amount=true`），submitter 再用 `PATCH /fields`
   逐字段 `confirm=true` 补齐低置信项；再点"确认整单" → 200，状态变"已确认"；
4. 用 curl 验证并发：同一 `version` 连发两次 PATCH → 第二次 409（测试已覆盖，冒烟可略）；
5. 确认后再次 confirm → 200 且版本不再增加（幂等）。

冒烟后清理票据/对象/临时账号。

- [ ] **Step 3: 文档**

- `PROGRESS`：勾 S3-6 + 表格行（confirm 闸门/并发 409/422 明细、测试计数）；关闭遗留 #29 的"评估"项，
  新增遗留：`confirm_field` 审计行待 S4-7、reviewer 复核确认待 S5；
- `DECISIONS`：**D-065**（confirm 闸门四类与顺序、本人权限、幂等与 version+1、审计行留 S4-7、不重写 PATCH）；
- `AGENTS` §10.8 锚点："整单确认 = `invoice_service.collect_confirm_errors/confirm_invoice` + `rule_source.resolve_rules` + `POST /confirm`"；
- `HANDOFF`：现状改"S3-6 已通，下一步 S3-7"，§7 换 S3-7 入口（重复报销检测：文件 sha256 已唯一、
  语义重复用 max(conflict_value) 等，需定口径）；`API.md` confirm 已实现；
- spec/plan 状态改"已实现"。

- [ ] **Step 4: 提交**

```bash
git add docs AGENTS.md
git commit -m "docs(s3-6): 整单确认口径与实测收口"
```

---

## 自检（对照 spec）

| spec 条目 | 覆盖 Task |
|---|---|
| §1 产出 1–7 | Task 1–4 |
| §2 Q1/Q2/Q3 | Task 2（本人 403/低置信闸门）、Task 2（不写审计） |
| §3.1 rule_source | Task 1 |
| §3.2 confirm_invoice 十步 | Task 2 |
| §3.3 接口/422 明细 | Task 2 |
| §3.4 前端 | Task 3 |
| §4 测试（含并发 409） | Task 2 |
| §5 验收命令 | Task 4 |
| §6 收口 | Task 4 |

## 回滚与降级

| 情况 | 处理 |
|---|---|
| confirm 误拦（合法票据确认不了） | 看 `data.fields` 的 error 文案逐条处理；若规则确实过严，先改 `field_rules.yaml`/DB 模板（`required`/regex），不改闸门代码 |
| 确认后想改 | 当前状态机 `confirmed` 无回退路径（S5 才允许 `void`）；S3-6 不提供"反确认"——这是刻意的审计约束，论文写明 |
| 并发冲突频繁 | 前端确认前先 `GET` 拿最新 `version`；后端已用 `version` 兜底 |
| 规则来源 DB/YAML 不一致 | `rule_source` 单测 + 流水线既有 `rules_source` 指标可见 |