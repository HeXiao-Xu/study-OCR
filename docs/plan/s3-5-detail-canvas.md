# S3-5 详情页图像与字段并排 + 框选高亮实现计划（plan）

> 对应 spec：[`docs/spec/s3-5-detail-canvas.md`](../spec/s3-5-detail-canvas.md)（已确认）｜
> 契约：`API.md` §2 `GET /image`（已定义）＋ 本卡新增 `GET /lines` ｜ 状态：**已实现（2026-11-20）**，Task 1–6 全部执行并验证
> 执行方式：Task 1→6 顺序；每个 Task 跑完验证命令再继续。

**目标**：详情页左图右表；`<img>` + canvas 叠层画**字段框 + 行框**；hover/点击双向联动；缩放平移后框仍对齐；
低置信黄底、必须人工红底并给候选文案。图片经 `GET /image` 以 Bearer + blob 取，明文仅本地调试。

**架构**：后端 `image_service`（原图/预处理图渲染）与 `line_service`（行框映射原图坐标、**不返回文本**）；
前端坐标换算只在 `utils/bbox.ts`，`ImageCanvas` 负责绘制/交互，`FieldTable` 负责表格与行样式，详情页持有状态。

**技术栈**：PyMuPDF（已有）/ FastAPI / SQLAlchemy / Vue 3 + TS（无新增依赖）。

## Global Constraints

- 命令用 `.venv/Scripts/python.exe`（web 用 `npm`）；默认后端目录 `E:\BiSheXZY\study-OCR\backend`。
- **本卡不加迁移**（head 保持 `0005`）；不改已提交迁移。
- 影像明文只允许 `STORAGE_ALLOW_PLAINTEXT=true` 且 `file.is_cipher=false`；其它情况 `409/40900`，不得回退明文。
- **不返回** `storage_key`/MinIO 路径/预签名 URL；`GET /lines` **不返回行文本**。
- 坐标：`ocr_lines` 四点=校正图；响应与前端一律**原图** `[x1,y1,x2,y2]`；PDF 渲染固定 **250 DPI**。
- 前端所有 HTTP 经 `src/api/`；组件里禁止 `import axios`；canvas 不得用 CSS transform 缩放（会发虚+错位）。
- 提交 Conventional Commits（`feat(s3-5): ...`）；**push 由用户执行**。

## 文件结构

| 文件 | 职责 |
|---|---|
| `backend/app/services/image_service.py`（新） | `RenderedImage` + `get_invoice_image`（photo 原字节 / PDF 250dpi PNG / preprocessed） |
| `backend/app/services/line_service.py`（新） | `get_invoice_lines`：行框映射原图坐标、命中字段名、无文本 |
| `backend/app/schemas/invoice.py` | `OcrLineBoxOut` / `InvoiceLinesData` |
| `backend/app/api/v1/invoices.py` | `GET /{id}/image`、`GET /{id}/lines` |
| `backend/tests/{test_image_service,test_lines_service,test_invoice_media}.py`（新） | 单元 + HTTP 集成 |
| `web/src/api/http.ts` | `getBlob`（responseType=blob，仍走鉴权/401 单飞） |
| `web/src/api/invoices.ts`、`types/invoice.ts` | `fetchInvoiceImage/fetchInvoiceLines` + 类型 |
| `web/src/utils/bbox.ts`（新） | 坐标换算/裁剪/命中（唯一实现） |
| `web/src/components/ImageCanvas.vue`（新） | img+canvas 叠层、缩放/平移、绘制、hover/点击、调试属性 |
| `web/src/components/FieldTable.vue`（新） | 字段表 + 行底色 + 联动 + 冲突文案 + 重识别按钮 |
| `web/src/views/InvoiceDetail.vue` | 左图右表、blob 生命周期、翻页、联动 |
| `web/src/utils/format.ts` | 补 `total_amount_upper` 标签 |

---

### Task 1: `GET /invoices/{id}/image`

**Files:**
- Create: `backend/app/services/image_service.py`
- Modify: `backend/app/api/v1/invoices.py`
- Test: `backend/tests/test_image_service.py`（单元）、`backend/tests/test_invoice_media.py`（HTTP 集成）

**Interfaces:**
- `RenderedImage{content: bytes, media_type: str, etag: str}`
- `get_invoice_image(db, user, invoice_id, *, page: int, variant: str, settings, storage) -> RenderedImage`

- [ ] **Step 1: 写失败测试**

`tests/test_image_service.py`：

```python
"""S3-5 影像渲染：原图直出 / PDF 按 250DPI 渲染 / 预处理变体 / 明文闸门。"""
from __future__ import annotations

import io

import pytest
from PIL import Image

from app.core.config import Settings
from app.core.exceptions import ConflictError, ValidationFailedError
from app.models.invoice_files import InvoiceFile
from app.services import image_service

_BASE = dict(
    database_url="postgresql://u:p@127.0.0.1:5433/db",
    redis_url="redis://:p@127.0.0.1:6380/0",
    minio_endpoint="127.0.0.1:9002", minio_access_key="k", minio_secret_key="s",
    minio_bucket="b", jwt_secret="s" * 32,
)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**_BASE, **overrides})


def _jpeg(width: int = 40, height: int = 30) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (240, 240, 240)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _pdf(pages: int = 2) -> bytes:
    import pymupdf

    document = pymupdf.open()
    for index in range(pages):
        page = document.new_page(width=595, height=842)      # A4 pt
        page.insert_text((72, 100), f"page {index + 1}")
    return document.tobytes()


def _file(mime: str, *, pages: int = 1, cipher: bool = False) -> InvoiceFile:
    return InvoiceFile(
        uploader_id=1, filename="a.bin", mime=mime, size=1, sha256="0" * 64,
        storage_key="invoices/x.bin", is_cipher=cipher, page_count=pages,
    )


def test_image_bytes_returned_as_is_for_jpeg() -> None:
    payload = _jpeg()
    rendered = image_service.render_image(
        _file("image/jpeg"), payload, page=1, variant="original", settings=_settings()
    )
    assert rendered.content == payload
    assert rendered.media_type == "image/jpeg"
    assert len(rendered.etag) == 64


def test_pdf_page_rendered_at_250_dpi() -> None:
    rendered = image_service.render_image(
        _file("application/pdf", pages=2), _pdf(2), page=2, variant="original",
        settings=_settings(),
    )
    assert rendered.media_type == "image/png"
    with Image.open(io.BytesIO(rendered.content)) as decoded:
        # A4 595×842pt @250dpi ≈ 2066×2924
        assert abs(decoded.width - 2066) <= 3
        assert abs(decoded.height - 2924) <= 3


def test_page_out_of_range_rejected() -> None:
    with pytest.raises(ValidationFailedError):
        image_service.render_image(
            _file("application/pdf", pages=2), _pdf(2), page=3, variant="original",
            settings=_settings(),
        )


def test_cipher_file_rejected() -> None:
    with pytest.raises(ConflictError):
        image_service.render_image(
            _file("image/jpeg", cipher=True), _jpeg(), page=1, variant="original",
            settings=_settings(),
        )


def test_plaintext_disabled_rejected() -> None:
    with pytest.raises(ConflictError):
        image_service.render_image(
            _file("image/jpeg"), _jpeg(), page=1, variant="original",
            settings=_settings(storage_allow_plaintext=False),
        )


def test_preprocessed_variant_returns_png() -> None:
    rendered = image_service.render_image(
        _file("image/jpeg"), _jpeg(160, 120), page=1, variant="preprocessed",
        settings=_settings(),
    )
    assert rendered.media_type == "image/png"
    with Image.open(io.BytesIO(rendered.content)) as decoded:
        assert decoded.format == "PNG"


def test_invalid_variant_rejected() -> None:
    with pytest.raises(ValidationFailedError):
        image_service.render_image(
            _file("image/jpeg"), _jpeg(), page=1, variant="nope", settings=_settings(),
        )
```

