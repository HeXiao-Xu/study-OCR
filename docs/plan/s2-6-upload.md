# S2-6 实现计划（plan）

> 对应 spec：`docs/spec/s2-6-upload.md` ｜ 契约：`docs/API.md` §2 ｜ 状态：**已实现（2026-11-19）**，计划与实现同步落盘

## Step 1 — 配置与存储封装

- `core/config.py`：`storage_allow_plaintext: bool = True`、`upload_max_pdf_pages: int = 5`；
  validator：`environment='prod'` 且允许明文 → `ConfigError`。
- `services/storage.py`：`StorageService`（`minio.Minio`）：
  - `build_object_key(now) -> "invoices/yyyy/mm/<32hex>.bin"`；
  - `put(key, fileobj, length, content_type)` / `delete(key)` / `exists(key)`；
  - S3 错误 → `ServiceUnavailableError`（503）。
- 依赖 `get_storage()`（`lru_cache`），测试可覆盖。

**验证**：单元/导入检查；配置 validator 单测（prod+明文 → ConfigError）。

## Step 2 — 上传服务

- `services/upload_service.py::upload_invoice(db, user, upload, settings, storage)`：
  1. 角色：admin → `NotFoundError`，非 submitter → `RoleForbiddenError`；
  2. 分块读 `upload.file`：size、sha256、首 8 字节魔数；
  3. 空文件 → 422；超 `max_upload_mb` → 413；类型不在 jpeg/png/pdf → 422；
  4. 图片 Pillow `verify()`、PDF `pypdf` 页数 ≤ `upload_max_pdf_pages`；失败 → 422；
  5. 去重查 `invoice_files.sha256`：命中 → `409/40901` + `detail={"invoice_id": ...}`；
  6. `storage.put(...)`（`is_cipher = not settings.storage_allow_plaintext`）；
  7. `_persist(...)` 同一事务写 file/invoice/job；失败补偿 `storage.delete`；
  8. 返回 `UploadData`。
- 文件名清洗：`Path(name).name`、去控制字符、截断 255；原文件名只入库展示。

**验证**：单元级路径（伪造 UploadFile）或直接进 Step 4 集成测试。

## Step 3 — 路由与异常

- `api/v1/files.py`：`POST /invoices/upload`，`response_model=Envelope[UploadData]`、`status_code=202`；
- `core/exceptions.py` 处理器：错误响应的 `data` 使用 `exc.detail or None`（供 409 返回 `invoice_id`）；
- `main.py` 挂载；`schemas/invoice.py` 加 `UploadData`。

**验证**：`pytest -q` 不回归；手工 curl 202/409。

## Step 4 — 测试（`tests/test_upload.py`）

- 临时库 + 假存储 + 覆盖 `get_settings` / `get_storage`；
- 成功/去重/空文件/超限/假图/TIFF/PDF 6 页/权限/补偿/文件名清洗（见 spec §5）。

**验证**：`pytest -m integration -q tests/test_upload.py`。

## Step 5 — 回归与收口

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m pytest
../.venv/Scripts/python.exe -m pytest -m integration
../.venv/Scripts/python.exe -m alembic check
```

- `DECISIONS.md` D-044；`PROGRESS.md` 勾 S2-6；`AGENTS.md` 锚点；`HANDOFF.md` 下一步 S2-7；
- 提交 `feat(s2-6): 上传/去重/MinIO 存储与补偿`；**不 push**。

## 回滚与降级

| 情况 | 处理 |
|---|---|
| 真实 MinIO 不可用 | 上传返回 503；测试用假存储，不阻塞 CI |
| 落库失败 | 必删对象；测试用 monkeypatch 钉住 |
| 并发同 sha | 唯一索引兜底，补偿删除后转 409 |
| 明文路径被误用于 prod | 配置 validator 启动即失败 |