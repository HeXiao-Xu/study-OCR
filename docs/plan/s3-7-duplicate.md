# S3-7 重复报销检测实现计划（plan）

> 对应 spec：[`docs/spec/s3-7-duplicate.md`](../spec/s3-7-duplicate.md)（已确认）｜
> 契约：`API.md` §2 新增 `GET /invoices/{id}/duplicate-check`（覆盖 D-013）｜ 状态：**已实现（2026-11-20）**，Task 1–4 全部执行并验证
> 执行方式：Task 1→4 顺序；每个 Task 跑完验证命令再继续。

**目标**：强重复（`duplicate_of`/同票号/同影像）在 `confirm` 阻断 409 + 已有 `invoice_id`；
弱重复（同金额+同日期+同销方税号、票号不同）只提示；检测集中 `duplicate.py`，详情页横幅。
**无迁移**（唯一索引、`duplicate_of` 列均已存在）。

## Global Constraints

- 命令用 `.venv/Scripts/python.exe`；默认后端目录 `E:\BiSheXZY\study-OCR\backend`。
- **本卡不改表**（head 保持 `0005`）；不改已提交迁移。
- 检测只读、不加锁；弱重复**绝不阻断**；`matches` 只给最小字段（不含姓名/税号明文）。
- 强重复的 409 在 `confirm` 返回（上传/流水线只标记，作业不失败）。
- 提交 Conventional Commits（`feat(s3-7): ...`）；**push 由用户执行**。

## 文件结构

| 文件 | 职责 |
|---|---|
| `backend/app/services/duplicate.py`（新） | `DuplicateMatch/DuplicateReport` + `check_duplicates` + `get_duplicate_report` |
| `backend/app/schemas/invoice.py` | `DuplicateMatchOut` / `DuplicateCheckData` |
| `backend/app/api/v1/invoices.py` | `GET /{id}/duplicate-check` |
| `backend/app/services/ocr_pipeline.py` | 唯一冲突回退时写 `duplicate_of` + `metrics.duplicate_of` |
| `backend/app/services/invoice_service.py` | `confirm_invoice` 强重复闸门 → 409 |
| `backend/tests/test_duplicate.py`（新） | 三档/权限/confirm 409/并发/影像映射 |
| `web/src/{api/invoices.ts,types/invoice.ts,views/InvoiceDetail.vue}` | 重复横幅 |
| `docs/API.md`、`PROGRESS`、`DECISIONS`、`HANDOFF`、`AGENTS` | 契约与收口 |

---

### Task 1: `duplicate.py` + `GET /duplicate-check`

**Files:**
- Create: `backend/app/services/duplicate.py`
- Modify: `backend/app/schemas/invoice.py`、`backend/app/api/v1/invoices.py`、`docs/API.md` §2/§13
- Test: `backend/tests/test_duplicate.py`（新）

**Interfaces:**
- `DuplicateMatch{invoice_id, kind, invoice_code, invoice_no, invoice_date, total_amount, status, created_at}`
- `DuplicateReport{invoice_id, level, matches}`
- `check_duplicates(db, invoice, *, weak_limit=20) -> DuplicateReport`
- `get_duplicate_report(db, user, invoice_id) -> DuplicateReport`（含可见性）

- [ ] **Step 1: 写失败测试**