`tests/test_invoice_media.py`（HTTP 集成，夹具照 `test_invoices.py`；额外覆盖 `get_storage`）：

```python
@pytest.fixture()
def client(session_factory, clean_db, storage_payload) -> Iterator[TestClient]:
    from app.services.storage import get_storage

    app = create_app()

    def _override_db() -> Iterator[Session]:
        db = session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = _override_db
    app.dependency_overrides[get_storage] = lambda: FakeStorage(storage_payload)
    with TestClient(app, raise_server_exceptions=False) as test_client:
        yield test_client


def test_image_endpoint_owner_ok(client, session_factory, actors, storage_payload) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)   # file.mime=image/jpeg
    response = client.get(f"{BASE}/{invoice_id}/image", headers=_auth(actors["alice"]))
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["etag"].strip('"') == hashlib.sha256(storage_payload).hexdigest()


def test_image_endpoint_permissions(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id)
    assert client.get(f"{BASE}/{invoice_id}/image",
                      headers=_auth(actors["alice"])).status_code == 200
    assert client.get(f"{BASE}/{invoice_id}/image",
                      headers=_auth(actors["rita"])).status_code == 200
    assert client.get(f"{BASE}/{invoice_id}/image",
                      headers=_auth(actors["bob"])).status_code == 404
    assert client.get(f"{BASE}/{invoice_id}/image",
                      headers=_auth(actors["ada"])).status_code == 404


def test_image_endpoint_cipher_returns_409(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id, is_cipher=True)
    response = client.get(f"{BASE}/{invoice_id}/image", headers=_auth(actors["alice"]))
    assert response.status_code == 409
    assert response.json()["code"] == 40900


def test_image_endpoint_page_out_of_range_422(client, session_factory, actors) -> None:
    invoice_id = _make_invoice(session_factory, actors["alice"].id, page_count=1)
    response = client.get(f"{BASE}/{invoice_id}/image?page=2", headers=_auth(actors["alice"]))
    assert response.status_code == 422
```

（`_make_invoice` 需支持 `mime=` 与 `page_count=` 覆盖；本地文件字节用 `_jpeg()` 生成。）

- [ ] **Step 2: 确认失败**：`../.venv/Scripts/python.exe -m pytest -q tests/test_image_service.py`

- [ ] **Step 3: 实现 `image_service.py`**

```python
"""票据影像渲染（S3-5）：原图直出 / PDF 按 250 DPI 渲染 / 预处理变体。

安全边界（D-063）：只有 `STORAGE_ALLOW_PLAINTEXT=true` 且文件 `is_cipher=false` 才返回明文；
S4 接入信封后在这里换成"内存解密 → 渲染 → 立即释放"，接口形状不变。
PDF 固定 250 DPI 渲染：与 S3-1 `prepare_pages(pdf_dpi=250)` 一致，否则字段框会按比例全偏。
"""
from __future__ import annotations

import hashlib
import io

import cv2
import numpy as np

from app.core.config import Settings
from app.core.exceptions import ConflictError, ValidationFailedError
from app.models.invoice_files import InvoiceFile
from app.ocr.intake import prepare_input
from app.services.storage import StorageService

PDF_DPI = 250
_VARIANTS = {"original", "preprocessed"}


@dataclass(frozen=True)
class RenderedImage:
    content: bytes
    media_type: str
    etag: str


def _etag(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _png(image: np.ndarray) -> bytes:
    ok, buffer = cv2.imencode(".png", image)
    if not ok:
        raise ValidationFailedError("影像编码失败")
    return buffer.tobytes()


def _render_pdf_page(data: bytes, page: int, dpi: int = PDF_DPI) -> bytes:
    import pymupdf

    with pymupdf.open(stream=data, filetype="pdf") as document:
        pixmap = document.load_page(page - 1).get_pixmap(dpi=dpi)
        return pixmap.tobytes("png")


def render_image(
    file_row: InvoiceFile, data: bytes, *, page: int, variant: str, settings: Settings
) -> RenderedImage:
    if file_row.is_cipher or not settings.storage_allow_plaintext:
        raise ConflictError("加密影像解密尚未接入（S4）")
    if variant not in _VARIANTS:
        raise ValidationFailedError(f"variant 只能是 {'/'.join(sorted(_VARIANTS))}")
    if page < 1 or page > (file_row.page_count or 1):
        raise ValidationFailedError(f"页码越界：1–{file_row.page_count or 1}")

    if file_row.mime == "application/pdf":
        content = _render_pdf_page(data, page)
        return RenderedImage(content, "image/png", _etag(content))

    if variant == "preprocessed":
        prepared = prepare_input(data)
        content = _png(prepared.image)
        return RenderedImage(content, "image/png", _etag(content))

    media_type = file_row.mime if file_row.mime in {"image/jpeg", "image/png"} else "application/octet-stream"
    return RenderedImage(data, media_type, _etag(data))


def get_invoice_image(
    db, user, invoice_id: int, *, page: int, variant: str,
    settings: Settings, storage: StorageService,
) -> RenderedImage:
    invoice = get_visible_or_404(db, Invoice, invoice_id, user)
    file_row = db.get(InvoiceFile, invoice.file_id)
    if file_row is None:
        raise NotFoundError("票据文件不存在")
    # 先做闸门判定再下载：密文时不必把字节读进内存
    if file_row.is_cipher or not settings.storage_allow_plaintext:
        raise ConflictError("加密影像解密尚未接入（S4）")
    data = storage.get_bytes(file_row.storage_key, max_bytes=settings.max_upload_mb * 1024 * 1024)
    return render_image(file_row, data, page=page, variant=variant, settings=settings)
```

