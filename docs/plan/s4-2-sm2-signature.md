# S4-2 SM2 签名与验签 — 实现计划（plan）

> 对应 spec：[`docs/spec/s4-2-sm2-signature.md`](../spec/s4-2-sm2-signature.md)（含与任务卡 API 的取舍说明）｜
> 任务卡：实施计划第 7 章 S4-2｜状态：**已实现（2026-11-20）**，Task 0–4 全部执行并验证
> 阶段 4 硬约束：只动 `backend/app/crypto/`、`backend/tests/test_crypto/` 与文档；不碰业务接口、不加表/迁移。

## Global Constraints

- **签名策略唯一**：只走 `sign_with_sm3` / `verify_with_sm3`（e = SM3(ZA‖M)）；
  **绝不调用 `sign(data,K)` 直接签外部摘要**，也绝不在封装层再算一次 SM3；
- K 只来自 `secrets.token_hex(32)` + 拒绝采样；禁止 `gmssl.func.random_hex`（基于 `random`）；
- 签名值 = DER(SEQUENCE{r,s}) hex；记录里存 base64(DER)（对齐 SCHEMA "约 70–72 字节"）；
- 验签畸形输入返回 `False`，不抛；`r,s ∉ [1,n-1]` 直接拒绝（`s+n` 可塑性）；
- 测试默认跑；SM2 测试必须含 **GB/T 32918.5 A.1 公开向量**；
- 新依赖：无（`gmssl` / `pycryptodomex` 已在 S4-1 锁定）；无新环境变量。

## 文件结构

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/app/crypto/sm/sm2.py` | `sign_bytes` / `verify_bytes` / `DEFAULT_USER_ID` + 密钥归一化/曲线校验/DER 范围校验 | 新 |
| `backend/app/crypto/signature.py` | `SignatureRecord` / `VerifyResult` / `sign_canonical` / `verify_record` / `verify_canonical` | 新 |
| `backend/tests/test_crypto/test_sm2.py` | 14 组用例（国标向量/篡改/陷阱/缺陷回归） | 新 |
| `backend/tests/test_crypto/test_signature.py` | 10 组用例（记录字段/三类失败/模型校验） | 新 |
| `docs/{spec,plan}/s4-2-*.md` | 本卡文档 | 新 |
| `docs/{PROGRESS,DECISIONS,HANDOFF}.md`、`AGENTS.md` §10.8 | 收口 | 改 |

## 测试夹具（写进测试文件，附出处注释）

| 用途 | 数据 |
|---|---|
| 国标向量（GB/T 32918.5 A.1） | `dA=3945208F…4DF7C5B8`；`PA=(09F9DF31…56F35020, CCEA490C…2DA9AD13)`；`k=59276E27…EAC1BC21`；`M=b"message digest"`；期望 `r=F5A03B06…EEE720B3`、`s=B1B6AA29…85BBC1AA` |
| gmssl `lstrip("04")` 回归（G2） | `d=7F3A9C41E6B2D085A3F19C7E52B8D46A0C9E1F3B7D5A2C8E6F4B1D9A3C7E6146`；`PA` 以 `04` 开头（128 hex） |

> 两组都是**测试专用**密钥；生产密钥由 S4-5 的 OpenSSL 流程生成，绝不复用。

---

### Task 0: 环境与接口体检（已执行）

- [x] 读 `.venv/Lib/site-packages/gmssl/sm2.py`，确认 G1–G7 七条行为；
- [x] 用 `k=6CB28D99…` 的错误预期发现：GB/T 32918.2 旧示例是**自建曲线**，
      推荐曲线的公开向量是 **GB/T 32918.5 A.1**（`ZA=B2E14C5C…`、`e=F0B43E94…`、
      `k=59276E27…`、`r=F5A03B06…`、`s=B1B6AA29…`），gmssl 逐位复现（已实测）；
- [x] 用 `gmssl._kg` 一次性生成"PA 以 04 开头"的密钥对（只用于测试夹具）。

---

### Task 1: `test_sm2.py`（RED）→ `sm2.py`（GREEN）

- [x] **Step 1**: 写 14 组用例（spec §5 第一张表），先导入未实现的符号：

```python
from app.crypto.sm.sm2 import DEFAULT_USER_ID, sign_bytes, verify_bytes
```

- [x] **Step 2: 看它红**

```bash
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto/test_sm2.py -q)
# 期望：ModuleNotFoundError: No module named 'app.crypto.sm.sm2'
```

- [x] **Step 3**: 实现（要点）：

```python
_N = 0xFFFFFFFE...39D54123          # 曲线阶 n
_A = 0xFFFFFFFE...FFFFFC
_B = 0x28E9FA9E...4D940E93
_P = 0xFFFFFFFE...FFFFFFFF

