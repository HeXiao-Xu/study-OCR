# S4-1 SM3 摘要与 canonical JSON 规范化 — 实现计划（plan）

> 对应 spec：[`docs/spec/s4-1-canonical-sm3.md`](../spec/s4-1-canonical-sm3.md)（口径已定死）｜
> 任务卡：实施计划第 7 章 S4-1｜状态：**已实现（2026-11-20）**，Task 0–5 全部执行并验证
> 阶段 4 硬约束：只动 `backend/app/crypto/`、`backend/tests/test_crypto/`、根 `scripts/` 的验收脚本
> 与依赖/文档；**不碰任何业务接口、不加表、不加迁移**。

## Global Constraints

- 密码代码只走 `crypto/` 封装：业务（这里就是测试）**不得** import `gmssl`，
  唯一例外是 `crypto/sm/sm3.py` 自身；
- `gmssl.sm3.sm3_hash` 会**原地修改**入参 → 实现里必须传副本（`list(bytes)`）；
- 所有新增函数带完整类型标注；错误不用 `except: pass`；
- 密码测试**默认跑**（不打 `slow`/`integration` 标记），且必须含国标向量 + 篡改失败；
- 新增依赖 `gmssl==3.2.2` 必须同时进 `requirements.txt` 与 `requirements.lock.txt`，并记 DECISIONS；
- 提交 Conventional Commits（`feat(s4-1): ...`）；**push 由用户执行**。

## 文件结构

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/app/crypto/canonical.py` | `canonical_json` / `canonical_sha256` / `CanonicalJSONError` + 校验遍历 | 新 |
| `backend/app/crypto/sm/sm3.py` | `sm3_digest` / `sm3_hex`（gmssl 唯一入口） | 新 |
| `backend/tests/test_crypto/__init__.py` | 包标记 | 新 |
| `backend/tests/test_crypto/test_canonical.py` | 17 组用例（§5） | 新 |
| `backend/tests/test_crypto/test_sm3.py` | 国标向量 + 入参/类型/篡改 | 新 |
| `scripts/canonical_fixture.json` | 跨语言 fixture（中文/负数/嵌套/emoji 键/控制字符） | 新 |
| `scripts/canonical_cross_check.py` | Python 侧打印规范化字节 + 两种摘要 | 新 |
| `scripts/canonical_cross_check.mjs` | Node 侧独立实现（按码点排序） | 新 |
| `backend/requirements.txt`、`requirements.lock.txt` | `gmssl==3.2.2` | 改 |
| `docs/{spec,plan}/s4-1-*.md` | 本卡文档 | 新 |
| `docs/{PROGRESS,DECISIONS,HANDOFF}.md`、`AGENTS.md` §10.8 | 收口 | 改 |

---

### Task 0: 依赖落地（先装后锁，装完立刻回归）

- [x] **Step 1: 安装**（用户指令"开始 S4-1"即视为同意安装任务卡指定的 `gmssl==3.2.2`）

```bash
cd /e/BiSheXZY/study-OCR
.venv/Scripts/python.exe -m pip install gmssl==3.2.2
.venv/Scripts/python.exe -c "import gmssl, importlib.metadata as m; print(m.version('gmssl'))"
```

- [x] **Step 2: 接口体检**（确认纯 Python 包、`sm3_hash` 的入参与返回值、是否原地改）

```bash
.venv/Scripts/python.exe -c "from gmssl.sm3 import sm3_hash; d=list(b'abc'); print(sm3_hash(d)); print(d)"
```

- [x] **Step 3: 锁版本**：`requirements.txt` 新增 `# --- 国密算法（S4-1 起）---` 段；
  `requirements.lock.txt` 在 `gmssl` 字母序位置插入 `gmssl==3.2.2`（其余行不动）。

- [x] **Step 4: 回归**（防 numpy/paddle 被顶版本）

```bash
(cd backend && ../.venv/Scripts/python.exe -m pytest -q)   # 应仍全绿
.venv/Scripts/python.exe -c "import numpy, paddle; print(numpy.__version__, paddle.__version__)"
```

---

### Task 1: `test_canonical.py`（RED）

- [x] **Step 1**: 建 `backend/tests/test_crypto/__init__.py`（空）。
- [x] **Step 2**: 按 spec §5 表逐条写用例（17 组，多为参数化）。先只导入本卡要实现的符号：

```python
from app.crypto.canonical import CanonicalJSONError, canonical_json, canonical_sha256
```

- [x] **Step 3: 看它红**

```bash
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto/test_canonical.py -q)
# 期望：ModuleNotFoundError: No module named 'app.crypto.canonical'
```