```python
"""S3-7 重复检测：强（duplicate_of/同票号）、弱（同金额+日期+销方）、权限、confirm 阻断、并发索引。"""
from __future__ import annotations

import secrets
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from alembic import command
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text as sa_text, update
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.core.database import get_db
from app.core.security import TokenType, encode_token
from app.main import create_app
from app.models.invoice_fields import InvoiceField
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.users import User
from app.services import duplicate, invoice_service
from tests.db_helpers import alembic_config, temporary_database

BASE = "/api/v1/invoices"
pytestmark = pytest.mark.integration


# 夹具照 test_confirm.py：dup_database / engine / session_factory / clean_db / actors / client / _auth
# （alice、bob 为 submitter，rita 为 reviewer，ada 为 admin）


def _make_invoice(session_factory, uploader_id: int, *, invoice_no: str | None = None,
                  invoice_code: str | None = None, duplicate_of: int | None = None,
                  sha256: str | None = None) -> int:
    with session_factory() as db:
        file = InvoiceFile(uploader_id=uploader_id, filename="a.jpg", mime="image/jpeg",
                           size=1024, sha256=sha256 or secrets.token_hex(32),
                           storage_key=f"invoices/{secrets.token_hex(8)}.bin", page_count=1)
        db.add(file)
        db.flush()
        invoice = invoice_service.create_draft(db, uploader_id=uploader_id, file_id=file.id)
        invoice.invoice_no = invoice_no
        invoice.invoice_code = invoice_code
        invoice.duplicate_of = duplicate_of
        db.commit()
        return invoice.id


def _fields(session_factory, invoice_id: int, values: dict[str, str]) -> None:
    with session_factory() as db:
        for name, value in values.items():
            db.add(InvoiceField(invoice_id=invoice_id, field_name=name, field_value=value,
                                normalized_value=value, raw_text=value, source="ocr",
                                confidence=Decimal("0.990")))
        db.commit()


def test_duplicate_of_is_strong(session_factory, actors) -> None:
    first = _make_invoice(session_factory, actors["alice"].id, invoice_no="11111111")
    second = _make_invoice(session_factory, actors["alice"].id, duplicate_of=first)
    with session_factory() as db:
        report = duplicate.check_duplicates(db, db.get(Invoice, second))

    assert report.level == "strong"
    assert report.matches[0].kind == "invoice_no"
    assert report.matches[0].invoice_id == first


def test_code_no_branch_mapping_is_strong(monkeypatch, session_factory, actors) -> None:
    """同票号查询是纵深防御：唯一索引让正常路径不可能存在两条同号非 void 票，单测映射逻辑。"""
    first = _make_invoice(session_factory, actors["alice"].id, invoice_no="11111111")
    second = _make_invoice(session_factory, actors["alice"].id)
    with session_factory() as db:
        other = db.get(Invoice, first)
        monkeypatch.setattr(duplicate, "_code_no_matches",
                            lambda db_, invoice_: [other])
        report = duplicate.check_duplicates(db, db.get(Invoice, second))

    assert report.level == "strong"
    assert report.matches[0].kind == "invoice_no"
    assert {item.invoice_id for item in report.matches} == {first}


def test_weak_duplicate_three_dimensions(session_factory, actors) -> None:
    first = _make_invoice(session_factory, actors["alice"].id, invoice_no="11111111")
    _fields(session_factory, first, {"total_amount": "100.00",
                                     "invoice_date": "2026-03-01",
                                     "seller_tax_no": "91420100MA4K000001"})
    second = _make_invoice(session_factory, actors["alice"].id, invoice_no="22222222")
    _fields(session_factory, second, {"total_amount": "100.00",
                                      "invoice_date": "2026-03-01",
                                      "seller_tax_no": "91420100MA4K000001"})

    with session_factory() as db:
        report = duplicate.check_duplicates(db, db.get(Invoice, second))

    assert report.level == "weak"
    assert report.matches[0].kind == "weak"
    assert report.matches[0].invoice_no == "11111111"


def test_weak_requires_all_three_and_excludes_void(session_factory, actors) -> None:
    first = _make_invoice(session_factory, actors["alice"].id, invoice_no="11111111")
    _fields(session_factory, first, {"total_amount": "100.00",
                                     "invoice_date": "2026-03-01",
                                     "seller_tax_no": "91420100MA4K000001"})
    second = _make_invoice(session_factory, actors["alice"].id, invoice_no="22222222")
    _fields(session_factory, second, {"total_amount": "100.00",
                                      "invoice_date": "2026-03-01"})   # 缺销方税号
    with session_factory() as db:
        assert duplicate.check_duplicates(db, db.get(Invoice, second)).level == "none"

    with session_factory() as db:
        db.execute(sa_text("update invoices set status='void' where id=:id"), {"id": first})
        db.commit()
        report = duplicate.check_duplicates(db, db.get(Invoice, second))
    assert report.level == "none"


def test_duplicate_check_endpoint_and_permissions(session_factory, actors) -> None:
    first = _make_invoice(session_factory, actors["alice"].id, invoice_no="11111111")
    second = _make_invoice(session_factory, actors["alice"].id, duplicate_of=first)
    with _client(session_factory) as client:
        owner = client.get(f"{BASE}/{second}/duplicate-check", headers=_auth(actors["alice"]))
        reviewer = client.get(f"{BASE}/{second}/duplicate-check", headers=_auth(actors["rita"]))
        other = client.get(f"{BASE}/{second}/duplicate-check", headers=_auth(actors["bob"]))
        admin = client.get(f"{BASE}/{second}/duplicate-check", headers=_auth(actors["ada"]))

    assert owner.status_code == 200
    data = owner.json()["data"]
    assert data["level"] == "strong"
    assert data["matches"][0]["invoice_id"] == first
    assert "buyer_name" not in data["matches"][0]
    assert reviewer.status_code == 200
    assert other.status_code == 404 and admin.status_code == 404


def test_confirm_blocks_strong_duplicate(session_factory, actors) -> None:
    first = _make_invoice(session_factory, actors["alice"].id, invoice_no="11111111")
    second = _make_invoice(session_factory, actors["alice"].id, duplicate_of=first)
    _fields(session_factory, second, {"invoice_no": "22222222",
                                      "invoice_date": "2026-03-01",
                                      "amount": "100.00", "tax_amount": "13.00",
                                      "total_amount": "113.00"})
    with _client(session_factory) as client:
        blocked = client.post(f"{BASE}/{second}/confirm", json={},
                              headers=_auth(actors["alice"]))
        plain = client.post(f"{BASE}/{first}/confirm", json={},
                            headers=_auth(actors["alice"]))

    assert blocked.status_code == 409
    assert blocked.json()["code"] == 40900
    assert blocked.json()["data"]["invoice_id"] == first
    assert plain.status_code == 200          # 无重复的票照常确认（回归）


def test_unique_index_blocks_concurrent_same_number(session_factory, actors) -> None:
    """并发最后一道闸：两个 session 同时写同 (code,no)，第二个必须 IntegrityError。"""
    first = _make_invoice(session_factory, actors["alice"].id)
    second = _make_invoice(session_factory, actors["alice"].id)

    with session_factory() as db1, session_factory() as db2:
        db1.execute(update(Invoice).where(Invoice.id == first)
                    .values(invoice_code="1234567890", invoice_no="11111111"))
        db1.commit()
        with pytest.raises(IntegrityError):
            db2.execute(update(Invoice).where(Invoice.id == second)
                        .values(invoice_code="1234567890", invoice_no="11111111"))
            db2.commit()


def test_image_match_mapping_is_strong(monkeypatch, session_factory, actors) -> None:
    """影像重复受 uq_invoice_files_sha256 限制造不出两条同 sha，单测映射逻辑。"""
    first = _make_invoice(session_factory, actors["alice"].id)
    second = _make_invoice(session_factory, actors["alice"].id)
    with session_factory() as db:
        other = db.get(Invoice, first)
        monkeypatch.setattr(duplicate, "_image_matches", lambda db_, invoice_: [other])
        report = duplicate.check_duplicates(db, db.get(Invoice, second))

    assert report.level == "strong"
    assert report.matches[0].kind == "image"
```