- [ ] **Step 4: 路由**

```python
@router.get(
    "/{invoice_id}/image",
    summary="票据影像（原图/预处理图；PDF 按 250 DPI 渲染）",
    response_class=Response,
    responses={200: {"content": {"image/jpeg": {}, "image/png": {}}}},
)
def get_invoice_image(
    invoice_id: int,
    page: int = Query(1, ge=1),
    variant: Literal["original", "preprocessed"] = "original",
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    storage: StorageService = Depends(get_storage),
) -> Response:
    rendered = image_service.get_invoice_image(
        db, user, invoice_id, page=page, variant=variant, settings=settings, storage=storage
    )
    return Response(
        content=rendered.content,
        media_type=rendered.media_type,
        headers={
            "ETag": f'"{rendered.etag}"',
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
        },
    )
```

（顶部补 `Literal`、`Query` 已用、`Response`、`Settings/get_settings`、`StorageService/get_storage`、
`image_service` 导入。）

- [ ] **Step 5: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_image_service.py
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_invoice_media.py
../.venv/Scripts/python.exe -m pytest -q tests/test_api_docs.py
```

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/image_service.py backend/app/api/v1/invoices.py \
  backend/tests/test_image_service.py backend/tests/test_invoice_media.py
git commit -m "feat(s3-5): 实现 GET /invoices/{id}/image（原图/PDF 250DPI/预处理，明文仅本地）"
```

---

### Task 2: `GET /invoices/{id}/lines`（行框，映射原图坐标、不含文本）

**Files:**
- Create: `backend/app/services/line_service.py`
- Modify: `backend/app/schemas/invoice.py`、`backend/app/api/v1/invoices.py`、`docs/API.md` §2
- Test: `backend/tests/test_lines_service.py`（单元）、补 `test_invoice_media.py`（HTTP）

**Interfaces:**
- `OcrLineBoxOut{line_no:int, bbox:list[int], score:float|None, field_name:str|None}`
- `InvoiceLinesData{invoice_id:int, page_no:int, lines:list[OcrLineBoxOut]}`
- `get_invoice_lines(db, user, invoice_id, *, page, settings, storage) -> InvoiceLinesData`

- [ ] **Step 1: 写失败测试**

```python
"""S3-5 行框：原图坐标映射、字段归属、绝不返回文本。"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import numpy as np
import pytest

from app.core.config import Settings
from app.core.exceptions import ConflictError, ValidationFailedError
from app.services import line_service

_BASE = dict(
    database_url="postgresql://u:p@127.0.0.1:5433/db",
    redis_url="redis://:p@127.0.0.1:6380/0",
    minio_endpoint="127.0.0.1:9002", minio_access_key="k", minio_secret_key="s",
    minio_bucket="b", jwt_secret="s" * 32,
)


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{**_BASE, **overrides})


class _Prepared:
    class preprocess:                       # 假的预处理结果：0.5× 放大
        to_original = np.array([[0.5, 0.0, 0.0], [0.0, 0.5, 0.0], [0.0, 0.0, 1.0]])


def _fake_storage(payload: bytes = b"fake-image"):
    class _Storage:
        def get_bytes(self, key, *, max_bytes):
            return payload
    return _Storage()


def test_lines_map_to_original_and_link_field(
    session_factory, actors, monkeypatch
) -> None:
    monkeypatch.setattr(line_service, "prepare_input", lambda data: _Prepared())
    invoice_id, _ = _make_invoice_with_line(
        session_factory, actors["alice"].id,
        line_box=[[10, 20], [300, 20], [300, 60], [10, 60]],   # 校正图四点
        field_name="total_amount",
    )
    with session_factory() as db:
        user = db.get(User, actors["alice"].id)
        data = line_service.get_invoice_lines(
            db, user, invoice_id, page=1, settings=_settings(), storage=_fake_storage()
        )

    assert data.page_no == 1
    assert data.lines[0].line_no == 0
    assert data.lines[0].bbox == [5, 10, 150, 30]        # 0.5× 映射后的原图外接框
    assert data.lines[0].field_name == "total_amount"
    assert "text" not in data.lines[0].model_dump()       # 不返回票面文本


def test_lines_page_out_of_range_rejected(session_factory, actors) -> None:
    invoice_id, _ = _make_invoice_with_line(session_factory, actors["alice"].id)
    with session_factory() as db:
        user = db.get(User, actors["alice"].id)
        with pytest.raises(ValidationFailedError):
            line_service.get_invoice_lines(
                db, user, invoice_id, page=2, settings=_settings(), storage=_fake_storage()
            )


def test_lines_cipher_rejected(session_factory, actors) -> None:
    invoice_id, _ = _make_invoice_with_line(
        session_factory, actors["alice"].id, is_cipher=True
    )
    with session_factory() as db:
        user = db.get(User, actors["alice"].id)
        with pytest.raises(ConflictError):
            line_service.get_invoice_lines(
                db, user, invoice_id, page=1, settings=_settings(), storage=_fake_storage()
            )
```

辅助（测试文件内定义，不用真预处理）：

```python
def _make_invoice_with_line(session_factory, uploader_id: int, *, line_box=None,
                            field_name: str | None = None, is_cipher: bool = False) -> tuple[int, int]:
    with session_factory() as db:
        file = InvoiceFile(uploader_id=uploader_id, filename="a.jpg", mime="image/jpeg",
                           size=1024, sha256=secrets.token_hex(32),
                           storage_key=f"invoices/{secrets.token_hex(8)}.bin",
                           is_cipher=is_cipher, page_count=1)
        db.add(file)
        db.flush()
        invoice = invoice_service.create_draft(db, uploader_id=uploader_id, file_id=file.id)
        db.add(OcrLine(invoice_id=invoice.id, page_no=1, line_no=0, text="价税合计 ￥1,234.56",
                       score=Decimal("0.950"),
                       box_json=line_box or [[100, 300], [900, 300], [900, 400], [100, 400]]))
        if field_name:
            db.add(InvoiceField(invoice_id=invoice.id, field_name=field_name,
                                field_value="￥1,234.56", normalized_value="1234.56",
                                raw_text="价税合计 ￥1,234.56", source="ocr",
                                confidence=Decimal("0.700"),
                                bbox_json=[50, 150, 450, 200], source_page_no=1,
                                source_line_no=0))
        db.commit()
        return invoice.id, file.id
```