---

### Task 2: `canonical.py`（GREEN）

- [x] **Step 1**: `_validate(obj)` 递归校验：str/int/bool/None/list/dict；
  int 超 `2**53-1` 拒；float/Decimal/其它对象拒；dict 键非 str 拒；循环引用用 `id()` 栈检测；
  str 用 `obj.encode("utf-8")` 探测孤立代理项（失败 → `CanonicalJSONError`）。
- [x] **Step 2**: `canonical_json` = 校验 → `json.dumps(...)`（`ensure_ascii=False, sort_keys=True,
  separators=(",", ":"), allow_nan=False`）→ `.encode("utf-8")`；把 `ValueError` 归一成
  `CanonicalJSONError`（循环引用、编码失败）。
- [x] **Step 3**: `canonical_sha256` = `hashlib.sha256(canonical_json(obj)).hexdigest()`。
- [x] **Step 4: 转绿**

```bash
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto/test_canonical.py -q)
```

---

### Task 3: `test_sm3.py`（RED）→ `sm3.py`（GREEN）

- [x] **Step 1**: 先写国标向量与行为用例（spec §5 第二张表）：
  `abc` / `b"abcd"*16` / `b""` 三条已知向量；返回 32 字节；1 bit 篡改摘要必变；
  `bytearray` 入参调用后**不被修改**；`str` 入参 `TypeError`；`sm3_hex` 与 `digest.hex()` 一致。
- [x] **Step 2: 看它红**

```bash
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto/test_sm3.py -q)
# 期望：ModuleNotFoundError: No module named 'app.crypto.sm.sm3'
```

- [x] **Step 3**: 实现 `sm3_digest` / `sm3_hex`（`list(bytes)` 副本、`bytes.fromhex`、长度守卫）。
- [x] **Step 4: 转绿**

```bash
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto/test_sm3.py -q)
```

---

### Task 4: 跨语言一致性（命令行脚本，不进 pytest）

- [x] **Step 1**: `scripts/canonical_fixture.json`：含中文值、负数字符串、嵌套 list、`null`、
  **emoji 键**（测 UTF-16 码元序 vs 码点序）、控制字符（`\n`/`\u0001`）。
- [x] **Step 2**: `scripts/canonical_cross_check.py`：读 fixture → 输出 `{"canonical_b64","sm3","sha256"}`。
- [x] **Step 3**: `scripts/canonical_cross_check.mjs`：Node 独立实现（码点排序 + JSON 转义），
  `createHash("sm3")` / `createHash("sha256")`；同样输出三字段。
- [x] **Step 4: 比对**

```bash
.venv/Scripts/python.exe scripts/canonical_cross_check.py
node scripts/canonical_cross_check.mjs
# 断言三字段逐字相等（记录到 PROGRESS）
```

---

### Task 5: 回归、文档收口与提交

- [x] **Step 1**: 全量验证

```bash
(cd backend && ../.venv/Scripts/python.exe -m pytest -q)                     # 默认跳过 integration
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto -q)   # 本卡
(cd backend && ../.venv/Scripts/python.exe -m alembic check)                 # 零差异（无迁移）
```

- [x] **Step 2**: `docs/PROGRESS.md`：勾选 S4-1、加"实际产出"行、追加遗留（如有）；
  `docs/DECISIONS.md`：**D-073**（gmssl 落地与不引 gmssl-python）、**D-074**（canonical 口径 Q1–Q11）；
  `AGENTS.md` §10.8 回填"密码封装"锚点（本卡先登记 canonical/sm3，其余待 S4-2~S4-6）；
  `docs/HANDOFF.md`：现状/下一步改 S4-2 + 收口本次实测数字。
- [x] **Step 3**: 提交

```bash
git add -A && git commit -m "feat(s4-1): canonical JSON 规范化与 SM3 摘要封装"
```

## 风险与兜底

| 风险 | 兜底 |
|---|---|
| `pip install gmssl` 顶版本（numpy/其它） | 装完立即 `pip freeze` 对照 + 全量 pytest；被顶就回滚并报告（不擅自改锁） |
| `sm3_hash` 原地改入参 | 只传 `list(data)` 副本；单测用 `bytearray` 断言"调用后未变" |
| Node 默认排序 ≠ 码点序 | fixture 放 emoji 键；mjs 显式按 `[...key]` 比码点 |
| 孤立代理项让 `encode` 抛原生异常 | 校验阶段先探测，抛 `CanonicalJSONError` 并带字段路径 |
| 跨语言字节不一致 | 以"同一 fixture 三字段相等"为闸门；不一致就改规范化实现而不是改 fixture |