（`_client` 与 `_auth` 照 `tests/test_confirm.py` 复制。）

- [ ] **Step 2: 确认失败**

- [ ] **Step 3: 实现 `duplicate.py`**

```python
"""重复报销检测（S3-7）：强重复（duplicate_of/同票号/同影像）+ 弱重复（同金额/日期/销方）。

强重复**在确认时阻断**（`confirm` → 409）；弱重复只提示不阻断——硬阻断会误伤连号票。
影像维度是纵深防御：`uq_invoice_files_sha256` + 上传去重已让正常路径不可能重复。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.models.enums import InvoiceStatus
from app.models.invoice_fields import InvoiceField
from app.models.invoice_files import InvoiceFile
from app.models.invoices import Invoice
from app.models.users import User
from app.services.repository import get_visible_or_404

__all__ = ["DuplicateMatch", "DuplicateReport", "check_duplicates", "get_duplicate_report"]

_VOID = InvoiceStatus.VOID.value
_WEAK_FIELDS = ("total_amount", "invoice_date", "seller_tax_no")


@dataclass(frozen=True)
class DuplicateMatch:
    invoice_id: int
    kind: str
    invoice_code: str | None
    invoice_no: str | None
    invoice_date: date | None
    total_amount: Decimal | None
    status: str
    created_at: datetime


@dataclass(frozen=True)
class DuplicateReport:
    invoice_id: int
    level: str
    matches: list[DuplicateMatch]


def _to_match(row: Invoice, kind: str) -> DuplicateMatch:
    return DuplicateMatch(
        invoice_id=row.id, kind=kind, invoice_code=row.invoice_code,
        invoice_no=row.invoice_no, invoice_date=row.invoice_date,
        total_amount=row.total_amount, status=row.status, created_at=row.created_at,
    )


def _image_matches(db: Session, invoice: Invoice) -> list[Invoice]:
    file_row = db.get(InvoiceFile, invoice.file_id)
    if file_row is None or not file_row.sha256:
        return []
    return list(db.scalars(
        select(Invoice)
        .join(InvoiceFile, InvoiceFile.id == Invoice.file_id)
        .where(InvoiceFile.sha256 == file_row.sha256,
               Invoice.id != invoice.id, Invoice.status != _VOID)
    ))


def _code_no_matches(db: Session, invoice: Invoice) -> list[Invoice]:
    if not (invoice.invoice_code and invoice.invoice_no):
        return []
    return list(db.scalars(select(Invoice).where(
        Invoice.invoice_code == invoice.invoice_code,
        Invoice.invoice_no == invoice.invoice_no,
        Invoice.id != invoice.id,
        Invoice.status != _VOID,
    )))


def _weak_values(db: Session, invoice: Invoice) -> dict[str, str | None]:
    rows = db.scalars(
        select(InvoiceField).where(InvoiceField.invoice_id == invoice.id,
                                   InvoiceField.field_name.in_(_WEAK_FIELDS))
    ).all()
    return {row.field_name: (row.normalized_value or row.field_value) for row in rows}


def _weak_matches(db: Session, invoice: Invoice, *, limit: int,
                  exclude: set[int]) -> list[Invoice]:
    values = _weak_values(db, invoice)
    total, day, tax = (values.get(name) for name in _WEAK_FIELDS)
    if not (total and day and tax):
        return []                       # 缺任一维不判弱重复：宁可漏报，不误伤
    stmt = select(Invoice).where(
        Invoice.id.in_(select(InvoiceField.invoice_id).where(
            InvoiceField.field_name == "total_amount",
            InvoiceField.normalized_value == total)),
        Invoice.id.in_(select(InvoiceField.invoice_id).where(
            InvoiceField.field_name == "invoice_date",
            InvoiceField.normalized_value == day)),
        Invoice.id.in_(select(InvoiceField.invoice_id).where(
            InvoiceField.field_name == "seller_tax_no",
            InvoiceField.normalized_value == tax)),
        Invoice.id != invoice.id,
        Invoice.status != _VOID,
        Invoice.id.not_in(exclude),
    )
    if invoice.invoice_no:
        stmt = stmt.where(or_(Invoice.invoice_no.is_(None), Invoice.invoice_no != invoice.invoice_no))
    return list(db.scalars(stmt.order_by(Invoice.created_at.desc(), Invoice.id.desc()).limit(limit)))


def check_duplicates(db: Session, invoice: Invoice, *, weak_limit: int = 20) -> DuplicateReport:
    strong: list[DuplicateMatch] = []
    seen = {invoice.id}

    if invoice.duplicate_of:
        row = db.get(Invoice, invoice.duplicate_of)
        if row is not None and row.status != _VOID:
            strong.append(_to_match(row, "invoice_no"))
            seen.add(row.id)

    for row in _code_no_matches(db, invoice):
        if row.id not in seen:
            strong.append(_to_match(row, "invoice_no"))
            seen.add(row.id)

    for row in _image_matches(db, invoice):
        if row.id not in seen:
            strong.append(_to_match(row, "image"))
            seen.add(row.id)

    weak = [_to_match(row, "weak")
            for row in _weak_matches(db, invoice, limit=weak_limit, exclude=seen)]
    level = "strong" if strong else ("weak" if weak else "none")
    return DuplicateReport(invoice_id=invoice.id, level=level, matches=strong + weak)


def get_duplicate_report(db: Session, user: User, invoice_id: int) -> DuplicateReport:
    invoice = get_visible_or_404(db, Invoice, invoice_id, user)
    return check_duplicates(db, invoice)
```

