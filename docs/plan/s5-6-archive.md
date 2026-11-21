# S5-6 归档与检索 — 实现计划（plan）

> 对应 spec：[`docs/spec/s5-6-archive.md`](../spec/s5-6-archive.md)｜
> 任务卡：实施计划第 8 章 S5-6｜状态：**已实现（2026-11-21）**，Task 0–9 全部执行并验证
> **本卡有迁移 0009**（前五张卡都没有归档字段），且**先改 SCHEMA/API.md 再写码**。

## Global Constraints

- 归档不可逆：接口必须 `confirm=true`（否则 422/42202）；只有 `approved` 可归档；
- 归档后任何修改/决策入口 409（沿用已实现的状态机闸门，需测试覆盖）；
- 归档号 `AR-YYYY-NNNN` 用 PG 序列 + 业务年（`Asia/Shanghai`），UNIQUE 兜底；
- 归档包**不得含影像明文/对象名/私钥**；整体口令加密（PBKDF2 200k + AES-256-GCM）；
- 检索走 `filter_visible`（submitter 本人 / reviewer 全部 / admin 403）；分页复用 S2-4；
- 审计 `action=archive` 同事务。

## 文件结构

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/alembic/versions/0009_report_archive.py` | 两列 + 序列 + CHECK + `audit_logs.action` 放开 `archive` | 新 |
| `backend/app/models/{report_forms,enums}.py` | 两列/约束；`AuditAction.ARCHIVE` | 改 |
| `backend/app/crypto/archive_crypto.py` | `seal_archive/open_archive`（PBKDF2+AES-GCM 容器） | 新 |
| `backend/app/services/archive_service.py` | `archive_report` / `search_reports` / `export_archive` | 新 |
| `backend/app/schemas/archive.py` + `report.py` | 归档/检索/导出出入参；`ReportOut` 补 `archive_no/archive_at` | 新/改 |
| `backend/app/api/v1/admin.py` | `POST /reports/{id}/archive` | 改 |
| `backend/app/api/v1/reports.py` | `GET ""`（归档检索）、`POST /{id}/export` | 改 |
| `scripts/open_archive.py` | 口令解开归档包（列成员/解压） | 新 |
| `backend/tests/{test_archive_crypto,test_archive}.py` | 4 纯函数 + 11 集成 | 新 |

---

### Task 0: 前提

- [x] `approved → archived` 已在 `report_service.ALLOWED_TRANSITIONS`；
- [x] `report_forms` 无非归档列 → 需 SCHEMA + 迁移 0009；
- [x] 归档/检索/导出三条接口在 API.md 里不存在 → 先补契约；
- [x] `cryptography` 已装（PBKDF2/AES-GCM），**不引入新依赖**（不用 pyzipper）。

### Task 1: 契约先行

- [x] SCHEMA §5.1 增 `archive_no`/`archive_at` 两行 + 两条 CHECK；§9.19b 增 `archive`；
- [x] API.md §0 矩阵说明 admin 归档例外；§4 增 `GET /reports`、`POST /reports/{id}/export`；§7 增 archive 接口。

### Task 2: 迁移 0009 + 模型

- [x] `add_column archive_no TEXT`、`archive_at TIMESTAMPTZ`；`uq_report_forms_archive_no`；
      `ck_report_forms_archive_pair`、`ck_report_forms_archived_fields`；`CREATE SEQUENCE archive_no_seq`；
- [x] 放开 `ck_audit_logs_action` 增加 `archive`（downgrade 先查是否已有该 action 行）；
- [x] 模型同步（`__table_args__` 约束），`alembic check` 零差异；downgrade/upgrade 往返。

### Task 3: `crypto/archive_crypto.py` + 纯函数测试

- [x] 容器格式：`magic("STUDYOCR-ARCHIVE") ‖ version("v1") ‖ iterations(4B) ‖ salt(16) ‖ nonce(12) ‖ AESGCM(ct)`，
      AAD 绑 header；口令错误/篡改统一 `ArchiveOpenError("口令错误或归档包损坏")`；
- [x] `test_archive_crypto.py`：往返/错口令/1 bit 篡改/截断。

### Task 4: `services/archive_service.py` + schemas

- [x] `archive_report(db, user, report_id, confirm) -> ArchiveData`：admin（路由保证）→ `db.get` →
      `confirm` 否则 42202 → `FOR UPDATE` → `status='approved'` → 归档号（`nextval` + 业务年）→
      `transition(archived, bump_version=False)` + `version+1` → 审计 `archive` → commit；
- [x] `search_reports(db, user, query) -> PageResult[ReportOut]`：`filter_visible` + `status='archived'` +
      q/归档日/金额区间 + 排序白名单 + 分页；
- [x] `export_archive(db, user, report_id, passphrase) -> (bytes, filename)`：可见性 → 仅 archived →
      组装 4 个成员 → zip（内存）→ `seal_archive` → 返回密文包；
- [x] `ReportOut` 补 `archive_no`/`archive_at`（S5-1 既有输出向后兼容，默认 None）。

### Task 5: 路由

- [x] `check_roles`: admin router 下 `POST /admin/reports/{id}/archive`；
- [x] reports router：`GET ""`（submitter/reviewer；admin 403）与 `POST /{id}/export`（submitter/reviewer）；
      注意 `GET ""` 与 `POST ""` 同路径不同方法，`/{id}` 路由顺序不受影响；
- [x] 导出响应 `StreamingResponse`/`Response`，`application/octet-stream` + `Content-Disposition`。

### Task 6: 测试 `test_archive.py`（11 组）

- [x] 复用 approved 构造（submit→verify→approve）；spec §4.2 的 11 组全部落地（含并发归档、只读、
      检索权限/过滤、导出内容与"无影像明文"、脚本解开）。

### Task 7: 前端（本卡无页面）

- [x] 任务卡产出未要求前端；归档/检索/导出走接口与脚本（S5-7 演示脚本串起来）。不做 UI。

### Task 8: 真机冒烟 + 全量

- [x] `alembic upgrade head`（0009）+ `check`；全量单测/集成；
- [x] 真机：提交→验签→approve→archive→`GET /reports?status=archived`→`export`→
      `scripts/open_archive.py` 解出 ZIP（4 成员、清单 BOM、无影像）→归档后修改 409；清理业务行（审计保留）。

### Task 9: 收口

- [x] API/SCHEMA/PROGRESS/DECISIONS **D-089**/HANDOFF（下一步 S5-7）/AGENTS 锚点；
- [x] 提交 `feat(s5-6): 归档与检索（归档号 + 只读 + 口令加密归档包）`（push 由用户执行）。

## 风险与兜底

| 风险 | 兜底 |
|---|---|
| 归档不可逆误操作 | `confirm=true` 强校验（42202）；admin 专用；审计 `archive` 留痕 |
| 并发归档重号/双归档 | 序列 + UNIQUE + 行锁 |
| 归档后还能改 | 状态机已拦（409）；专门用例覆盖 加明细/签名/提交/决策 |
| ZIP 泄漏影像 | 成员固定 4 个、全部是元数据/签名/清单；测试断言无影像字节 |
| 新依赖 | 不引 pyzipper；用现有 cryptography 封装并在 D-089 写清"非 WinZip AES 兼容" |
| 归档号跨年语义 | 与 report_no 一致（全局递增不按年重置），论文写清 |