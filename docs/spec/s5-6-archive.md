# S5-6 归档与检索 — 设计（spec）

> 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 第 8 章 S5-6。
> 契约：本卡**先补** `docs/API.md`（三条接口）与 `docs/SCHEMA.md` §5.1（两列 + 约束）——前五张卡都没这两个字段。
> 目标：`approved` 单据可**不可逆归档**、可按条件检索、可导出**口令加密、不含明文影像**的归档包。
> 状态：**已实现（2026-11-21）**，实测见 `PROGRESS.md` S5-6 行。

## 1. 范围

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/alembic/versions/0009_report_archive.py` | `report_forms.archive_no/archive_at` + `archive_no_seq` + CHECK + `audit_logs.action` 放开 `archive` | 新 |
| `backend/app/models/report_forms.py` | 两列 + 唯一约束 + 配对/状态 CHECK | 改 |
| `backend/app/models/enums.py` | `AuditAction.ARCHIVE` | 改 |
| `backend/app/crypto/archive_crypto.py` | 归档包口令加密（PBKDF2 + AES-256-GCM） | 新 |
| `backend/app/services/archive_service.py` | 归档、检索、导出归档包 | 新 |
| `backend/app/schemas/{report,archive}.py` | 归档/检索/导出出入参 | 改/新 |
| `backend/app/api/v1/admin.py` | `POST /admin/reports/{id}/archive` | 改 |
| `backend/app/api/v1/reports.py` | `GET /reports`（归档检索）、`POST /reports/{id}/export` | 改 |
| `scripts/open_archive.py` | 用口令解开归档包（生成 ZIP 并解压） | 新 |
| `backend/tests/{test_archive.py,test_archive_crypto.py}` | 集成 + 纯函数 | 新 |
| `docs/{API,SCHEMA,PROGRESS,DECISIONS,HANDOFF}.md`、`AGENTS.md` | 契约/进度/取舍/锚点 | 改 |

**不做**：前端归档页（任务卡产出未要求；归档是 admin 运维动作，走接口/脚本）；归档包内**不放影像明文**（硬约束）。

## 2. 决策

### 2.1 库：`report_forms` 补两列（SCHEMA 先改，迁移 0009）

| 列 | 类型 | 说明 |
|---|---|---|
| `archive_no` | `TEXT NULL` | 归档号 `AR-YYYY-NNNN`；**UNIQUE**（`uq_report_forms_archive_no`）；未归档为 NULL |
| `archive_at` | `TIMESTAMPTZ NULL` | 归档时间（UTC） |

约束（库兜底，不只靠应用层）：
- `ck_report_forms_archive_pair`：`(archive_no IS NULL) = (archive_at IS NULL)`；
- `ck_report_forms_archived_fields`：`status <> 'archived' OR archive_no IS NOT NULL`。

归档号用 **PG 序列 `archive_no_seq`**（迁移建，全局递增、年份取 `Asia/Shanghai` 业务年）：
`AR-YYYY-NNNN`。与 `report_no` 同一取舍（D-084）：只保证唯一、不保证按年重置/连续。

`audit_logs.action` 增加 **`archive`**（`enums.AuditAction` + SCHEMA §9.19b + 迁移 CHECK 三处同步）。

### 2.2 归档接口（admin）

`POST /api/v1/admin/reports/{id}/archive`，body `{confirm: true}`：
- 仅 `admin`（路由 `require_roles("admin")`）——这是**系统运维动作**，不改变 admin "业务表 404"的常规视图策略：
  归档走 `db.get` 直查，响应只给 `{report_id, report_no, status, archive_no, archive_at}`，不返回业务内容；
- `confirm != true` → **422/42202**（缺少高风险操作的二次确认，复用既有异常）；
- 状态必须 `approved`；已归档/其它状态 → 409；
- 单据行 `FOR UPDATE`：并发归档只有一个成功；
- 写审计 `action=archive`（`target_type=form`，detail 含 `report_no/archive_no`），与状态同事务；
- `report_forms.version + 1`；归档后**任何修改/决策入口**因状态机自然 409（S5-1~S5-5 已有闸门，测试覆盖）。

### 2.3 检索（submitter / reviewer）

`GET /api/v1/reports`：
- 权限：`submitter`（仅本人）、`reviewer`（全部）；`admin` → 403（角色级；归档管理走 §2.2）；
- 只列 `status='archived'`（`status` 参数只接受 `archived`，其它值 422——本接口就是归档检索）；
- 筛选：`q`（单号 / 申请人 username/real_name）、`date_from`/`date_to`（**归档日**，`Asia/Shanghai` 业务日）、
  `amount_min`/`amount_max`（`Decimal`，含端点）、分页复用 S2-4、排序白名单 `archive_at`/`total_amount`/`submitted_at`；
- 行级隔离走 `filter_visible`；出参 `ReportOut`（不含明细，沿用列表约定），新增 `archive_no`/`archive_at`（可空）。

### 2.4 导出归档包（submitter 本人 / reviewer）

`POST /api/v1/reports/{id}/export`，body `{passphrase: 8–128}`：
- 只允许 `status='archived'`（未归档 409）；submitter 仅本人、reviewer 全部；admin 403；
- 生成 **标准 ZIP**（内存中），再整体用 **PBKDF2(200k)+AES-256-GCM** 加密成 `.zip.enc` 容器；
- ZIP 内容（**没有影像明文、没有 MinIO 对象名、没有私钥**）：
  - `archive.json`：单据/归档号/申请人/金额/明细元数据/签名元数据/信封元数据；
  - `manifest.csv`：归档清单（UTF-8 BOM + CRLF：归档号/单号/申请人/金额/票据数/归档时间/验签结论）；
  - `canonical.json`：签名域原文字节（供离线复算）；
  - `signatures/signature.json`：签名记录 + 申请人证书 PEM（公开材料，供离线验签）。
- 返回 `application/octet-stream`，`Content-Disposition: attachment; filename="<report_no>-archive.zip.enc"`；
- 提供 `scripts/open_archive.py`：口令解密 → 写出 ZIP（可选 `--extract` 解压），作为验收工具。
- **为什么不用 pyzipper 做"标准 zip 口令"**：需要引入新依赖（AGENTS §2.1 必须先确认）；本期用现有
  `cryptography`（PBKDF2 + AES-GCM）封装，格式自描述、可脚本解开；论文写"归档包 = 口令加密容器 + 标准 ZIP"，
  并注明若要 WinZip AES 兼容可后续换 `pyzipper`（D-089）。

## 3. 接口契约（本卡新增，写进 API.md）

| 方法 | 路径 | 权限 | 说明 |
|---|---|---|---|
| POST | `/api/v1/admin/reports/{id}/archive` | admin | `{confirm:true}`；approved→archived；42202/409 |
| GET | `/api/v1/reports` | submitter/reviewer | 归档检索（q/日期/金额区间/排序/分页） |
| POST | `/api/v1/reports/{id}/export` | submitter 本人 / reviewer | 归档包（口令加密），仅 archived |

## 4. 测试

### 4.1 纯函数（`test_archive_crypto.py`，默认套件）

| 用例 | 断言 |
|---|---|
| 往返 | `open_archive(seal_archive(zip_bytes, pass)) == zip_bytes` |
| 错口令 | 抛 `ArchiveOpenError`（统一文案，不区分口令/损坏） |
| 1 bit 篡改 | 抛错（GCM tag 失败） |
| 截断/坏头 | 抛错 |

### 4.2 集成（`test_archive.py`，`-m integration`）

复用 `test_submit`/`test_decision` 装置，先构造 approved 单。

| # | 用例 | 断言 |
|---|---|---|
| 1 | 归档成功 | 200、`status=archived`、`archive_no` 匹配 `^AR-\d{4}-\d{4,}$`、`archive_at` 非空；审计 `archive`；`verify_chain ok` |
| 2 | 未确认 | `confirm=false` → 422/42202；状态不变 |
| 3 | 状态不允许 | 对 submitted/已归档单 → 409 |
| 4 | 权限 | reviewer/submitter → 403；未登录 401 |
| 5 | 归档号唯一/并发 | 两个线程归档两张 approved 单 → 两个不同归档号；库内 UNIQUE |
| 6 | 归档后只读 | 加明细/签名/提交/决策 → 全部 409；详情（submitter 本人 / reviewer）200 |
| 7 | 检索-本人 | submitter 只看到自己的归档单；`q`/金额区间/归档日过滤正确 |
| 8 | 检索-reviewer | reviewer 看到全部归档；admin → 403；非法 `status` → 422 |
| 9 | 导出 | 200、`application/octet-stream`、文件名含 `archive.zip.enc`；解密后是合法 ZIP，含 4 个成员；`manifest.csv` 有 BOM；**ZIP 内无影像字节**（不含原图明文，成员里没有 `files/`） |
| 10 | 导出权限/状态 | 非本人 submitter 404；未归档 409；admin 403；短口令 422 |
| 11 | 脚本 | `scripts/open_archive.py` 解密 + 列出成员（与后端同一实现） |

## 5. 验收

1. `pytest tests/test_archive.py tests/test_archive_crypto.py` 全绿；全量单测/集成不回归；`alembic check` 零差异；
2. `alembic upgrade head`（0009）、`downgrade 0008 && upgrade head` 往返；
3. 真机冒烟：提交→验签→approve→archive（归档号/审计）→检索命中→导出归档包→脚本解出 ZIP 且内容完整→
   归档后修改被 409；清理业务行（审计保留）；
4. 文档：API.md（三条接口 + 矩阵说明）、SCHEMA.md（两列 + 约束 + 枚举）、PROGRESS、DECISIONS **D-089**、
   HANDOFF（下一步 S5-7）、AGENTS 锚点。

## 6. 已知限制

- 归档**不可逆**（无取消归档接口）；需要强约束的场景只能靠备份；
- 归档号全局递增、不按年重置（同 D-084）；
- 归档包是"口令加密容器 + 标准 ZIP"，**不是 WinZip AES 兼容的 zip**（不引 pyzipper，可用脚本解开）；
- 归档操作仅 admin 接口，前端无归档页面（S5-7 演示脚本可覆盖）；
- 归档后影像明文仍只能走财务 `verify/decrypt` 的 30min 预览；归档包**刻意不放影像**；
- 检索只覆盖 archived；已审批/驳回的检索仍走财务待审/详情或后续补充视图。