- [ ] **Step 4: schema + route**

```python
class DuplicateMatchOut(BaseModel):
    invoice_id: int
    kind: str
    invoice_code: str | None = None
    invoice_no: str | None = None
    invoice_date: date | None = None
    total_amount: Decimal | None = None
    status: str
    created_at: datetime


class DuplicateCheckData(BaseModel):
    invoice_id: int
    level: str
    matches: list[DuplicateMatchOut] = Field(default_factory=list)
```

```python
@router.get(
    "/{invoice_id}/duplicate-check",
    response_model=Envelope[DuplicateCheckData],
    summary="重复检测（强：同票号/同影像；弱：同金额+日期+销方）",
)
def duplicate_check(
    invoice_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:
    report = duplicate.get_duplicate_report(db, user, invoice_id)
    return ok({
        "invoice_id": report.invoice_id,
        "level": report.level,
        "matches": [asdict(match) for match in report.matches],
    })
```

（`asdict` 从 `dataclasses` 导入；也可在 schema 上加 `from_report` 工厂，二选一，保持路由 ≤30 行。）

`API.md` §2 在 `GET /lines` 之后加该接口（spec §3.4 原文）；§13 决策行改为
"**S3-7 已覆盖**：新增只读 `duplicate-check`，上传 `dedup` 与 `duplicate_of` 保留"。