- [ ] **Step 2: 确认失败**

- [ ] **Step 3: 实现**

```python
"""行框数据（S3-5）：ocr_lines 四点（校正图）→ 原图外接框；**不返回行文本**。"""

def get_invoice_lines(db, user, invoice_id: int, *, page: int,
                      settings: Settings, storage: StorageService) -> InvoiceLinesData:
    invoice = get_visible_or_404(db, Invoice, invoice_id, user)
    file_row = db.get(InvoiceFile, invoice.file_id)
    if file_row is None:
        raise NotFoundError("票据文件不存在")
    if page < 1 or page > (file_row.page_count or 1):
        raise ValidationFailedError(f"页码越界：1–{file_row.page_count or 1}")
    if file_row.is_cipher or not settings.storage_allow_plaintext:
        raise ConflictError("加密影像解密尚未接入（S4）")

    matrix = None
    if file_row.mime != "application/pdf":          # 照片：重跑预处理拿 to_original
        data = storage.get_bytes(file_row.storage_key,
                                 max_bytes=settings.max_upload_mb * 1024 * 1024)
        matrix = prepare_input(data).preprocess.to_original

    rows = db.scalars(
        select(OcrLine).where(OcrLine.invoice_id == invoice.id, OcrLine.page_no == page)
        .order_by(OcrLine.line_no.asc())
    ).all()
    owners = {
        (field.source_page_no, field.source_line_no): field.field_name
        for field in db.scalars(
            select(InvoiceField).where(InvoiceField.invoice_id == invoice.id)
        ).all()
        if field.source_line_no is not None
    }
    lines = [
        OcrLineBoxOut(
            line_no=row.line_no,
            bbox=list(bbox_to_original(points_to_bbox(row.box_json or []), matrix))
            if row.box_json else [],
            score=None if row.score is None else float(row.score),
            field_name=owners.get((page, row.line_no)),
        )
        for row in rows
    ]
    return InvoiceLinesData(invoice_id=invoice.id, page_no=page, lines=lines)
```

（`bbox` 为空/畸形时跳过该行或给空框；**先过滤 `box_json` 非四点**，不要制造 `[0,0,0,0]` 假框。）

- [ ] **Step 4: 路由 + 契约**

```python
@router.get("/{invoice_id}/lines", response_model=Envelope[InvoiceLinesData],
            summary="行框（原图坐标，不含文本）")
def get_invoice_lines_endpoint(
    invoice_id: int,
    page: int = Query(1, ge=1),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    storage: StorageService = Depends(get_storage),
) -> dict:
    return ok(line_service.get_invoice_lines(
        db, user, invoice_id, page=page, settings=settings, storage=storage
    ))
```

`docs/API.md` §2 在 image 之后追加 lines 条目（spec §3.2 原文），并把 image 的"实现"改为 `S3-5 ✅`，
备注写明"明文仅本地调试；密文/生产 409 待 S4"。

- [ ] **Step 5: 验证**

```bash
../.venv/Scripts/python.exe -m pytest -q tests/test_lines_service.py
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_invoice_media.py
../.venv/Scripts/python.exe -m pytest -q tests/test_api_docs.py
```

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/line_service.py backend/app/schemas/invoice.py \
  backend/app/api/v1/invoices.py docs/API.md backend/tests/test_lines_service.py \
  backend/tests/test_invoice_media.py
git commit -m "feat(s3-5): GET /invoices/{id}/lines（行框映射原图坐标，不含文本）"
```

---

### Task 3: 前端坐标工具 + API 封装

**Files:**
- Create: `web/src/utils/bbox.ts`
- Modify: `web/src/api/http.ts`、`web/src/api/invoices.ts`、`web/src/types/invoice.ts`

- [ ] **Step 1: `bbox.ts`**

```ts
/** 影像框坐标的唯一换算实现（S3-5）。
 *
 * 后端给的 bbox 是**原图**坐标 [x1,y1,x2,y2]；显示时按 displayWidth/naturalWidth 缩放。
 * 缩放通过改 img 的 CSS 宽高实现（不是 CSS transform），这样 canvas 始终按像素重绘、不会发虚。
 */
export interface Box { x1: number; y1: number; x2: number; y2: number }

export function clampZoom(zoom: number): number {
  return Math.min(4, Math.max(1, zoom))
}

export function toDisplayBox(bbox: number[], sx: number, sy: number): Box {
  return { x1: bbox[0] * sx, y1: bbox[1] * sy, x2: bbox[2] * sx, y2: bbox[3] * sy }
}

export function intersects(box: Box, view: Box): boolean {
  return box.x2 >= view.x1 && box.x1 <= view.x2 && box.y2 >= view.y1 && box.y1 <= view.y2
}

export function hitTest<T extends { id: string; box: Box }>(items: T[], x: number, y: number): string | null {
  let best: T | null = null
  for (const item of items) {
    if (x >= item.box.x1 && x <= item.box.x2 && y >= item.box.y1 && y <= item.box.y2) {
      const area = (item.box.x2 - item.box.x1) * (item.box.y2 - item.box.y1)
      if (!best || area < (best.box.x2 - best.box.x1) * (best.box.y2 - best.box.y1)) best = item
    }
  }
  return best?.id ?? null
}
```

- [ ] **Step 2: `http.getBlob`**

```ts
/** 二进制下载（影像）：不解包 JSON 壳，但仍走鉴权与 401 单飞。 */
export async function getBlob(url: string, config?: HttpRequestConfig): Promise<Blob> {
  const response = await http.get<Blob>(url, { ...config, responseType: 'blob' })
  return response.data
}
```

- [ ] **Step 3: API 与类型**

```ts
// types/invoice.ts
export interface OcrLineBox {
  line_no: number
  bbox: number[]
  score: number | null
  field_name: string | null
}
export interface InvoiceLinesData {
  invoice_id: number
  page_no: number
  lines: OcrLineBox[]
}

