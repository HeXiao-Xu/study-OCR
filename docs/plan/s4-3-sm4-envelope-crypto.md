# S4-3 SM4-CBC + HMAC-SM3 Encrypt-then-MAC — 实现计划（plan）

> 对应 spec：[`docs/spec/s4-3-sm4-envelope-crypto.md`](../spec/s4-3-sm4-envelope-crypto.md)｜
> 任务卡：实施计划第 7 章 S4-3｜状态：**已实现（2026-11-21）**，Task 0–4 全部执行并验证
> 阶段 4 硬约束：只动 `backend/app/crypto/`、`backend/tests/test_crypto/` 与文档；不碰业务接口、不加表/迁移。

## Global Constraints

- **Encrypt-then-MAC**，MAC 覆盖 `aad ‖ IV ‖ 密文`；**解密前必须先验 MAC**，失败不产生明文；
- IV 只来自 `secrets.token_bytes(16)`；加密 key 与 MAC key 必须不同（`mac_key = SM3(key‖"mac")`）；
- SM4 分组运算只用 gmssl（`CryptSM4.set_key` + `one_round`）；CBC 链接与 PKCS7 由本层实现，
  因为 gmssl `crypt_cbc` 的 `pkcs7_unpadding` 不校验（spec Q3）；
- 无新依赖（`gmssl` / `pycryptodomex` 已在 S4-1 锁定）；无新环境变量；无迁移；
- 测试默认跑；SM4 必须含 **GB/T 32907-2016 向量**；HMAC-SM3 用 OpenSSL 交叉向量（注明出处）。