- [ ] **Step 5: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_duplicate.py
../.venv/Scripts/python.exe -m pytest -q tests/test_api_docs.py
```

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/duplicate.py backend/app/schemas/invoice.py \
  backend/app/api/v1/invoices.py docs/API.md backend/tests/test_duplicate.py
git commit -m "feat(s3-7): 重复检测服务与 GET /invoices/{id}/duplicate-check"
```

---

### Task 2: 流水线写 `duplicate_of` + confirm 强重复闸门

**Files:**
- Modify: `backend/app/services/ocr_pipeline.py`、`backend/app/services/invoice_service.py`
- Test: `backend/tests/test_ocr_pipeline.py`（补断言）、`backend/tests/test_duplicate.py`（Task 1 已含）

- [ ] **Step 1: 流水线（唯一冲突回退时写 duplicate_of）**

```python
    except IntegrityError:
        metrics["duplicate_invoice_no"] = True
        metrics["warnings"].append("duplicate_invoice_no")
        duplicate_of = None
        code, no = values.get("invoice_code"), values.get("invoice_no")
        if code and no:
            duplicate_of = db.scalar(select(Invoice.id).where(
                Invoice.invoice_code == code, Invoice.invoice_no == no,
                Invoice.id != invoice.id, Invoice.status != InvoiceStatus.VOID.value))
        metrics["duplicate_of"] = duplicate_of
        db.execute(
            update(Invoice)
            .where(Invoice.id == invoice.id, Invoice.ocr_status.in_(_STATUS_OPEN))
            .values(invoice_type=classification.ticket_type, template_id=template_id,
                    duplicate_of=duplicate_of, **final)
        )
```