// api/invoices.ts
export async function fetchInvoiceImage(
  invoiceId: number, page = 1, variant: 'original' | 'preprocessed' = 'original',
): Promise<Blob> {
  return getBlob(`/invoices/${invoiceId}/image`, { params: { page, variant }, silent: true })
}

export async function fetchInvoiceLines(invoiceId: number, page = 1): Promise<InvoiceLinesData> {
  const envelope = await get<InvoiceLinesData>(`/invoices/${invoiceId}/lines`,
    { params: { page }, silent: true })
  return unwrap(envelope)
}
```

- [ ] **Step 4: 验证**：`cd web && npm run typecheck`

- [ ] **Step 5: 提交**

```bash
git add web/src/utils/bbox.ts web/src/api/http.ts web/src/api/invoices.ts web/src/types/invoice.ts
git commit -m "feat(s3-5): 前端坐标换算工具与影像/行框接口封装"
```

---

### Task 4: `ImageCanvas.vue`

**Files:**
- Create: `web/src/components/ImageCanvas.vue`

**Interfaces（props/emits 固定，供 Task 5 使用）：**
- props：`src: string`、`naturalWidth/naturalHeight: number`、`fieldBoxes: Array<{fieldName,label,bbox,level,conflict}>`、
  `lineBoxes: Array<{lineNo,bbox,score,fieldName}>`、`activeField: string|null`、`hoveredField: string|null`、
  `page: number`、`pageCount: number`
- emits：`select-field(fieldName: string|null)`、`hover-field(fieldName: string|null)`、`update:page(page: number)`
- 暴露：`scrollToField(fieldName)`（父组件点表格时调用）

- [ ] **Step 1: 实现**

```vue
<script setup lang="ts">
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { clampZoom, hitTest, intersects, toDisplayBox, type Box } from '../utils/bbox'

interface FieldBoxInput {
  fieldName: string
  label: string
  bbox: number[]
  level: string | null
  conflict: boolean
}
interface LineBoxInput {
  lineNo: number
  bbox: number[]
  score: number | null
  fieldName: string | null
}

const props = defineProps<{
  src: string
  fieldBoxes: FieldBoxInput[]
  lineBoxes: LineBoxInput[]
  activeField: string | null
  hoveredField: string | null
  page: number
  pageCount: number
}>()

const emit = defineEmits<{
  'select-field': [fieldName: string | null]
  'hover-field': [fieldName: string | null]
  'update:page': [page: number]
}>()

const wrap = ref<HTMLDivElement | null>(null)
const imageEl = ref<HTMLImageElement | null>(null)
const canvas = ref<HTMLCanvasElement | null>(null)
const zoom = ref(1)
const naturalW = ref(0)
const naturalH = ref(0)
const baseWidth = ref(0)
const drawMs = ref(0)
const visibleLines = ref(0)
let frame = 0
let dragging = false
let dragMoved = false
let hoveredLocal: string | null = null
const last = { x: 0, y: 0 }

const displayWidth = computed(() => Math.max(1, Math.round(baseWidth.value * zoom.value)))
const displayHeight = computed(() =>
  naturalW.value ? Math.max(1, Math.round((displayWidth.value * naturalH.value) / naturalW.value)) : 1)
const scaleX = computed(() => (naturalW.value ? displayWidth.value / naturalW.value : 1))
const scaleY = computed(() => (naturalH.value ? displayHeight.value / naturalH.value : 1))

const fieldDisplayBoxes = computed(() =>
  props.fieldBoxes
    .filter((item) => item.bbox.length === 4)
    .map((item) => ({
      fieldName: item.fieldName,
      level: item.level,
      conflict: item.conflict,
      box: toDisplayBox(item.bbox, scaleX.value, scaleY.value),
    })),
)
const lineDisplayBoxes = computed(() =>
  props.lineBoxes
    .filter((item) => item.bbox.length === 4)
    .map((item) => ({
      lineNo: item.lineNo,
      fieldName: item.fieldName,
      box: toDisplayBox(item.bbox, scaleX.value, scaleY.value),
    })),
)

function scheduleDraw(): void {
  if (frame) return
  frame = requestAnimationFrame(() => {
    frame = 0
    draw()
  })
}

function draw(): void {
  const element = canvas.value
  if (!element || !displayWidth.value) return
  const started = performance.now()
  const dpr = window.devicePixelRatio || 1
  element.width = Math.round(displayWidth.value * dpr)
  element.height = Math.round(displayHeight.value * dpr)
  element.style.width = `${displayWidth.value}px`
  element.style.height = `${displayHeight.value}px`
  const ctx = element.getContext('2d')
  if (!ctx) return
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  ctx.clearRect(0, 0, displayWidth.value, displayHeight.value)

  const left = wrap.value?.scrollLeft ?? 0
  const top = wrap.value?.scrollTop ?? 0
  const view: Box = {
    x1: left,
    y1: top,
    x2: left + (wrap.value?.clientWidth ?? 0),
    y2: top + (wrap.value?.clientHeight ?? 0),
  }

  let visible = 0
  for (const item of lineDisplayBoxes.value) {
    if (!intersects(item.box, view)) continue
    visible += 1
    const hot = Boolean(item.fieldName) &&
      (item.fieldName === props.activeField || item.fieldName === props.hoveredField)
    ctx.strokeStyle = hot ? 'rgba(64,158,255,0.95)' : 'rgba(120,150,190,0.45)'
    ctx.lineWidth = hot ? 2 : 1
    ctx.strokeRect(item.box.x1, item.box.y1, item.box.x2 - item.box.x1, item.box.y2 - item.box.y1)
  }
  visibleLines.value = visible

  for (const item of fieldDisplayBoxes.value) {
    if (!intersects(item.box, view)) continue
    const active = item.fieldName === props.activeField
    const hovered = item.fieldName === props.hoveredField
    const color = item.conflict || item.level === 'manual' ? '#f56c6c'
      : item.level === 'review' ? '#e6a23c' : '#67c23a'
    if (active) {
      ctx.fillStyle = 'rgba(230,162,60,0.16)'
      ctx.fillRect(item.box.x1, item.box.y1, item.box.x2 - item.box.x1, item.box.y2 - item.box.y1)
    }
    ctx.strokeStyle = color
    ctx.lineWidth = active ? 3 : hovered ? 2.5 : 1.6
    ctx.setLineDash(item.conflict ? [6, 3] : [])
    ctx.strokeRect(item.box.x1, item.box.y1, item.box.x2 - item.box.x1, item.box.y2 - item.box.y1)
    ctx.setLineDash([])
  }

  const spent = performance.now() - started
  drawMs.value = Math.round((drawMs.value * 0.7 + spent * 0.3) * 100) / 100
}

