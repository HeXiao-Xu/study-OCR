# S4-8 密码模块单测与国标测试向量验证 — 设计（spec）

> 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 第 7 章 S4-8（★关键，阶段 4 收口）。
> 目标：把"自己签自己验"升级为**标准向量 + 跨实现 + 端到端**，并用覆盖率守住底线。
> 状态：**已实现（2026-11-21）**，实测见 `PROGRESS.md` S4-8 行。

## 1. 目标

1. **集中向量表**（`test_crypto/test_vectors.py`）：每条向量注明标准号 + 附录/出处；
2. **端到端链路**（`test_crypto/test_pipeline.py`）：
   `canonical_json → SM3 → SM2 签名 → SM4 信封 → 解封 → 验签`，含 **1 bit 篡改**矩阵；
3. **跨实现**：`scripts/crypto_cross_check.py` 用 OpenSSL 3.5（Docker，D-078）验证我们签的名；
4. **覆盖率**：`pytest tests/test_crypto --cov=app/crypto` 行覆盖 **≥85%**，数字与向量出处进 PROGRESS。

## 2. 向量与出处（写死进测试注释）

| 算法 | 向量 | 出处 |
|---|---|---|
| SM3 | `abc` → `66c7f0f4…4ba8e0`；`abcd`×16 → `debe9ff9…0c5732`；空串 → `1ab21d83…aa2b` | **GB/T 32905-2016** 附录 A 示例（SM3 密码杂凑算法） |
| SM4 | key=PT=`0123456789abcdeffedcba9876543210` → CT=`681edf34d206965e86b3e94f536e4246` | **GB/T 32907-2016** 附录 A.1（SM4 分组密码算法，单块） |
| SM2 签名 | `dA=3945208F…C5B8`、`k=59276E27…BC21`、`M="message digest"` → `r=F5A03B06…20B3`、`s=B1B6AA29…C1AA` | **GB/T 32918.5-2017** 附录 A.1（推荐曲线 sm2p256v1） |
| HMAC-SM3 | `key="key"`、data=… → `bd4a3407…ebd398` 等 4 组 | **无国标向量**（HMAC 非 GB/T 定义内容）；OpenSSL 3.5 交叉计算（Node `createHmac('sm3')`），D-076 |
| canonical JSON | `{"b":1,"a":2}` → `b'{"a":2,"b":1}'`、中文原样、控制字符 `\u0001` | 本项目口径（D-074）；与 Node 侧按码点排序实现逐字对照 |

> 原则：**不冒充国标**。没有国标向量的（HMAC）明确写"OpenSSL 交叉向量"。

## 3. 端到端与篡改矩阵（`test_pipeline.py`）

```python
payload  = {"report_no": "...", "items": [...], "total_amount": "1068.00"}
canonical = canonical_json(payload)                 # S4-1
digest    = sm3_digest(canonical)                   # S4-1
record    = sign_canonical(payload, 签名私钥)        # S4-2（内部 canonical + SM2-SM3）
envelope  = seal(canonical, 收方公钥=另一对密钥)     # S4-4（SM4-CBC EtM + SM2 包 CEK）
plain     = open_sealed(envelope, 收方私钥)          # S4-4
assert plain == canonical
assert verify_canonical(payload, record, 签名公钥) is OK   # S4-2
assert sm3_digest(plain) == digest
```

篡改矩阵（每条都必须失败且**错误归类正确**）：

| 篡改点 | 期望 |
|---|---|
| 载荷改 1 个字符 | `verify_canonical` → `digest_mismatch` |
| 签名 DER 改 1 bit | `verify_record` → `bad_signature` |
| 信封密文改 1 bit | `open_sealed` → `EnvelopeError("mac_mismatch")` |
| 信封 IV / MAC 改 1 bit | `mac_mismatch` |
| `wrapped_key` 的 C2/C3 段改 1 bit | `mac_mismatch` |
| 用错收方私钥 | `mac_mismatch` / `bad_envelope` |
| 用错签名公钥 | `bad_signature` |

## 4. 跨实现脚本

`scripts/crypto_cross_check.py`（一次性命令，不进 pytest 以避免测试依赖 Docker）：

1. 用 `scripts/openssl.sh`（Docker 内 OpenSSL 3.5）生成一对 SM2 密钥到仓库临时目录；
2. 读 PKCS#8 → `d`，用 **我们的 `sign_bytes`** 对固定消息签名（DER）；
3. 用 `openssl pkeyutl -verify -rawin -digest sm3 -pkeyopt distid:1234567812345678` 验证；
4. 打印 `{"ok": true, "openssl_verified": true, ...}`，清理临时目录（私钥不留盘）。

> 必须显式 `distid`（D-079/遗留 #88）：OpenSSL 默认 SM2 ID 为空，不传会验不过。

## 5. 覆盖率

- 命令：`(cd backend && ../.venv/Scripts/python.exe -m pytest tests/test_crypto --cov=app/crypto --cov-report=term-missing)`
- 基线（S4-7 结束）：**90%**（760 行，缺 76）；本卡新增向量/端到端与若干错误分支后要求 **≥85%**（实际值写 PROGRESS）；
- 只覆盖 `app/crypto`（密码层），不把 `services/` 算进来（那由各自测试覆盖）。

## 6. 新增依赖

`pytest-cov==7.1.0` + `coverage==7.17.4`（仅测试期；任务卡的验收命令直接用它）。
按 AGENTS §2.1 记录 D-082。装完回归全量单测（确认没动 pytest 版本）。

## 7. 验收

1. `pytest tests/test_crypto -q` 全绿（220 + 新增）；
2. `pytest tests/test_crypto --cov=app/crypto` **≥85%**（贴 TOTAL 行）；
3. `scripts/crypto_cross_check.py` → `openssl_verified: true`；
4. 全量单测/集成无回归；`alembic check` 零差异；
5. 向量出处进测试注释与 PROGRESS；论文可直接引用。

## 8. 已知限制

- 只做"固定向量 + 端到端 + 1 bit 篡改"，**不做 fuzz/属性测试**（时间与范围）；S6-4 再补安全用例；
- 覆盖率是行覆盖，不衡量分支/路径；也不覆盖 `tests/` 自身；
- 跨实现只验"我们签 → OpenSSL 验"，反向（OpenSSL 签 → 我们验）在 S4-2 已用 gmssl 做过；
- 性能不在本卡（S6-3）。