def _normalize_public_key(hex_str):   # 130 hex 带 04 → 去掉一个 04；校验 128 hex + 曲线方程
def _validate_private_key(hex_str):   # 64 hex，1 <= d <= n-2
def _random_k_hex():                  # secrets.token_hex(32) + 拒绝采样
def _build(public_key_hex):           # CryptSM2(private_key="", public_key=pk, mode=1, asn1=True)
                                      # 构造后强制 sm2.public_key = normalized（绕开 lstrip("04")）
```

`sign_bytes`：校验 → 取 K（或固定 K）→ 最多 8 次重试 → 返回 DER hex；
`verify_bytes`：hex 检查 → `DerSequence` 解出 r/s → 范围校验 → `verify_with_sm3` → `bool`，
`(ValueError, TypeError, binascii.Error)` → `False`。

- [x] **Step 4: 转绿**

```bash
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto/test_sm2.py -q)
```

---

### Task 2: `test_signature.py`（RED）→ `signature.py`（GREEN）

- [x] **Step 1**: 写 10 组用例（spec §5 第二张表）。
- [x] **Step 2: 看它红**

```bash
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto/test_signature.py -q)
# 期望：ModuleNotFoundError: No module named 'app.crypto.signature'
```

- [x] **Step 3**: 实现 `SignatureRecord`（Pydantic v2，`frozen/extra=forbid`、`signed_at` 必须带时区、
  `payload_version` 非空）+ 三个函数；`VerifyResult` 用 `StrEnum`（`app.models.enums` 是 DB 枚举，
  这里不混用，放在 `signature.py` 内）。
- [x] **Step 4: 转绿**

```bash
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto -q)
```

---

### Task 3: 独立交叉验证

- [x] **Step 1**: 国标向量对账（不依赖 gmssl 的期望值来自标准文本，测试内已断言）；
- [x] **Step 2**: 用 Node 的 OpenSSL SM3 复算 `SM3(canonical_json({"a":1}))`，与 `sm3_digest` 对照：

```bash
node -e "const {createHash}=require('crypto');console.log(createHash('sm3').update(Buffer.from('{\"a\":1}','utf8')).digest('hex'))"
(cd backend && ../.venv/Scripts/python.exe -c "from app.crypto.canonical import canonical_json; from app.crypto.sm.sm3 import sm3_hex; print(sm3_hex(canonical_json({'a': 1})))")
# 两行必须相同
```

- [x] **Step 3**: 全量回归

```bash
(cd backend && ../.venv/Scripts/python.exe -m pytest -q)
(cd backend && ../.venv/Scripts/python.exe -m alembic check)
```

---

### Task 4: 文档收口与提交

- [x] **Step 1**: `PROGRESS` 勾选 S4-2 + 产出/验收行 + 遗留；`DECISIONS` **D-075**
  （签名策略裁决、DER 编码、gmssl 七条缺陷与处理、ZA/ID 口径）；`AGENTS` §10.8 锚点补 sm2/signature；
  `HANDOFF` 现状/命令/下一步（S4-3）；
- [x] **Step 2**: 提交 `feat(s4-2): SM2 签名与验签（国标 ZA 绑定、secrets 随机源）`；**push 由用户做**。

## 风险与兜底

| 风险 | 兜底 |
|---|---|
| Pydantic 版本差异（2.14）导致 `StrEnum` 序列化不同 | `VerifyResult` 继承 `enum.StrEnum`（3.11 原生），不用 pydantic 泛型 |
| DER 编码差异（70/71/72 字节） | 测试只断言"r/s 等于公开向量"，不断言总长度；长度只做 70–72 的软断言 |
| `k` 重试掩盖 bug | 固定 K 用例把"重试"路径隔离：固定 K 时失败直接抛，不静默换 K |
| 某天 gmssl 升级改行为 | S4-1 已锁 `gmssl==3.2.2`；本卡测试对 G1–G7 逐条回归，升级先炸 |