- [ ] **Step 2: confirm 闸门**

```python
from app.services.duplicate import check_duplicates

    ...
    if version is not None and version != invoice.version:
        raise ConflictError(f"版本冲突：当前 version={invoice.version}")

    report = check_duplicates(db, invoice)
    strong = [match for match in report.matches if match.kind in ("invoice_no", "image")]
    if strong:
        raise ConflictError("该票据与已有票据重复", detail={
            "invoice_id": strong[0].invoice_id, "kind": strong[0].kind})
```

（放在字段闸门之前：重复是硬阻断，先返回 409，不必先列 422。）

- [ ] **Step 3: 测试**

`tests/test_ocr_pipeline.py::test_run_pipeline_duplicate_invoice_no_is_warning` 补：

```python
        assert invoice.duplicate_of == existing_id     # S3-7：冲突时记录已有票
        assert job.metrics["duplicate_of"] == existing_id
```

Task 1 的 `test_confirm_blocks_strong_duplicate` 已覆盖 409。

- [ ] **Step 4: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_ocr_pipeline.py
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_duplicate.py
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_confirm.py
```

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/ocr_pipeline.py backend/app/services/invoice_service.py \
  backend/tests/test_ocr_pipeline.py
git commit -m "feat(s3-7): 流水线记录 duplicate_of，整单确认阻断强重复 409"
```

---

### Task 3: 前端重复横幅

**Files:**
- Modify: `web/src/api/invoices.ts`、`web/src/types/invoice.ts`、`web/src/views/InvoiceDetail.vue`

- [ ] **Step 1: API 与类型**

```ts
// types/invoice.ts
export interface DuplicateMatch {
  invoice_id: number
  kind: 'invoice_no' | 'image' | 'weak'
  invoice_code: string | null
  invoice_no: string | null
  invoice_date: string | null
  total_amount: string | null
  status: string
  created_at: string
}
export interface DuplicateCheckData {
  invoice_id: number
  level: 'strong' | 'weak' | 'none'
  matches: DuplicateMatch[]
}

// api/invoices.ts
export async function fetchDuplicateCheck(invoiceId: number): Promise<DuplicateCheckData> {
  const envelope = await get<DuplicateCheckData>(`/invoices/${invoiceId}/duplicate-check`, {
    silent: true,
  })
  return unwrap(envelope)
}
```

- [ ] **Step 2: 详情页横幅**

```ts
const duplicates = ref<DuplicateCheckData | null>(null)

// load() 里，字段加载后：
try {
  duplicates.value = await fetchDuplicateCheck(invoiceId.value)
} catch {
  duplicates.value = null      // 检测失败不阻塞核对
}
```

```vue
<el-alert
  v-if="duplicates && duplicates.level !== 'none'"
  :type="duplicates.level === 'strong' ? 'error' : 'warning'"
  :closable="false"
  show-icon
  :title="duplicates.level === 'strong' ? '疑似重复票据（同票号/同影像）' : '弱重复提示（同金额/日期/销方）'"
  class="duplicate-alert"
>
  <ul class="duplicate-list">
    <li v-for="item in duplicates.matches" :key="`${item.kind}-${item.invoice_id}`">
      票据 #{{ item.invoice_id }}（{{ item.status }}）·
      票号 {{ item.invoice_no ?? '—' }} · 日期 {{ item.invoice_date ?? '—' }} ·
      金额 {{ formatMoney(item.total_amount) }}
      <router-link :to="`/invoices/${item.invoice_id}`">查看</router-link>
    </li>
  </ul>
</el-alert>
```