function canvasPoint(event: PointerEvent): { x: number; y: number } | null {
  const rect = canvas.value?.getBoundingClientRect()
  if (!rect) return null
  return { x: event.clientX - rect.left, y: event.clientY - rect.top }
}

function boxAt(event: PointerEvent): { field: string | null; line: LineBoxInput | null } {
  const point = canvasPoint(event)
  if (!point) return { field: null, line: null }
  const field = hitTest(
    fieldDisplayBoxes.value.map((item) => ({ id: item.fieldName, box: item.box })),
    point.x,
    point.y,
  )
  const lineId = hitTest(
    lineDisplayBoxes.value.map((item) => ({ id: `L${item.lineNo}`, box: item.box })),
    point.x,
    point.y,
  )
  const line = lineId ? lineDisplayBoxes.value.find((item) => `L${item.lineNo}` === lineId) ?? null : null
  return { field, line }
}

function onWheel(event: WheelEvent): void {
  event.preventDefault()
  const before = zoom.value
  const next = clampZoom(before * (event.deltaY < 0 ? 1.2 : 1 / 1.2))
  if (next === before) return
  const rect = canvas.value?.getBoundingClientRect()
  const ratioX = rect && rect.width ? (event.clientX - rect.left) / rect.width : 0.5
  const ratioY = rect && rect.height ? (event.clientY - rect.top) / rect.height : 0.5
  zoom.value = next
  void nextTick(() => {
    const container = wrap.value
    if (!container) return
    const containerRect = container.getBoundingClientRect()
    container.scrollLeft = displayWidth.value * ratioX - (event.clientX - containerRect.left)
    container.scrollTop = displayHeight.value * ratioY - (event.clientY - containerRect.top)
    scheduleDraw()
  })
}

function onPointerDown(event: PointerEvent): void {
  dragging = true
  dragMoved = false
  last.x = event.clientX
  last.y = event.clientY
  canvas.value?.setPointerCapture?.(event.pointerId)
}

function onPointerMove(event: PointerEvent): void {
  if (dragging && wrap.value) {
    const dx = event.clientX - last.x
    const dy = event.clientY - last.y
    if (Math.abs(dx) + Math.abs(dy) > 3) dragMoved = true
    wrap.value.scrollLeft -= dx
    wrap.value.scrollTop -= dy
    last.x = event.clientX
    last.y = event.clientY
    scheduleDraw()
    return
  }
  const hit = boxAt(event)
  const nextHover = hit.field ?? hit.line?.fieldName ?? null
  if (nextHover !== hoveredLocal) {
    hoveredLocal = nextHover
    emit('hover-field', nextHover)
  }
}

function onPointerUp(event: PointerEvent): void {
  dragging = false
  canvas.value?.releasePointerCapture?.(event.pointerId)
}

function onCanvasClick(event: MouseEvent): void {
  if (dragMoved) {
    dragMoved = false
    return
  }
  const hit = boxAt(event as unknown as PointerEvent)
  emit('select-field', hit.field ?? hit.line?.fieldName ?? null)
}

function onImageLoad(): void {
  const element = imageEl.value
  if (element) {
    naturalW.value = element.naturalWidth
    naturalH.value = element.naturalHeight
  }
  baseWidth.value = wrap.value?.clientWidth ?? 0
  scheduleDraw()
}

function scrollToField(fieldName: string): void {
  const item = fieldDisplayBoxes.value.find((entry) => entry.fieldName === fieldName)
  const container = wrap.value
  if (!item || !container) return
  container.scrollTo({
    left: Math.max(0, item.box.x1 - container.clientWidth / 2),
    top: Math.max(0, item.box.y1 - container.clientHeight / 2),
    behavior: 'smooth',
  })
}

let observer: ResizeObserver | null = null
onMounted(() => {
  if (wrap.value && typeof ResizeObserver !== 'undefined') {
    observer = new ResizeObserver(() => {
      baseWidth.value = wrap.value?.clientWidth ?? 0
      scheduleDraw()
    })
    observer.observe(wrap.value)
  }
})
onBeforeUnmount(() => {
  observer?.disconnect()
  if (frame) cancelAnimationFrame(frame)
})

watch(
  [() => props.fieldBoxes, () => props.lineBoxes, () => props.activeField,
   () => props.hoveredField, zoom, () => props.src],
  scheduleDraw,
  { deep: true },
)

defineExpose({ scrollToField, draw })
</script>

<template>
  <div class="canvas-shell">
    <div class="canvas-toolbar">
      <el-button-group size="small">
        <el-button @click="zoom = clampZoom(zoom / 1.2)">缩小</el-button>
        <el-button @click="zoom = 1">适应</el-button>
        <el-button @click="zoom = clampZoom(zoom * 1.2)">放大</el-button>
      </el-button-group>
      <span class="zoom-label">{{ Math.round(zoom * 100) }}%</span>
      <span v-if="pageCount > 1" class="pager">
        <el-button size="small" :disabled="page <= 1" @click="emit('update:page', page - 1)">上一页</el-button>
        <span>{{ page }} / {{ pageCount }}</span>
        <el-button size="small" :disabled="page >= pageCount" @click="emit('update:page', page + 1)">下一页</el-button>
      </span>
      <span class="hint">滚轮缩放 · 拖拽平移</span>
    </div>
    <div
      ref="wrap"
      class="canvas-scroll"
      @wheel="onWheel"
      @pointerdown="onPointerDown"
      @pointermove="onPointerMove"
      @pointerup="onPointerUp"
      @pointerleave="onPointerUp($event); emit('hover-field', null)"
    >
      <div class="canvas-stage" :style="{ width: `${displayWidth}px`, height: `${displayHeight}px` }">
        <img ref="imageEl" class="invoice-image" :src="src" alt="票据影像"
             draggable="false" @load="onImageLoad" />
        <canvas
          ref="canvas"
          class="box-canvas"
          @click="onCanvasClick"
          :data-image-size="`${naturalW}×${naturalH}`"
          :data-scale="scaleX.toFixed(6)"
          :data-field-boxes="JSON.stringify(fieldDisplayBoxes.map((item) => ({
            field: item.fieldName,
            box: [Math.round(item.box.x1), Math.round(item.box.y1),
                  Math.round(item.box.x2), Math.round(item.box.y2)],
          })))"
          :data-visible-lines="String(visibleLines)"
          :data-draw-ms="String(drawMs)"
        />
      </div>
    </div>
  </div>