## 文件结构

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/app/crypto/sm/sm3.py` | 追加 `hmac_sm3`（RFC 2104，块长 64） | 改 |
| `backend/app/crypto/sm/sm4.py` | `IntegrityError` / `Sm4Ciphertext` / `derive_mac_key` / `encrypt` / `decrypt` + 内部 CBC/PKCS7/分组 | 新 |
| `backend/tests/test_crypto/test_sm3.py` | 追加 6 组 `hmac_sm3` 用例 | 改 |
| `backend/tests/test_crypto/test_sm4.py` | 国标向量 + 交叉向量 + 往返/篡改/结构错误 | 新 |
| `docs/{spec,plan}/s4-3-*.md` | 本卡文档 | 新 |
| `docs/{PROGRESS,DECISIONS,HANDOFF}.md`、`AGENTS.md` §10.8 | 收口 | 改 |

## 已知向量（探针已实测）

| 用途 | 输入 → 期望 |
|---|---|
| SM4 国标（GB/T 32907-2016 A.1） | `key=PT=0123456789abcdeffedcba9876543210` → `681edf34d206965e86b3e94f536e4246` |
| SM4-CBC（Node `sm4-cbc`，固定 IV） | `key=0123…3210`、`iv=000102…0e0f`、`"hello 国密 world"` → `61e48c1e…af5c` |
| HMAC-SM3 ① | `key="key"`、`data="The quick brown fox jumps over the lazy dog"` → `bd4a3407…ebd398` |
| HMAC-SM3 ② 长 key | `key="k"*100`、`data="data"` → `d2e31919…96fedfe8` |
| HMAC-SM3 ③ 空 | `key=""`、`data=""` → `0d23f72b…915261` |
| HMAC-SM3 ④ 业务形态 | `key="secret"`、`data="canonical-payload"` → `34be4603…dad064` |

---

### Task 0: 探针（已执行）

- [x] 读 `gmssl/sm4.py`：`crypt_cbc` 无条件 PKCS7；`func.pkcs7_unpadding = data[:-data[-1]]` 不校验；
- [x] 实测 `CryptSM4.set_key + one_round` 复现 SM4 国标向量；
- [x] 实测自写 CBC+PKCS7 与 Node `createCipheriv('sm4-cbc')` 逐字节相同；
- [x] 实测 Node `createHmac('sm3')` 可用，产出 4 组 HMAC 向量。

---

### Task 1: `hmac_sm3`（先红后绿）

- [x] **Step 1**: 在 `test_sm3.py` 追加 6 组用例（spec §5 第一张表）。
- [x] **Step 2: 看它红**

```bash
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto/test_sm3.py -q)
# 期望：ImportError: cannot import name 'hmac_sm3'
```

- [x] **Step 3**: 实现 `hmac_sm3`（RFC 2104；`_as_bytes` 复用；key>64B 先 `sm3_digest`）。
- [x] **Step 4: 转绿**

```bash
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto/test_sm3.py -q)
```

---

### Task 2: `sm4.py`（先红后绿）

- [x] **Step 1**: 写 `test_sm4.py`（spec §5 第二张表）。
- [x] **Step 2: 看它红**

```bash
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto/test_sm4.py -q)
# 期望：ModuleNotFoundError: No module named 'app.crypto.sm.sm4'
```

- [x] **Step 3**: 实现：

```python
def _encrypt_block(sm4: CryptSM4, block: bytes) -> bytes:   # 国标向量测这个
def _cbc_encrypt(key, iv, padded) -> bytes
def _cbc_decrypt(key, iv, ciphertext) -> bytes
def _pkcs7_pad(data) / _pkcs7_unpad(data)                   # 严格校验
def encrypt(key, plaintext, *, aad=b"") -> Sm4Ciphertext    # secrets IV + EtM
def decrypt(key, sealed, *, aad=b"") -> bytes               # 先验 MAC → 解密 → 严格去填充
```

- [x] **Step 4: 转绿**

```bash
(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto/test_sm4.py -q)
```

---

### Task 3: 独立交叉验证与全量回归

- [x] **Step 1**: Node 复算（固定 IV 的 CBC 向量 + 4 组 HMAC）：

```bash
node -e "const {createCipheriv,createHmac}=require('crypto');
const key=Buffer.from('0123456789abcdeffedcba9876543210','hex'), iv=Buffer.from('000102030405060708090a0b0c0d0e0f','hex');
const c=createCipheriv('sm4-cbc',key,iv); const ct=Buffer.concat([c.update(Buffer.from('hello 国密 world','utf8')),c.final()]);
console.log('sm4-cbc:', ct.toString('hex'));
console.log('hmac:', createHmac('sm3',Buffer.from('key')).update(Buffer.from('The quick brown fox jumps over the lazy dog')).digest('hex'));"
```

- [x] **Step 2**: 全量回归

```bash
(cd backend && ../.venv/Scripts/python.exe -m pytest -q)
(cd backend && ../.venv/Scripts/python.exe -m alembic check)
```

---

### Task 4: 文档收口与提交

- [x] **Step 1**: PROGRESS 勾选 + 产出/验收行 + 遗留；DECISIONS **D-076**（EtM 口径、自写 CBC/PKCS7 的理由、
  HMAC 交叉向量出处、错误分类）；AGENTS §10.8 锚点补 sm4；HANDOFF 现状/命令/下一步（S4-4）；
- [x] **Step 2**: 提交 `feat(s4-3): SM4-CBC + HMAC-SM3 Encrypt-then-MAC 对称层`；**push 由用户做**。

## 风险与兜底

| 风险 | 兜底 |
|---|---|
| 自写 CBC 出错 | 固定 IV 向量与 Node/OpenSSL 逐字节对照（Task 3） |
| PKCS7 校验过严把合法数据判坏 | 往返覆盖 0/1/15/16/17/100/64KB 长度 |
| MAC 比较非恒定时间 | 用 `hmac.compare_digest` |
| 坏填充用例无法构造（MAC 先拦） | 测试用 `derive_mac_key` 自己算合法 MAC，构造可控的坏填充密文 |
| 性能（纯 Python SM4） | 64KB 往返仅做正确性；p50/p95 留 S6-3 |