（横幅放在 `.detail-grid` 上方；`strong` 时隐藏"确认整单"按钮：`v-if="canEdit && !editMode &&
store.role === 'submitter' && duplicates?.level !== 'strong'"`——后端仍会 409 兜底。）

- [ ] **Step 3: 验证**：`cd web && npm run typecheck && npm run build`

- [ ] **Step 4: 提交**

```bash
git add web/src/api/invoices.ts web/src/types/invoice.ts web/src/views/InvoiceDetail.vue
git commit -m "feat(s3-7): 详情页重复提示横幅"
```

---

### Task 4: 冒烟 + 全量回归 + 文档收口

- [ ] **Step 1: 全量验证**

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m alembic check
../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/python.exe -m pytest -m integration -q
cd ../web && npm run typecheck && npm run build
```

- [ ] **Step 2: 真实冒烟**

起 uvicorn/worker/Vite；用临时 submitter：
1. 上传票 A → 识别成功（拿到 `invoice_code/invoice_no`）；
2. 上传**同一张图** → 上传 409（已有 `invoice_id`）——影像重复；
3. 上传另一张**同号**票（可用同号合成样本或直接改库模拟：把第二张的 `duplicate_of` 置为 A）→
   `GET /duplicate-check` = strong；`POST /confirm` = **409 + invoice_id**；
4. 弱重复：两张不同号但同金额/日期/销方 → `level=weak`；详情页黄色横幅（截图）。

清理票据/对象/账号。

- [ ] **Step 3: 文档**

- `PROGRESS`：勾 S3-7 + 表格行（三档实测、confirm 409、截图表）；关闭遗留 #47（重复检测）；
  新增遗留：跨用户重复合并、弱重复人工确认（S5）；
- `DECISIONS`：**D-066**（覆盖 D-013；强重复 confirm 409；弱重复只提示；duplicate_of 由流水线写；
  影像重复纵深防御；弱重复三维全等）；
- `AGENTS` §10.8 锚点；`HANDOFF`：下一步 S3-8（批量上传）与 S3-9（若存在）说明；
- `API.md` §13 决策行更新；spec/plan 状态改"已实现"。

- [ ] **Step 4: 提交**

```bash
git add docs AGENTS.md
git commit -m "docs(s3-7): 重复检测口径与实测收口"
```

---

## 自检（对照 spec）

| spec 条目 | 覆盖 Task |
|---|---|
| §1 产出 1–8 | Task 1–4 |
| §2 Q1/Q2/Q3 | Task 1（接口）、Task 2（confirm 409）、Task 3（横幅） |
| §3.1 duplicate.py | Task 1 |
| §3.2 流水线 duplicate_of | Task 2 |
| §3.3 confirm 闸门 | Task 2 |
| §3.4 接口 | Task 1 |
| §3.5 前端 | Task 3 |
| §4 测试（含并发/影像映射） | Task 1/2 |
| §5 验收 | Task 4 |
| §6 收口 | Task 4 |

## 回滚与降级

| 情况 | 处理 |
|---|---|
| 弱重复误报多 | 改 `_weak_matches` 的维度（如加 `uploader_id` 或金额容差），不动接口形状 |
| 强重复 409 挡了合法票 | 核对该票是否真的同号：查 `duplicate_of` 指向的原票；作废原票（void）后重试 |
| `duplicate_of` 指向的票被删 | 检测忽略 void/不存在，回落到同票号查询 |
| 影像重复判断失效（历史数据） | 唯一索引 + 上传去重仍是主闸；`kind=image` 只做兜底提示 |