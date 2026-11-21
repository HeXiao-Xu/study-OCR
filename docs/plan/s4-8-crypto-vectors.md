# S4-8 密码模块单测与国标向量 — 实现计划（plan）

> 对应 spec：[`docs/spec/s4-8-crypto-vectors.md`](../spec/s4-8-crypto-vectors.md)｜
> 任务卡：实施计划第 7 章 S4-8｜状态：**已实现（2026-11-21）**，Task 0–5 全部执行并验证
> 范围：`backend/tests/test_crypto/`、`scripts/crypto_cross_check.py`、依赖与文档；不改业务代码。

## Global Constraints

- 向量必须注明出处（标准号 + 附录），**不冒充国标**（HMAC 用 OpenSSL 交叉向量）；
- 测试不依赖 Docker：跨实现走命令行脚本（S4-8 验收时手动跑并记录）；
- 新增测试期依赖 `pytest-cov` / `coverage` 要锁版本并记 DECISIONS；
- 覆盖率只算 `app/crypto`，阈值 ≥85%。

## 文件结构

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/tests/test_crypto/test_vectors.py` | 集中向量：SM3/SM4/SM2/HMAC/canonical，逐条注释出处 | 新 |
| `backend/tests/test_crypto/test_pipeline.py` | 端到端链路 + 7 类篡改矩阵 | 新 |
| `backend/tests/test_crypto/{test_certs,test_keystore}.py` | 补错误分支（提升覆盖率） | 改 |
| `scripts/crypto_cross_check.py` | OpenSSL 交叉验签（一次性命令） | 新 |
| `backend/requirements.txt` / `requirements.lock.txt` | `pytest-cov==7.1.0`、`coverage==7.17.4` | 改 |
| `docs/{spec,plan}/s4-8-crypto-vectors.md`、`docs/{PROGRESS,DECISIONS,HANDOFF}.md` | 收口 | 新/改 |

---

### Task 0: 探针（已执行）

- [x] 覆盖率基线：`pytest tests/test_crypto --cov=app/crypto` → **90%**（760 行缺 76）；
- [x] `pytest-cov` / `coverage` **未装** → 已 `pip install pytest-cov==7.1.0`（coverage 7.17.4）；
- [x] 各文件覆盖：certs 83% / keystore 88% / sm4 88% / sm2 90% / sm3 91% / envelope 93% / canonical 96%。

---

### Task 1: 锁依赖并回归

- [x] `requirements.txt` 测试段加 `pytest-cov==7.1.0`、`coverage==7.17.4`；lock 同步；
- [x] `pytest --version` 仍是 9.2.0（没被顶版本）；全量单测回归。

---

### Task 2: `test_vectors.py`

- [x] SM3：GB/T 32905 三条（abc / 64B / 空）；
- [x] SM4：GB/T 32907 单块；
- [x] SM2：GB/T 32918.5 A.1 固定 k 复现 r/s；
- [x] HMAC-SM3：4 组 OpenSSL 交叉向量（注明非国标）；
- [x] canonical：键序/中文/控制字符字节。

### Task 3: `test_pipeline.py`

- [x] 端到端：canonical → SM3 → 签名 → 信封 → 解封 → 验签 → 摘要比对；
- [x] 篡改矩阵 7 条（载荷/签名/密文/IV/MAC/wrapped_key/错密钥）；
- [x] 全部断言错误归类（`digest_mismatch` / `bad_signature` / `mac_mismatch`）。

### Task 4: 跨实现脚本

- [x] `scripts/crypto_cross_check.py`：临时目录生成 SM2 密钥 → 我们签名 → OpenSSL 验签 → 清理；
- [x] 真跑一次，记录 `openssl_verified: true`。

### Task 5: 覆盖率与收口

- [x] 跑 `--cov=app/crypto`，若 <85% 补错误分支（certs/keystore）；
- [x] 覆盖率数字与向量出处写进 PROGRESS；D-082（依赖）；AGENTS 锚点（测试向量）；
- [x] 全量单测 + 集成 + `alembic check`；提交 `feat(s4-8): 密码模块向量与端到端链路（覆盖率 XX%）`。

## 风险与兜底

| 风险 | 兜底 |
|---|---|
| pytest-cov 顶 pytest 版本 | 装完立刻 `pytest --version` + 全量回归 |
| 覆盖率被新测试拉低（新文件行数） | 新测试只调用既有封装；先测后看数字 |
| 跨实现脚本依赖 Docker | 只在验收手动跑，不进 pytest；失败即记录原因 |
| 向量记错 | 每条对照标准/已实测值（S4-1/S4-2 已用同值跑过） |