</template>

<style scoped>
.canvas-shell { display: flex; flex-direction: column; gap: 8px; }
.canvas-toolbar { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
.canvas-toolbar .hint { font-size: 12px; color: var(--el-text-color-secondary); }
.canvas-toolbar .zoom-label { font-size: 12px; color: var(--el-text-color-secondary); }
.canvas-scroll {
  position: relative;
  max-height: 72vh;
  overflow: auto;
  background: #f5f7fa;
  border: 1px solid var(--el-border-color-lighter);
  cursor: grab;
}
.canvas-scroll:active { cursor: grabbing; }
.canvas-stage { position: relative; }
.invoice-image { display: block; width: 100%; height: 100%; user-select: none; pointer-events: none; }
.box-canvas { position: absolute; inset: 0; }
</style>
```

（关键点：canvas 覆盖在 img 上、**尺寸随 zoom 变**而不是 CSS transform；点击坐标直接用 canvas 的
`getBoundingClientRect` 换算成 canvas CSS 像素，与 `fieldDisplayBoxes` 同一坐标系；
拖拽超过 3px 后抑制 click，避免"拖一下就把字段选中了"。）

- [ ] **Step 2: 验证**：`npm run typecheck`（此时未接入页面，只保证编译）

- [ ] **Step 3: 提交**

```bash
git add web/src/components/ImageCanvas.vue
git commit -m "feat(s3-5): ImageCanvas（img+canvas 叠层、缩放平移、字段/行框高亮与裁剪）"
```

---

### Task 5: `FieldTable.vue` + 详情页整合

**Files:**
- Create: `web/src/components/FieldTable.vue`
- Modify: `web/src/views/InvoiceDetail.vue`、`web/src/utils/format.ts`

- [ ] **Step 1: `FieldTable.vue`**

从现有详情页表格**原样搬**（列：字段/值/置信度/来源/状态/操作），新增：
- props：`fields`、`editMode`、`editable: Record<string,string>`、`editableNames: Set<string>`、
  `reocring: Set<string>`、`activeField`、`hoveredField`；
- emits：`hover-field`、`select-field`、`reread`、`update:editable`；
- 行 class：`manual || conflict` → `row-danger`；`review` → `row-warning`；`activeField` → `row-active`；
- 值列下方补充：冲突行 `<span class="conflict-note">冲突候选：{{ row.field_value }} / {{ row.conflict_value }}，未自动替换</span>`；
  `review_level === 'manual'` 行 `<span class="review-note">必须人工核对</span>`；
- 行事件：`@cell-mouse-enter="emit('hover-field', row.field_name)"`、
  `@row-click="emit('select-field', row.field_name)"`；
- 输入框 `:model-value="editable[row.field_name]"` + `@update:model-value="emit('update:editable', {field: row.field_name, value: $event})"`；
- `defineExpose({ scrollToField })`：`scrollToField(name)` 用 `el-table` 的 `ref` + 行 `ref` 或
  `document.querySelector` 定位 `tr` 后 `scrollIntoView({block:'center', behavior:'smooth'})`，
  并加 500ms 的 `.row-flash` class（CSS 动画）。

- [ ] **Step 2: 详情页布局与加载**

```ts
// 状态
const imageUrl = ref('')            // objectURL
const imageNatural = reactive({ width: 0, height: 0 })
const imageError = ref('')
const page = ref(1)
const lines = ref<OcrLineBox[]>([])
const activeField = ref<string | null>(null)
const hoveredField = ref<string | null>(null)
const canvasRef = ref<InstanceType<typeof ImageCanvas> | null>(null)
const tableRef = ref<InstanceType<typeof FieldTable> | null>(null)

const fieldBoxes = computed(() => sortedFields.value
  .filter((field) => Array.isArray(field.bbox) && field.bbox.length === 4)
  .map((field) => ({ fieldName: field.field_name, label: fieldLabel(field.field_name),
                     bbox: field.bbox as number[], level: field.review_level ?? null,
                     conflict: field.conflict })))

async function loadImage(): Promise<void> {
  imageError.value = ''
  revokeImage()
  try {
    const blob = await fetchInvoiceImage(invoiceId.value, page.value)
    imageUrl.value = URL.createObjectURL(blob)
  } catch (cause) {
    imageUrl.value = ''
    imageError.value = cause instanceof ApiError && cause.status === 409
      ? '影像暂不可用（加密链路待 S4）' : '影像加载失败'
  }
  try {
    lines.value = (await fetchInvoiceLines(invoiceId.value, page.value)).lines
  } catch {
    lines.value = []        // 行框失败只降级，不阻塞字段核对
  }
}

function selectField(name: string | null): void {
  activeField.value = name
  if (!name) return
  canvasRef.value?.scrollToField(name)
  tableRef.value?.scrollToField(name)       // 表格行滚入视口并闪烁（组件内实现）
}
```

- 模板：`.detail-grid` 左侧 `el-card` 放 `<ImageCanvas>`，关键绑定如下：

```vue
<ImageCanvas
  v-if="imageUrl"
  ref="canvasRef"
  :src="imageUrl"
  :field-boxes="fieldBoxes"
  :line-boxes="lines"
  :active-field="activeField"
  :hovered-field="hoveredField"
  :page="page"
  :page-count="detail.invoice.page_count"
  @select-field="selectField"
  @hover-field="hoveredField = $event"
  @update:page="page = $event; loadImage()"
/>
<el-empty v-else :description="imageError || '影像加载中…'" :image-size="70" />
```

无图时下方保留文件元数据（filename/mime/size/page_count/sha256 前 12 位）；
右侧放 `<FieldTable>`（`:fields="sortedFields"`、`:edit-mode="editMode"`、`:editable="editable"`、
`:editable-names="editableNames"`、`:reocring="reocring"`、`:active-field="activeField"`
`:hovered-field="hoveredField"`，`@hover-field` / `@select-field` / `@reread` / `@update:editable` 与父状态联动）；
`page` 变化时 `loadImage()`；
- 编辑/保存/重识别逻辑**留在父组件**，通过 props/emits 传给 FieldTable；
- `format.ts`：`FIELD_LABELS` 增 `total_amount_upper: '大写金额'`；`FIELD_ORDER` 增该字段（紧随 `total_amount`）。

- [ ] **Step 3: 验证**

```bash
cd /e/BiSheXZY/study-OCR/web && npm run typecheck && npm run build
```

- [ ] **Step 4: 提交**

```bash
git add web/src/components/FieldTable.vue web/src/views/InvoiceDetail.vue web/src/utils/format.ts
git commit -m "feat(s3-5): 详情页左图右表与字段/框双向联动"
```

---

### Task 6: 真实冒烟（数值断言 + 截图）+ 全量回归 + 文档

- [ ] **Step 1: 起服务并准备数据**

```bash
cd /e/BiSheXZY/study-OCR/backend
# 终端：uvicorn :8000、arq worker、web: npm run dev、无头 Edge :9222
../.venv/Scripts/python.exe ../scripts/create_user.py --username s35sub --role submitter \
  --real-name S35验收 --password 'S35sub!2026'
cd /e/BiSheXZY/study-OCR && .venv/Scripts/python.exe - <<'PYEOF'
import time, httpx
from pathlib import Path
c = httpx.Client(base_url="http://127.0.0.1:8000", timeout=120)
tok = c.post("/api/v1/auth/login", json={"username": "s35sub", "password": "S35sub!2026"}).json()["data"]["access_token"]
H = {"Authorization": f"Bearer {tok}"}
with Path("samples/synth_v1/vat_digital_landscape_001.jpg").open("rb") as f:
    up = c.post("/api/v1/invoices/upload", headers=H, files={"file": ("a.jpg", f, "image/jpeg")})
iid = up.json()["data"]["invoice_id"]
for _ in range(60):
    s = c.get(f"/api/v1/invoices/{iid}/ocr-status", headers=H).json()["data"]
    if s["ocr_status"] in ("succeeded", "failed"): break
    time.sleep(2)
print("INVOICE_ID", iid)
PYEOF
```

- [ ] **Step 2: 数值断言（ui_smoke `--eval`）**

```bash
# 1) 登录并打开详情，读 canvas 调试属性
node scripts/ui_smoke.mjs "http://127.0.0.1:5173/login" --clear --view 1440x900 \
  --fill 'input[autocomplete="username"]' s35sub \
  --fill 'input[autocomplete="current-password"]' 'S35sub!2026' --click 登录 --expect 我的票据
node scripts/ui_smoke.mjs "http://127.0.0.1:5173/invoices/<INVOICE_ID>" --view 1440x900 \
  --eval "(() => { const c=document.querySelector('canvas'); const img=document.querySelector('img.invoice-image'); return JSON.stringify({data:{...c.dataset}, natural:[img.naturalWidth,img.naturalHeight], display:[img.clientWidth,img.clientHeight], lines:document.querySelectorAll('.el-table__row').length}) })()" \
  --shot /tmp/s35-normal.png
```

人工核对数值（写进 PROGRESS）：
- `data-image-size` == `natural`；
- `data-scale` == `display[0]/natural[0]`；
- `data-field-boxes` 里 `total_amount` 的坐标 == 接口 `bbox × scale`（±1px）；
- `data-visible-lines` ≤ 行框总数。

```bash
# 2) 放大两次后再读一次（--steps 点击"放大"两次）
node scripts/ui_smoke.mjs "http://127.0.0.1:5173/invoices/<INVOICE_ID>" --view 1440x900 \
  --steps '[{"type":"click","value":"放大"},{"type":"click","value":"放大"}]' \
  --eval "(() => { const c=document.querySelector('canvas'); return JSON.stringify({...c.dataset}) })()" \
  --shot /tmp/s35-zoom.png
```

判定：scale 约为原来的 1.44×（1.2²）；同一字段框坐标按新 scale 重算后与 `data-field-boxes` 一致；
`data-draw-ms ≤ 16`；两张截图交用户目视抽查（AI 看不了图）。

- [ ] **Step 3: 全量回归**

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m alembic check
../.venv/Scripts/python.exe -m pytest -q
../.venv/Scripts/python.exe -m pytest -m integration -q
cd ../web && npm run typecheck && npm run build
```

- [ ] **Step 4: 清理冒烟数据与账号**（同前几卡：`cleanup([iid])` + 删用户），然后文档收口：

- `PROGRESS`：勾 S3-5 + 表格行（接口、数值断言、绘制耗时、截图路径）；关闭 #44/#45；
  新增遗留：明文影像仅本地（S4 切解密）、校验失败文案待 S3-6、行框接口为本卡新增契约；
- `DECISIONS`：**D-063**（image 按契约实现、明文闸门、PDF 250DPI、blob 取图）；
  **D-064**（lines 新接口、服务端映射原图坐标、不含文本；canvas 字段/行框分级 + culling；缩放改 CSS 尺寸而非 transform）；
- `API.md`/`AGENTS`/`HANDOFF` 同步（§10.8 锚点："详情页影像画框" = `ImageCanvas/FieldTable/bbox.ts` +
  `image_service/line_service`）；
- spec/plan 状态改"已实现"。

- [ ] **Step 5: 提交**

```bash
git add docs AGENTS.md
git commit -m "docs(s3-5): 影像画框口径、行框契约与实测收口"
```

---

## 自检（对照 spec）

| spec 条目 | 覆盖 Task |
|---|---|
| §1 产出 1–10 | Task 1–6 |
| §2 Q1/Q2/Q3 | Task 1、5、2 |
| §3.1 image 契约 | Task 1 |
| §3.2 lines 契约 | Task 2 |
| §4 坐标与渲染（250DPI、CSS 尺寸缩放、culling） | Task 1/2/4 |
| §5 组件（ImageCanvas/FieldTable/Detail） | Task 4/5 |
| §6 测试（后端单元/集成、前端数值断言） | Task 1/2/6 |
| §7 验收命令与指标 | Task 6 |
| §8 收口 | Task 6 |

## 回滚与降级

| 情况 | 处理 |
|---|---|
| 影像接口在密文/生产返回 409 | 前端显示"影像暂不可用（加密链路待 S4）"，字段表照常可用 |
| 行框接口失败 | 静默降级为只画字段框（页面不报错） |
| 框对不齐 | 先查 `data-scale` 与 `data-image-size`；PDF 检查是否 250 DPI；照片检查 `to_original` 映射是否生效 |
| 绘制耗时 >16ms | 检查 culling 是否生效（`data-visible-lines`）；必要时把行框绘制降为 0.7px 并跳过 hover 重绘 |
| 用户需要更高倍缩放 | 改 `clampZoom` 上限即可（4×→6×），注意 canvas 像素上限 |