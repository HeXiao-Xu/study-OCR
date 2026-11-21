# PROGRESS.md — 项目进度

> 唯一进度依据。每个任务完成后更新：勾选任务、记录实际产出文件、登记遗留问题。
> 任务编号出自 `docs/OCR票据识别与加密系统-AI协作开发实施计划.md`（S0-1 ~ S7-5，共 59 张卡）。
> **开新会话请先读 [`docs/HANDOFF.md`](HANDOFF.md)**：环境、复现命令、指标、关键取舍、下一步都在那里。

## 当前状态

- 阶段：**阶段 4 进行中**（S4-1 ~ S4-8 已完成，密码链路 + 审计链 + 覆盖率收口）；下一张卡 **S4-9 RSA-OAEP+PSS 与 AES-GCM 对照实现**
- 已完成：S0-1 ~ S0-7、S1-1 ~ S1-6、S2-1 ~ S2-12、S3-1 ~ S3-9、**S4-1 ~ S4-8**（共 42 张卡，全部 ✅ 通过）
- 最近完成：**S4-8 密码向量收口**（集中国标向量 + 端到端链路 + `app/crypto` 覆盖率 **94%**；OpenSSL 交叉验签通过）
- 附加改动（2026-11-20）：D-069 真实横版票抽取修复、D-070 确认流程简化（去重识别/逐字段操作）、D-071 删除改硬删（仅 draft，可重传）；业务状态统一显示为 待确认/待复核/已复核（前端派生）
- 下一步：**S4-9 RSA-OAEP+PSS 与 AES-GCM 对照实现**
- 测试期新增依赖：`pytest-cov==7.1.0` + `coverage==7.17.4`（仅测试，已锁）
- 已知阻塞：无
- 环境提醒：
  - 后端必须用 **Python 3.11**（`.venv/Scripts/python.exe`），本机默认 `python` 是 3.13，`paddlepaddle` 无对应 wheel
  - `study-ocr-{postgres,redis,minio}` 三个容器已跑在 5433 / 6380 / 9002
  - `gmssl==3.2.2` + `cryptography==50.0.2`（+ `pycryptodomex==3.24.2`）已装并锁版本；numpy 1.26.4 / paddle 2.6.2 未变
  - **本机没有 openssl CLI**：统一用 `scripts/openssl.sh` / `scripts/openssl.ps1`（Docker 内 OpenSSL 3.5.9，D-078）
  - **新增必填配置 `AUDIT_HMAC_KEY`**（≥32 字符，D-081）；`backend/.env` 已本地生成，`.env.example` 已加占位符
  - 本地领先 origin **79 个 commit**，`git push` 由用户执行

## 任务清单

```
## 阶段 0：项目启动（M0 前半）
[x] S0-1 创建仓库骨架与 .gitignore
[x] S0-2 写项目宪法 AGENTS.md
[x] S0-3 写契约文档 SCHEMA.md + API.md ★关键
[x] S0-4 起基础服务（PostgreSQL 16 + Redis 7 + MinIO）
[x] S0-5 后端最小可运行：config + 日志/trace_id + 统一异常
[x] S0-6 前端最小可运行 + axios 封装 + 路由守卫
[x] S0-7 冒烟脚本与 README 完整化（tag m0-env-ready）

## 阶段 1：OCR 技术验证（M0 后半）
[x] S1-1 票据样本集、标注规范与合成票据脚本 ★关键
[x] S1-2 图像预处理与透视校正 ★关键
[x] S1-3 PaddleOCR 检测识别最小闭环 ★关键
[x] S1-4 票种识别与版面区域划分
[x] S1-5 关键字锚定 + 规则字段抽取 + 校验器 ★关键
[x] S1-6 端到端基线脚本与指标计算 ★关键

## 阶段 2：基础框架（M1）
[x] S2-1 模型与首批迁移（16 张表）
[x] S2-2 JWT 认证 + RBAC 三角色 ★关键
[x] S2-3 行级数据隔离
[x] S2-4 分页、筛选与排序
[x] S2-5 票据表与 CRUD（invoices / invoice_files）
[x] S2-6 文件上传 + MinIO + sha256 去重 ★关键
[x] S2-7 ARQ 异步任务框架（状态机 + 重试 + 幂等）
[x] S2-8 票种模板与规则管理
[x] S2-9 前端登录与主框架
[x] S2-10 前端上传页
[x] S2-11 前端票据列表与详情
[x] S2-12 定时任务骨架与阶段收口

## 阶段 3：识别流水线上线（M2）
[x] S3-1 OCR Worker 串起全链路 ★关键
[x] S3-2 字段级置信度聚合与低置信度确认队列 ★关键
[x] S3-3 局部二次识别（crop 放大重识别）
[x] S3-4 识别结果 JSON Schema 与落库
[x] S3-5 票据详情页：图像与字段并排 + 框选高亮 ★关键
[x] S3-6 字段修正与确认 API
[x] S3-7 重复报销检测
[x] S3-8 批量上传与进度
[x] S3-9 双人复核（补充卡：提交人全字段 + 审核人二审）

## 阶段 4：密码模块（M3）
[x] S4-1 SM3 摘要与 canonical JSON 规范化 ★关键
[x] S4-2 SM2 签名与验签（防双重哈希） ★关键
[x] S4-3 SM4-CBC + HMAC-SM3 的 Encrypt-then-MAC 对称层 ★关键
[x] S4-4 数字信封（SM2 包裹会话密钥） ★关键
[x] S4-5 密钥生成与私钥口令保护 ★关键
[x] S4-6 X.509 自建 CA 与角色证书
[x] S4-7 审计日志哈希链 ★关键
[x] S4-8 密码模块单测与国标测试向量验证 ★关键
[ ] S4-9 RSA-OAEP+PSS 与 AES-GCM 对照实现

## 阶段 5：报销业务闭环（M4）
[ ] S5-1 报销单创建与明细
[ ] S5-2 签名 → 加密提交链路 ★关键
[ ] S5-3 财务端待审列表
[ ] S5-4 验签与解密 ★关键
[ ] S5-5 审核决策与驳回
[ ] S5-6 归档与检索
[ ] S5-7 端到端联调与异常场景

## 阶段 6：实验与评测（M5）
[ ] S6-1 识别对比实验（CRNN vs SVTR + 预处理消融）
[ ] S6-2 字段抽取对比（纯正则 vs 正则+语义匹配）
[ ] S6-3 密码性能实验 ★关键
[ ] S6-4 安全测试用例
[ ] S6-5 评测报告导出

## 阶段 7：部署、调优与论文支撑（M6）
[ ] S7-1 性能压测与 CPU 推理优化
[ ] S7-2 Docker 一键部署 + 备份
[ ] S7-3 冷启动检查与临时明文零残留扫描
[ ] S7-4 论文图表数据导出
[ ] S7-5 演示环境与录屏脚本
```

## 已完成任务的实际产出

| 任务 | 产出文件 | 验收方式 | 结论 |
|---|---|---|---|
| S4-8 | `backend/tests/test_crypto/test_vectors.py`（集中向量：SM3 GB/T 32905、SM4 GB/T 32907、SM2 GB/T 32918.5 A.1、HMAC-SM3 OpenSSL 交叉向量、canonical 字节向量）、`backend/tests/test_crypto/test_pipeline.py`（端到端 + 7 类 1 bit 篡改）、`test_certs.py` / `test_keystore.py`（补错误分支）、`scripts/crypto_cross_check.py`（我们签名 → OpenSSL 验签）、`backend/requirements{,.lock}.txt`（`pytest-cov==7.1.0` + `coverage==7.17.4`）、`docs/{spec,plan}/s4-8-crypto-vectors.md` | ① 向量全部注明出处（GB/T 32905-2016 附录 A、GB/T 32907-2016 附录 A.1、GB/T 32918.5-2017 附录 A.1；HMAC 明确标"OpenSSL 交叉向量，非国标"）；② 端到端 `canonical → SM3 → SM2 签名 → SM4 信封 → 解封 → 验签 → 摘要比对` 通过；③ 篡改矩阵：载荷 → `digest_mismatch`、签名 1 bit/错公钥 → `bad_signature`、密文/IV/MAC/wrapped_key(C2/C3)/错收方 → `mac_mismatch`、`payload_sha256` → `digest_mismatch`；④ **OpenSSL 3.5 交叉验签**：`scripts/crypto_cross_check.py` → `{"openssl_verified": true, "openssl_output": "Signature Verified Successfully"}`（显式 `distid:1234567812345678`）；⑤ **覆盖率**：`pytest tests/test_crypto --cov=app/crypto` → **TOTAL 94%**（760 行缺 43；certs 98%、keystore 99%、signature 98%、canonical 96%、envelope 93%、sm3 91%、sm2 90%、sm4 88%），≥85% 达标；⑥ `pytest tests/test_crypto` **256 passed**、全量单测 **703 passed**、集成 **259 passed**、`alembic check` 零差异 | ✅ 通过（新增测试期依赖 D-082；向量出处可直引论文） |
| S4-7 | `backend/app/services/audit.py`（新：`GENESIS_HASH` / `ADVISORY_LOCK_KEY=8071701` / `ChainVerification` / `payload_sha256` / `chain_hash` / `append_audit`（事务内 `pg_advisory_xact_lock` 后取链尾）/ `verify_chain`（按 id 流式复算，返回首个断点）/ `list_audit_logs`）、`backend/app/schemas/audit.py`（`AuditLogOut` / `AuditListQuery` / `AuditVerifyData`）、`backend/app/api/v1/admin.py` + `main.py`（`GET /admin/audit`、`GET /admin/audit/verify-chain`，admin 专用）、`core/config.py` + `.env.example`（新增必填 `AUDIT_HMAC_KEY` ≥32，prod 拒绝示例值）、`tasks/ocr_task.py`（`anchor_audit_task` 从占位改为每日真实复算）、`scripts/verify_chain.py`（断开退出码 1）、`backend/tests/{test_audit.py,test_audit_chain.py}`（18 单测 + 11 集成）、`docs/{spec,plan}/s4-7-audit-chain.md` | ① 3 条链：首行 `prev_hash=0×64`、逐行 prev 指向上一行 `entry_hash`；`verify ok=True, checked_count=3`；② 改中间行 `detail_json` → `ok=False, broken_at=2, broken_id=第二行, reason=entry_hash_mismatch`；③ 改 entry_hash → 断在该行（broken_at=1）；④ 删中间行 → 下一行 prev 对不上（broken_at=2, prev_hash_mismatch）；⑤ **并发 50 条**（10 线程各自事务）→ 全部成功、`verify ok`、`count(distinct prev_hash)=50`（不分叉）；⑥ 回滚不产生审计行；⑦ HTTP：admin 列表/校验 200、reviewer/submitter **403/40301**、`action=not_an_action`/`from>to`/`page=0` → **422**；⑧ `scripts/verify_chain.py` 真库输出 `{"ok": true, "checked_count": 0, ...}` 退出码 0；⑨ 单测 667 passed（646+21）、集成 259 passed（248+11）、`alembic check` 零差异（无迁移） | ✅ 通过（D-081：哈希载荷超集、payload 不落列、业务审计行留 S5） |
| S4-6 | `backend/app/crypto/certs.py`（新：`CertStatus{ok/cert_expired/cert_not_yet_valid/cert_revoked/cert_public_key_mismatch/cert_role_mismatch}`、`CertificateInfo`、`parse_certificate`、`check_certificate`；`cryptography` 解析证书/扩展/有效期/指纹，**公钥从 DER 的 SPKI BIT STRING 取**（cryptography 不支持 SM2 公钥）；角色取自 OU）、`scripts/ca_init.sh`（`init`/`issue`/`show`/`verify`；签发时解锁私钥**经 stdin** 做 CSR，私钥不落盘）、`scripts/unlock_key.py`（`.p8.enc` → PKCS#8 PEM 仅走 stdout）、`scripts/_openssl_docker.sh`（共享 docker 封装，`openssl.sh` 瘦身复用）、`backend/tests/test_crypto/fixtures/{ca,alice,bob}.crt.pem` + 公钥（公开夹具）、`backend/tests/test_crypto/test_certs.py`（15 条）、`docs/{spec,plan}/s4-6-certs.md` | ① 夹具由真 OpenSSL 3.5 生成：`openssl x509 -text` 可见 **KeyUsage(Digital Signature, Non Repudiation, Key Encipherment)**、**EKU=clientAuth**、SKI/AKI，签名算法 **sm2sign-with-SM3（1.2.156.10197.1.501）**；② `openssl verify -CAfile ca.crt alice.crt` → **OK**；③ 运行期 `parse_certificate` 指纹与 `openssl -fingerprint -sha256` 逐字一致（`9636254f…`）；公钥提取与 `sign.pub.pem` 一致；④ `check_certificate`：正常 `ok`、`now=not_after+1s` `cert_expired`、`now=not_before-1s` `cert_not_yet_valid`、`revoked=True` `cert_revoked`（优先级最高）、错公钥 `cert_public_key_mismatch`、错角色 `cert_role_mismatch`；⑤ 真跑 `ca_init.sh init` + `issue alice submitter`：证书签发成功、**公钥一致性自检通过**、`secrets/certs/` 只有 `alice.crt.pem`、无临时密钥文件；⑥ `pytest tests/test_crypto` **220 passed**（205+15），全量 **646 passed**（631+15），`alembic check` 零差异 | ✅ 通过（D-080：不做链验签的边界、stdin 签发、OU 取角色；证书落库属 S5） |
| S4-5 | `backend/app/crypto/keystore.py`（新：`EncryptedPrivateKey`（frozen/extra=forbid、iterations≥200000）、`KeyPair`、`KeystoreError`、`derive_kek`、`key_fingerprint`、`private_key_hex_from_pkcs8`（DER 原语解析）、`wrap_private_key`/`unwrap_private_key`/`load_keypair`；PBKDF2-HMAC-SHA256+AES-256-GCM，AAD 绑定元数据；解锁失败统一"口令错误"）、`backend/app/crypto/sm/sm2.py`（追加 `public_key_from_private`）、`scripts/keygen.py`（OpenSSL 生成 → 加锁 → 解锁 → 签名自检；临时 PEM 即删；口令只走 env/getpass）、`backend/tests/test_crypto/test_keystore.py`（22 条）+ `test_sm2.py`（+2）、`docs/{spec,plan}/s4-5-keystore.md` | ① 真 OpenSSL 3.5 生成：`genpkey -algorithm SM2` → PKCS#8，`pkey -pubout` 与 `dG` 推导**逐字节一致**；② `keygen.py` 全链路：产物只有 `sign.p8.enc`（498B JSON 密文）与 `sign.pub.pem`，**无临时 PEM 残留**；自检"解锁+签名+验签"通过；③ **OpenSSL 独立验签**：`pkeyutl -verify -rawin -digest sm3 -pkeyopt distid:1234567812345678` → `Signature Verified Successfully`（不传 distid 失败，见遗留 #88）；④ 错误口令 → `KeystoreError("口令错误")`；篡改密文/盐/nonce/iterations（AAD）→ 同一错误；空/7 字符口令 → `ValueError`；⑤ PKCS#8 解析拒绝非法 DER/31B 标量/越界标量；⑥ 模型 frozen/extra=forbid/iterations 下限/key_fp 格式；⑦ `key_fp = SHA-256(0x04‖X‖Y)`，128/130 hex 同指纹；⑧ `pytest tests/test_crypto` **205 passed**（181+24），全量 **631 passed**（607+24），`alembic check` 零差异（无迁移）；⑨ `backend/` 无任何真实私钥材料（`BEGIN PRIVATE KEY` 仅出现在文档说明；`secrets/` 未入库） | ✅ 通过（D-079：密钥生成/口令保护口径；`key_records` 落库属 S5） |
| S4-4 | `backend/app/crypto/sm/sm2.py`（追加 `encrypt_bytes/decrypt_bytes`：C1C3C2、C1 无 04 前缀、只收 1–32B、K 用 `secrets`、SM3 计数器 KDF）、`backend/app/crypto/envelope.py`（新：`Envelope` / `EnvelopeError` / `seal` / `open_sealed`；CEK 每封信封新生成；MAC 覆盖 `header‖wrapped_key‖IV‖密文‖证书指纹`；顺序 解 CEK→验 MAC 解密→sha256）、`backend/app/crypto/sm/sm3.py`（改：OpenSSL `hashlib` 快路径 + gmssl 回退，`hmac_sm3` 两条路径）、`backend/app/crypto/sm/sm4.py`（改：`cryptography`/OpenSSL 后端 + `trailer` 参数）、`backend/requirements.txt`（`cryptography==50.0.2`）、`backend/tests/test_crypto/{test_sm3,test_sm4,test_sm2,test_envelope}.py`（+35 条）、`docs/{spec,plan}/s4-4-envelope.md` | ① **2MB seal+open 实测 0.095s**（seal 0.054s / open 0.041s，任务卡要求 <1s；254KB 真实票据尺寸 22.4ms）；② 信封往返：空/1B/1KB/2MB、中文 JSON；`wrapped_key` 112B（C1 64+C3 32+C2 16）；③ 同明文两次：iv/密文/MAC/wrapped_key 全不同（新 CEK）；④ 篡改 ciphertext/iv/mac → `mac_mismatch`；篡改 wrapped_key 的 **C2 段与 C3 段** → `mac_mismatch`（C3 段靠"wrapped_key 进 MAC"发现）；篡改 cert_fp → `mac_mismatch`；篡改 payload_sha256 → `digest_mismatch`；错私钥 → `mac_mismatch/bad_envelope`；坏 base64/长度 → `bad_envelope`；⑤ SM2 加解密：1/16/32B 往返、>32B `ValueError`、同明文两次密文不同、C1 不在曲线 `ValueError`、**与 gmssl `encrypt/decrypt` 双向互操作**；⑥ SM3 双后端：monkeypatch 回退到 gmssl 后国标向量与 HMAC 向量仍一致；SM4 与 gmssl 参考实现逐字节一致；⑦ `pytest tests/test_crypto` **181 passed**（146 + 35），全量 **607 passed**（572 + 35），`alembic check` 零差异 | ✅ 通过（D-077：OpenSSL 后端、cryptography 直接依赖、SM2 加密自组合、C3/MAC 覆盖取舍） |
| S4-3 | `backend/app/crypto/sm/sm3.py`（追加 `hmac_sm3`：RFC 2104、块长 64、长 key 先哈希；返回 32B）、`backend/app/crypto/sm/sm4.py`（新：`IntegrityError(reason)` / `Sm4Ciphertext` / `derive_mac_key` / `encrypt` / `decrypt` + 内部 CBC/PKCS7/分组；IV 用 `secrets`；MAC 覆盖 `aad‖IV‖密文`；解密前先验 MAC + `compare_digest`）、`backend/tests/test_crypto/{test_sm3,test_sm4}.py`（+41 条）、`docs/{spec,plan}/s4-3-sm4-envelope-crypto.md` | ① **GB/T 32907-2016 A.1 国标向量**：key=PT=`0123456789abcdeffedcba9876543210` → `681edf34d206965e86b3e94f536e4246`；② **OpenSSL 交叉**：固定 IV 的 CBC 向量与 Node `createCipheriv('sm4-cbc')` 逐字节相同（`61e48c1e…af5c`）；HMAC-SM3 4 组向量与 Node `createHmac('sm3')` 相同（`bd4a3407…ebd398` 等）；③ 往返 0/1/15/16/17/31/32/100/1000 B 与中文载荷；空明文可往返；④ 同明文两次 IV/密文/MAC 全不同；⑤ 篡改密文 1 bit / 改 IV / 改 MAC / 错误 key / 截断分组 → `IntegrityError("mac_mismatch")`；⑥ **坏填充**（MAC 合法、末字节 0x00/0x05/0x11）→ `IntegrityError("bad_padding")`；坏填充+坏 MAC → 先报 `mac_mismatch`（证明"先验 MAC 再解密"）；⑦ aad 绑定：同 aad 通过、换 aad 失败；⑧ key≠16B / iv≠16B / mac≠32B → `ValueError`；异常信息不含明文；⑨ `pytest tests/test_crypto` **146 passed**（S4-1/2 105 + 本卡 41），全量 **572 passed**（531 + 41），`alembic check` 零差异（无迁移） | ✅ 通过（D-076：EtM 口径、自写 CBC/PKCS7 的理由、HMAC 交叉向量出处） |
| S4-2 | `backend/app/crypto/sm/sm2.py`（新：`sign_bytes` / `verify_bytes` / `DEFAULT_USER_ID` / `SM2_N`；只走 `sign_with_sm3`，K 用 `secrets` + 拒绝采样；DER 签名；公钥归一化 + 曲线校验 + 覆盖 gmssl `lstrip("04")` 缺陷；验签 r,s 范围校验 + 畸形返回 False）、`backend/app/crypto/signature.py`（新：`SignatureRecord`（frozen/extra=forbid）、`VerifyResult`、`sign_canonical` / `verify_record` / `verify_canonical`）、`backend/tests/test_crypto/{test_sm2,test_signature,_keys}.py`（51 条）、`docs/{spec,plan}/s4-2-sm2-signature.md` | ① **GB/T 32918.5 附录 A.1 公开向量**逐位对上：`M="message digest"`、`k=59276E27…` → `r=F5A03B06…20B3`、`s=B1B6AA29…C1AA`，验签 True；② ZA/摘要与标准一致（`ZA=B2E14C5C…`、`e=F0B43E94…`，实测）；③ 1 bit 改消息 / 改签名 / 错误公钥 → False；④ 同明文两次签名不同（`secrets`），固定 K 两次相同；⑤ **双重哈希陷阱**：把 `sm3_digest(payload)` 当消息再签 → 对摘要 True、对原 payload False；⑥ **gmssl 缺陷回归**：x 以 `04` 开头的公钥（`d=7F3A…6146`）签名/验签正确（未修 bug 时 gmssl 把 128 hex 剥成 126 并抛 `TypeError`，已实测）；⑦ **可塑性**：`s+n` 的 DER 变体 → False（gmssl 本身会验过）；⑧ 畸形签名 6 种 → False 不抛；非法私钥/公钥/K/非曲线点 → `ValueError`；⑨ 记录三类失败：载荷改 `digest_mismatch`、载荷+digest 同改 `bad_signature`、签名改/错公钥 `bad_signature`；模型 frozen/extra=forbid/naive 时间拒绝；⑩ `pytest tests/test_crypto` **105 passed**（S4-1 54 + 本卡 51），全量 **531 passed**（480 + 51），`alembic check` 零差异（本卡无迁移）；⑪ 独立实现对照：Node `createHash('sm3')` 与 `sm3_hex(canonical_json(...))` 同值 `d646a76e…` | ✅ 通过（D-075：签名策略裁决 + gmssl 七条缺陷处理；`hmac_sm3` 仍留 S4-3） |
| S4-1 | `backend/app/crypto/canonical.py`（新：`canonical_json` 校验遍历 + 标准库转义、`canonical_sha256`、`CanonicalJSONError`；拒 float/Decimal/非 str 键/超 2^53 整数/循环/代理项）、`backend/app/crypto/sm/sm3.py`（新：`sm3_digest` 32B、`sm3_hex` 仅日志；gmssl 唯一入口 + `list()` 副本挡原地修改）、`backend/tests/test_crypto/{__init__,test_canonical,test_sm3}.py`（54 条）、`scripts/{canonical_fixture.json,canonical_cross_check.py,canonical_cross_check.mjs}`、`backend/requirements{,.lock}.txt`（`gmssl==3.2.2` + `pycryptodomex==3.24.2`）、`docs/{spec,plan}/s4-1-canonical-sm3.md` | ① SM3 国标向量全过：`abc`→`66c7f0f4…`、`b"abcd"*16`→`debe9ff9…`、空串→`1ab21d83…`；② 键序不同 → 同字节同摘要（`{"b":1,"a":2}` 与 `{"a":2,"b":1}` 均 `b'{"a":2,"b":1}'`）；③ 中文不转义（`{"name":"发票"}` → 原样 UTF-8，无 `\u`）；负数/嵌套 list/null 精确字节；④ `100`(int)/`"100"`/`"100.00"` 三种字节三种摘要，`float 100.0` 与 `Decimal("100.00")` 被拒（`CanonicalJSONError`）；⑤ 非 str 键、`2^53`、`bytes/tuple/set/自定义对象`、循环引用、孤立代理项全部被拒；⑥ `bytearray` 入参调用后未被修改（gmssl 原地改入参已被封装挡住）；`str` 入参 `TypeError`；⑦ **跨语言**：`canonical_cross_check.py` 与 `.mjs` 三字段逐字相等（`canonical_utf8_b64` / `sm3=ae5c73b0…` / `sha256=d733f2fa…`）；用 JS 默认 UTF-16 排序算出的 SM3 是 `1504e4f3…`（证明 fixture 能抓住该坑，D-074 #11）；⑧ `pytest tests/test_crypto` **54 passed**，全量单测 **480 passed**（基线 426 + 54），`alembic check` 零差异（本卡无迁移）；⑨ 装 gmssl 后 numpy 1.26.4 / paddle 2.6.2 / paddleocr 2.7.3 未变 | ✅ 通过（D-073 依赖落地、D-074 canonical 口径；`hmac_sm3` 留 S4-3） |
| S3-9（补充卡） | `alembic/versions/0007_invoice_review.py`（`reviewed_by` FK users、`reviewed_at` + 索引）、`models/invoices.py`、`schemas/invoice.py`（`InvoiceOut` 两字段、`review_status` 过滤）、`services/invoice_service.py`（`update_fields` 删除金额角色限制 + 复核锁定、`review_invoice`、列表过滤）、`api/v1/invoices.py`（`POST /{id}/review`）、`web/src/{types,invoices api,InvoiceDetail,InvoiceList}`、`backend/tests/test_review.py`（新）+ 改 `test_invoices`、`docs/{spec,plan}/s3-9-two-level-review.md` | ① 提交人改金额：不勾选 **422/42202**、勾选 **200**（原 403 删除）；② 提交人确认 113 → v4；审核人 review → **200**，`reviewed_by=15`、`reviewed_at` 落库、v5；③ 列表 `review_status=pending` 空、`reviewed=[113]`；④ 复核后提交人改字段 → **409/40900**；重复复核 → **200 且 v 不变**（幂等）；⑤ 迁移 0007 upgrade/downgrade/upgrade，`alembic check` 零差异；⑥ 单元 422 / 集成 245 passed，`typecheck`/`build` 通过；⑦ UI 断言 reviewer 详情“已复核”通过，截图 `backend/eval/results/s39-reviewed.png` | ✅ 通过（D-068 取代 D-017 金额限制；复核即锁定，无反复核） |
| S3-8 | `alembic/versions/0006_batch_upload.py`（`invoices.batch_id` + 索引、`ocr_jobs.dispatched`）、`models/{invoices,ocr_jobs}.py`、`core/config.py` + `.env.example`（`OCR_MAX_ACTIVE_PER_USER=3`）、`services/batch_service.py`（新：`create_batch`/`dispatch_pending`/`dispatch_for_job`/`batch_summary`/`retry_item`）、`tasks/worker.py`（`after_job_end` 补位，ARQ 0.28 只传 ctx → `Job.info()` 回查参数）、`api/v1/invoices.py` + `schemas/invoice.py`（3 个 batch 接口）、`web/src/{api/files.ts,api/invoices.ts,types/invoice.ts,views/Upload.vue}`（批量模式）、`backend/tests/test_batch.py`、`docs/{spec,plan}/s3-8-batch.md` | ① 迁移 0006 可 upgrade/downgrade/upgrade，`alembic check` 零差异；② 真实 30 文件：POST **202 / created=30 / 1.17s**，轮询 **48.6s** 全部 `succeeded`，逐项 `progress` 全 100；③ 并发：批内 5 文件只有 **3** 个 `dispatched=true`（其余 `false` 排队），GET 轮询/`after_job_end` 补位后依次入队；④ 去重/非法按项返回（`duplicates/rejected`），不拖垮整批；⑤ 重试：**202 + 新 job**，`invoices` 数不变（30→30，job 81 有 2 条），重试后识别成功；⑥ 权限/404/422/409 全覆盖；⑦ 单元 422 / 集成 237 passed，`typecheck`/`build` 通过；⑧ 冒烟 30 票/对象/账号已清理 | ✅ 通过（唯一新增迁移 0006；前端批量 UI 仅 typecheck/build，未做 30 文件浏览器点击自动化） |
| S3-7 | `backend/app/services/duplicate.py`（新：`check_duplicates` 强/弱、只读）、`schemas/invoice.py`（`DuplicateMatchOut/DuplicateCheckData`）、`api/v1/invoices.py`（`GET /{id}/duplicate-check`）、`services/ocr_pipeline.py`（唯一冲突回退写 `duplicate_of` + metric）、`services/invoice_service.py`（confirm 强重复闸门 409）、`web/src/{api/invoices.ts,types/invoice.ts,views/InvoiceDetail.vue}`（重复横幅）、`backend/tests/test_duplicate.py`、`docs/{spec,plan}/s3-7-duplicate.md` | ① 真实冒烟：A 票 id49（票号 05975405）；同图再传 **409/40901 + invoice_id=49**；同票面重编码（不同 sha）为 B id50 → 流水线写 `duplicate_of=49`、`invoice_no=None`（作业 succeeded）；② `GET /duplicate-check` B → **strong**, kind=invoice_no, match=49；③ `POST /confirm` B → **409/40900**, `data={invoice_id:49, kind:invoice_no}`；④ 弱重复（同金额+日期+销方、不同票号）单测为 weak、缺维/void 为 none；⑤ 并发：两 session 同写 (code,no) → 第二个 `IntegrityError`（唯一索引兜底）；⑥ 详情页红色横幅断言“疑似重复票据”通过，截图 `backend/eval/results/s37-dup.png`；⑦ 单元 422 / 集成 230 passed，`typecheck`/`build` 通过，`alembic check` 零差异（head 0005，无迁移）；⑧ 冒烟票据/对象/账号已清理 | ✅ 通过（新增 duplicate-check 覆盖 D-013；弱重复只提示不阻断） |
| S3-6 | `backend/app/services/{rule_source,invoice_service}.py`（`resolve_rules` 共享；`collect_confirm_errors` 四类闸门；`confirm_invoice` 幂等/版本/落库）、`api/v1/invoices.py` + `schemas/invoice.py`（`POST /{id}/confirm`、`ConfirmRequest`）、`web/src/{api/invoices.ts,types/invoice.ts,views/InvoiceDetail.vue,components/FieldTable.vue}`（确认整单/逐字段确认/422 列表）、`backend/tests/{test_rule_source,test_confirm}.py`、`docs/{spec,plan}/s3-6-confirm.md` | ① 闸门顺序 必填→冲突→低置信未确认→validators，同字段只留第一条，`422/42201` + `data.fields`；② 真实冒烟（vat_general）：首次确认 **422**，fields=amount(冲突)/invoice_no/tax_amount/total_amount/total_amount_upper(低置信)；reviewer 修金额 200、submitter 逐字段确认 200 → 再确认 **200**，`status/extract_status=confirmed`、version 3（0→1→2→3）；③ 幂等：已确认再调 200 且版本不再增加；权限：他人 404 / reviewer 403 / admin 404；旧 version 409；④ 并发验收：同 `version=0` 连发两次 PATCH → 200 + 409/40900；⑤ 详情页新增“确认整单”与逐字段“确认”按钮 + 422 字段列表；截图 `backend/eval/results/s36-confirmed.png`（页面显示“已确认”）；⑥ 单元 422 / 集成 222 passed，`typecheck`/`build` 通过，`alembic check` 零差异（head 0005，无迁移）；⑦ 冒烟票据/对象/账号已清理 | ✅ 通过（审计行留 S4-7；PATCH 未重写） |
| S3-5 | `backend/app/services/image_service.py`（新：照片原字节 / PDF 250DPI / preprocessed；明文闸门）、`backend/app/services/line_service.py`（新：ocr_lines 四点→原图外接框、不含文本）、`schemas/invoice.py`（`OcrLineBoxOut/InvoiceLinesData`）、`api/v1/invoices.py`（`GET /{id}/image`、`GET /{id}/lines`）、`backend/tests/{test_image_service,test_lines_service,test_invoice_media}.py`、`web/src/utils/bbox.ts`、`web/src/components/{ImageCanvas,FieldTable}.vue`、`web/src/views/InvoiceDetail.vue`（左图右表）、`web/src/api/{http,invoices}.ts`、`web/src/types/invoice.ts`、`docs/{spec,plan}/s3-5-detail-canvas.md` | ① `GET /image`：本人/reviewer 200、他人/admin 404、密文/明文关闭 409、页码越界 422；响应头 `ETag/Cache-Control: private,no-store/nosniff`；PDF 按 250 DPI（A4 实测 2066±3×2924±3）；② `GET /lines`：行框经 `to_original` 映射（假 0.5× 断言 `[10,20,300,60]→[5,10,150,30]`），**响应不含 text**，字段归属正确，畸形框跳过；③ 真实冒烟（vat_general）：图片 260403B JPEG、40 行框、12 字段框；显示 532px 时 `scale=0.338854`，`amount` 框 `[841,772,946,795]→[285,262,321,270]`、`total_amount` 同理；④ 点两次放大后 `scale=0.487898`（=×1.44）、框坐标按新比例重算仍落在图片范围内、`visibleLines` 40→35（culling 生效）、`drawMs` 0.39/0.48ms（<16）；截图 `/tmp/s35-normal.png`、`/tmp/s35-zoom.png`（需用户目视抽查）；⑤ 单元 422 / 集成 211 passed，`typecheck`/`build` 通过，`alembic check` 零差异（head 0005，本卡无迁移）；⑥ 冒烟票据/对象/账号已清理 | ✅ 通过（明文影像仅本地；校验失败文案待 S3-6；行框接口为本卡新增契约） |
| S3-4 | `backend/app/schemas/ocr.py`（新：唯一契约，含 `FieldError`/`raw_value`）、`backend/app/ocr/types.py`（转 re-export）、`backend/app/ocr/extractor.py`（`_extract_matched` 保留票面原文）、`backend/app/services/ocr_pipeline.py`（`field_value=raw`、`normalized_value=归一`）、`backend/app/services/invoice_service.py` + `schemas/invoice.py` + `api/v1/invoices.py`（`GET /{id}/fields`）、`scripts/export_ocr_schema.py` + `docs/schemas/ocr_result.schema.json`、`web/src/types/ocr.ts`、`backend/tests/test_ocr_schema.py` + 补 `test_extractor/test_ocr_pipeline/test_invoices`、`docs/{spec,plan}/s3-4-ocr-contract.md` | ① 契约单一来源：5 个模型 + `raw_value`；`ocr/types.py` 只是 re-export（同一类对象断言）；② JSON Schema 入库并由 `--check` + 单测深度比对；前端 `types/ocr.ts` 与模型字段**完全一致**（自动比对）；③ 字段名白名单测试发现并修正 `total_amount_upper` 不在可编辑白名单（详情页会永远无法确认）；④ `GET /fields` 实测：`field_value`=`￥1,796.37`（票面原文）、`normalized_value`=`1796.37`、`review_level` 三档齐全、`conflicts` 只回 `candidate_conflict`、`overall_confidence=0.900`；⑤ `ocr_lines` 单条 executemany 断言通过；⑥ 本卡**无迁移**（head 仍 0005，造空迁移属假动作）；⑦ 单元 415 / 集成 198 passed，`typecheck`/`build` 通过；冒烟数据与账号已清理 | ✅ 通过（旧 `field_value=归一值` 的历史数据不迁移，见遗留 #58；`secrets/` 未动） |
| S3-3 | `backend/app/ocr/refine.py`（新：外扩/clamp/2×放大/四点→外接框 + 坐标映射）、`backend/app/services/reocr_service.py`（新：`submit_reocr` 校验 + `run_reocr` 写回）、`backend/app/api/v1/invoices.py`（`POST /{id}/reocr` 202）、`backend/app/schemas/invoice.py`（`ReocrRequest/ReocrData`）、`backend/app/tasks/{ocr_task,queue,worker}.py`（`reocr_task` + `enqueue_reocr`）、`backend/app/models/enums.py` + `alembic/versions/0005_audit_action_field_reread.py`（`field_reread` 枚举）、`backend/app/core/config.py` + `.env.example`（4 个 `REREAD_*`）、`backend/app/services/ocr_pipeline.py`（bbox 改原图坐标）、`backend/tests/{test_refine,test_reocr}.py` + 补 `test_ocr_pipeline/test_config/test_models`、`web/src/{api/invoices.ts,types/invoice.ts,views/InvoiceDetail.vue}`、`scripts/reread_eval.py`（新）、`docs/{spec,plan}/s3-3-reread.md` | ① 契约按 API.md 的异步 `reocr`（202+job_id），审核 403/他人 404/非 draft 409/字段非法 422/重复作业 409/入队失败 503；② worker：按 `ocr_lines` 四点裁剪（外扩 max(8px,10%行高) → 2× INTER_CUBIC，长边 1024）→ `recognize_line`，**只改 raw_text/confidence/source=ocr_retry，`field_value` 不变**；整单 `overall_confidence/extract_status` 重算；1.5s 预算、空文本/超预算记 skipped；③ 40 张离线验收：112 个低置信字段中 **109 个提升（97.3%）**，平均 Δ **+0.180**（total_amount 38/38、seller_name 22/22、amount/tax 各 10/10、invoice_date 20/21）；④ HTTP 冒烟：amount 0.743→**0.952**、tax 0.690→**0.946**，`source=ocr_retry`、值未变、整单 0.899；⑤ 坐标修正：`invoice_fields.bbox_json` 写原图坐标（`ocr_lines` 保持校正图）；⑥ 迁移 0005 只放开 `field_reread` CHECK（审计行留 S4-7）；单元 396 / 集成 194 passed；⑦ 冒烟数据、对象与临时账号已清理 | ✅ 通过（候选选择/改值归 S3-6；D-060/D-061） |
| S3-2 | `backend/app/services/confidence.py`（新：公式/三档/封顶/聚合 + 纯函数单测）、`backend/app/ocr/{types,extractor}.py`（证据成分 `anchor_score/value_score/agreement`，校验失败封顶 0.45）、`backend/app/services/ocr_pipeline.py`（写 `need_review/overall_confidence/extract_status` + metrics 三档分布）、`backend/app/services/invoice_service.py` + `schemas/invoice.py`（派生 `review_level`）、`backend/app/core/config.py` + `.env.example`（5 个配置）、`web/src/{router/index.ts,utils/landing.ts,views/InvoiceDetail.vue,types/invoice.ts}`、`scripts/confidence_eval.py`（新）、`docs/{spec,plan}/s3-2-confidence.md` | ① 阈值口径定为任务卡三档（0.90/0.60）+ 校验封顶 0.45，SCHEMA 同步；② 置信度验收 40 张 / 213 字段：**auto 101、review 110、manual 2**；阈值扫描（0.50→0.95）字段需人工数 **2/2/23/90/112/152**、整单需核对数 **25/25/33/40/40/40**（单调不减）；不变量全过（校验失败必 ≤0.45、auto 必 ≥0.90、review 必 <0.90 或冲突）；③ 真实链路两张（train/vat_digital）：`extract_status=need_confirm`、`overall_confidence` 0.879/0.881、字段档位逐条正确；④ reviewer 登录落 `/review` → `extract_status=need_confirm` 过滤后 **共 2 张**（UI 冒烟）；⑤ 详情字段带 `review_level`（前端不再硬编码阈值）；⑥ 单元 379 passed、集成 173 passed、`alembic check` 零差异；⑦ 冒烟数据与临时账号已清理 | ✅ 通过（队列用既有 `extract_status` 筛选，不新增状态/接口；D-058/D-059） |
| S3-1 | `backend/app/services/ocr_pipeline.py`（新：`run_pipeline` + `PipelineDeps` 注入 + 19 条流水线测试（6 单元 + 13 集成））、`backend/app/services/ocr_status_service.py`（新）、`backend/app/tasks/{ocr_task,worker}.py`、`backend/app/ocr/{intake,extractor}.py`、`backend/app/services/{storage,job_service}.py`、`backend/app/api/v1/invoices.py`（`GET /{id}/ocr-status`）、`backend/app/schemas/invoice.py`（`OcrStageOut/OcrStatusData`）、`backend/alembic/versions/0004_ocr_jobs_metrics_and_times.py`、`backend/app/models/ocr_jobs.py`、`backend/tests/{test_ocr_pipeline,test_ocr_status,test_storage}.py` + `test_{jobs,intake,extractor,config}.py` 补例、`scripts/ocr_acceptance.py`（新）、`web/src/{api/invoices.ts,types/invoice.ts,views/Upload.vue}`、`docs/{spec,plan}/s3-1-ocr-pipeline.md` | ① 迁移 0004：`ocr_jobs` 加 `metrics/started_at/finished_at`，`alembic check` 零差异；② 流水线：下载（`storage.get_bytes`）→ 临时目录 → `intake.prepare_pages`（PDF 前 3 页）→ PaddleOCR（worker 单例）→ `classify_ticket` → DB 模板规则抽取（YAML 回退）→ `run_validators` → 落 `ocr_lines`/`invoice_fields`/`invoices` 冗余列；③ **真实验收 20 张：20/20 成功（100%）、P50 1012ms、P95 1680ms、max 4995ms、临时目录残留 0**；④ `ocr-status` 返回 4 阶段、`progress`（10/30/40/20）、真 `started_at/finished_at`、`engine_version=2.7.3`；⑤ 详情接口可见字段（如行程单 4 字段）、列表/详情与 `ocr_status=succeeded` 一致；⑥ 单元 365 passed、集成 169 passed、`typecheck`/`build` 通过、上传页冒烟"识别完成"；⑦ 失败分类：对象缺失可重试、契约错误/密文/作废票永久失败；僵尸线程 `superseded` 回滚业务写入；⑧ 测试数据、MinIO 对象与验收账号已清理 | ✅ 通过（`field_value` 暂存归一值待 S3-4；D-055/D-056/D-057） |
| S2-12 | `backend/app/tasks/scheduler.py`、`backend/app/core/config.py`（scheduler/cron/锁配置）、`backend/app/main.py`（lifespan）、`backend/tests/{test_scheduler,test_api_docs}.py`、`requirements.txt/lock`（APScheduler 3.11.1 + tzlocal 5.4.5）、`.env.example`、`docs/{spec,plan}/s2-12-scheduler.md` | ① APScheduler 3.11.1 安装并钉住；② lifespan 启停调度器，`SCHEDULER_ENABLED=false` 完全不启动（测试默认关）；③ 锚定任务每日 02:00（Asia/Shanghai），cron/锁 key/TTL 进 Settings；④ 多副本：`SET NX EX` 抢锁，**成功后锁保留到 TTL**（时段去重），失败释放 + Lua token 校验；⑤ 双 uvicorn + 同一 Redis、`*/10`、TTL=5：4 个 tick 共执行 **4** 次、另一侧 4 次 skipped（不是 8 次）；⑥ `test_api_docs` 保证实际 OpenAPI 路由都在 API.md；⑦ `alembic check` 零差异；⑧ `pytest` 341 passed、集成 146、smoke 9 OK | ✅ 通过（anchor 任务仍为 S4-7 占位，只日志；D-050） |
| S2-11 | `web/src/views/{InvoiceList,InvoiceDetail}.vue`、`web/src/utils/format.ts`、`web/src/api/invoices.ts`（list/update）、`web/src/types/invoice.ts`（全量类型）、`router`（/invoices 列表+详情）、`MainLayout`（reviewer 票据查询）、`backend/app/schemas/invoice.py`（补 vat_digital）+ `tests/test_invoices.py`、`scripts/ui_smoke.mjs`（`--steps`）、`docs/{spec,plan}/s2-11-invoice-ui.md` | ① `npm run typecheck` / `npm run build` 通过；② 列表筛选（状态/抽取/票种/关键字/日期/排序）+ 分页，URL query 为唯一状态源，`?status=draft&invoice_type=vat_digital` 刷新后仍命中；③ 详情字段表（置信度<85% 标红、待确认/冲突标签）；④ 草稿编辑 buyer_name → 保存 → 页面刷新可见，DB `confirmed=true/source=manual/version 0→1`；⑤ submitter 金额字段置灰、reviewer 金额需勾选确认（后端 42202 兜底）；⑥ 影像预览按 D-049 占位"待 S3-5"；⑦ reviewer 菜单"票据查询"且可看全量；⑧ `pytest` 全绿（新增 vat_digital 筛选用例）、`smoke.py` 9 OK；⑨ 测试数据/MinIO/Redis 已清理 | ✅ 通过（预览占位；后端补 vat_digital 漏项；D-049） |
| S2-10 | `web/src/views/Upload.vue`、`web/src/api/{files,invoices}.ts`、`web/src/types/invoice.ts`、`web/src/api/http.ts`（`silent`）、`router`（/upload 换组件）、`.env.development/.env.example`（`VITE_MAX_UPLOAD_MB`）、`scripts/ui_smoke.mjs`（`--upload`）、`docs/{spec,plan}/s2-10-upload-ui.md` | ① `npm run typecheck` / `npm run build` 通过；② 真实上传 JPEG：行状态"失败 / 识别失败：TaskNotReadyError: OCR 流水线未接入（S3-1）"（S2-7 真实行为）；③ 同文件重传：绿色"已上传 / 该票据已上传 + 查看链接"，无全局红条；④ `.txt` 本地拒绝"只支持 JPG/PNG/PDF"；⑤ 串行上传 + 单文件/总进度，轮询 3s 起退避、120s 上限；⑥ `scripts/smoke.py` **9 OK / 0 FAIL**；⑦ 测试数据/MinIO/Redis 已清理 | ✅ 通过（轮询用详情接口，S3-1 换 `ocr-status`；批量不自动跳详情；D-048） |
| S2-9 | `web/src/{api/auth.ts,api/http.ts,stores/user.ts,router/index.ts,main.ts,App.vue,layout/MainLayout.vue,views/{Login,Forbidden,Placeholder,Home}.vue,types/{auth,api}.ts}`、`scripts/ui_smoke.mjs`（`--fill/--clear`）、`docs/{spec,plan}/s2-9-fe-login.md` | ① `npm run typecheck` / `npm run build` 通过；② 真实登录（无头 Edge + CDP）：submitter 菜单含「我的票据」、reviewer 含「待核对/待审核」、admin 含「票种模板/审计日志」；③ admin 访问 `/upload` → `/403`（路由 `meta.roles`）；④ 401 单飞：后端 `ACCESS_TOKEN_TTL_SECONDS=1`，登录后等 2s 并发两个 `/auth/me` → **refreshCount=1**，两个请求都成功；⑤ 刷新失败才清会话跳登录（提示 1.5s 节流）；⑥ `scripts/smoke.py` **9 OK / 0 FAIL**（前端 dev server 在跑）；⑦ 测试用户与 Redis key 已清理 | ✅ 通过（未实现业务页用占位，不放假数据；DEV 诊断钩子仅开发态，D-047） |
| S2-8 | `backend/app/services/{template_service,template_registry,template_seed}.py`、`backend/app/api/v1/templates.py`、`backend/app/schemas/template.py`、`backend/alembic/versions/0003_ticket_type_vat_digital.py`、`scripts/seed_templates.py`、`backend/tests/test_templates.py`、`docs/{spec,plan}/s2-8-templates.md` | ① 权限：reviewer 列表/详情 200、写 403；submitter 列表 403；② 创建 201/version=1/active 默认 false；重复 type_code 409；非法 type_code/空 keywords/非法正则/未知 strategy 422；③ 列表默认 active、`active=false` 查历史、type_code 过滤、列表不含 `field_specs_json`；④ 详情含 specs + rules；⑤ 升版本：PUT 生成 v2 新行、v1 保留、规则复制+覆盖、过期 version 409（带 `current_version`）、同票种仅 1 条 active；⑥ 60s 缓存：改库不 invalidate 读到旧快照、invalidate 后读到新值；⑦ `/test`：真 OCR 抽出 10 字段、matched 关键字、confidence 0.881；reviewer 403 / 未启用 422 / 假图 422 / OCR 异常 50010；不建票不写对象；⑧ seed 幂等（5 模板/37 规则，二次跑跳过）；⑨ `pytest 332 passed`、`pytest -m integration 146 passed` | ✅ 通过（补 `vat_digital`：SCHEMA + enum + 0003；D-046） |
| S2-7 | `backend/app/services/job_service.py`、`backend/app/tasks/{worker,ocr_task,queue}.py`、`backend/app/api/v1/files.py`（入队）、`backend/app/api/deps.py`（`get_task_queue`）、`backend/tests/{test_jobs,test_upload}.py`、`docs/{spec,plan}/s2-7-arq.md` | ① 作业状态机 `pending→running→succeeded|failed`、可重试失败回 `pending`；② 幂等双保险：终态跳过 + Redis `SET NX EX 600`（锁占用返回 `locked`、handler 不执行）；③ 重试：第 1 次失败退避 **5s**、第 2 次 **30s**、第 3 次终态 `failed` 且 `retry_count=3`；永久错误（`TaskNotReadyError`）直接 failed；④ 僵尸恢复：30min 前 `running` → `failed`（新的 `running` 不动）；⑤ 上传入队：假队列收到 `job_id`；队列不可用 → 503 + job `failed`；⑥ 真实 ARQ worker + uvicorn：上传 202 → worker 执行 → `ocr_jobs failed / retry_count=1 / TaskNotReadyError(S3-1)`；⑦ 依赖 ARQ 导致 redis-py 8.1→5.3.1，全量回归通过；⑧ `pytest 323 passed`、`pytest -m integration 137 passed`、smoke 8 OK | ✅ 通过（`ocr_task` 在 S3-1 前如实失败，不伪造成功；D-045） |
| S2-6 | `backend/app/services/{storage,upload_service}.py`、`backend/app/api/v1/files.py`、`backend/app/schemas/invoice.py`（UploadData）、`backend/core/config.py`（明文开关/prod 校验）、`backend/.env.example`、`backend/tests/test_upload.py`、`docs/{spec,plan}/s2-6-upload.md` | ① `POST /invoices/upload` 202，返回 `invoice_id/file_id/job_id/sha256/status/ocr_status/dedup`，**响应无 storage_key**；② 对象名 `invoices/yyyy/mm/<32hex>.bin`（SCHEMA），原文件名只展示（`../../etc/passwd` → `passwd`）；③ 魔数 + Pillow 真图 + pypdf 页数（≤5）校验；空文件 422、超限 413、假图/TIFF/6 页 PDF 422；④ **去重先于写对象**：同字节二次上传 409/40901 + 同一 `invoice_id`，MinIO 只有 1 个对象（put 次数 1）；⑤ 落库同一事务写 file+invoice+job（pending/preprocess），失败或并发同 sha 时补偿删除对象；⑥ 权限：reviewer 403、admin 404、未登录 401；⑦ 真实 uvicorn + MinIO 手工：202 → 409 → 对象存在（643B）→ 清理干净；⑧ `pytest 311 passed`、`pytest -m integration 129 passed` | ✅ 通过（S4 前本地明文调试，prod 启动拒绝，D-044；ARQ 入队留 S2-7） |
| S2-5 | `backend/app/services/invoice_service.py`、`backend/app/api/v1/invoices.py`、`backend/app/schemas/invoice.py`、`backend/app/core/exceptions.py`（42201/42202）、`backend/tests/test_invoices.py`、`docs/{spec,plan}/s2-5-invoices.md` | ① `transition()` 单入口：`draft→confirmed→in_report→archived` 合法、非法跳转/终态 409；② 列表：三权限（submitter 本人 / reviewer 全量 / admin **404**）+ 7 类筛选 + 分页 + 默认排除 void + 金额两位小数字符串 + 无 `storage_key`；③ 详情：`invoice + fields + latest_job`，他人/不存在/admin 均 404；④ 软删：204、重复 204、`in_report/archived` 409、他人 404、**void 释放 `(code,no)` 唯一索引后可重建**；⑤ 字段修改：submitter 非金额 200 / 金额 403、reviewer 金额需 `confirm_amount`（否则 42202）、版本冲突 409、非草稿 409、非法字段/值 422、未决冲突 42201 且**回滚**、`confirm` 控制 `need_review`；⑥ 链路 `create_draft → PATCH fields → GET detail` 通过；⑦ `pytest 310 passed`、`pytest -m integration 118 passed` | ✅ 通过（按契约范围：无 `POST /invoices`、无 `PATCH /invoices/{id}`；字段修改用 PATCH /fields，见 D-043） |
| S2-4 | `backend/app/schemas/common.py`（`PageParams/PageResult`）、`backend/app/services/pagination.py`、`backend/app/utils/text.py`（`escape_like`）、`backend/tests/test_pagination.py`、`scripts/bench_list.py`、`docs/{spec,plan}/s2-4-pagination.md` | ① `PageParams`：默认 1/20、`page=0` 与 `page_size=1000` → 422、`order` 只允许 asc/desc；② 排序白名单（created_at/invoice_date/total_amount）+ `id asc` 稳定兜底，未知字段 **42200** 并列出可选值；③ `count` 与数据共用同一 `Select`（`order_by(None).subquery()`），翻页并集=全集、无重叠、超末页 `items=[]` 且 `total` 不变；④ 与 S2-3 组合：submitter/reviewer 的 `total` 按可见性分别计算；⑤ N+1 断言：join 列表每页恰好 **2 条 SELECT**；⑥ 50,000 行基准四组 p50/p95 = **13.4/15.9、20.1/25.4、19.3/21.9、27.6/32.3 ms**，`EXPLAIN ANALYZE` 走 `Index Scan using ix_invoices_uploader_created`；⑦ `pytest 310 passed`、`pytest -m integration 87 passed` | ✅ 通过（关系方案选 A：显式 join，不改 S2-1 锚点，见 D-042） |
| S2-3 | `backend/app/services/repository.py`、`backend/tests/{test_repository,test_isolation}.py`、`docs/{spec,plan}/s2-3-isolation.md` | ① 策略表覆盖 `invoices` / `invoice_files` / `report_forms`；未登记模型 `false()` 默认拒绝；② submitter A/B 列表互相不可见、B 查 A 详情抛 `PermissionDeniedError`（存在但无权）与 `NotFoundError`（确实不存在）内部可区分；③ reviewer 能看到全部票据/文件，报销单只看到 `submitted/approved/rejected/archived`（8 状态参数化）；④ admin 三类业务数据全 404；⑤ 列表与详情对四种角色逐一一致；⑥ HTTP（真实 `create_app()` + 异常处理器）：B 查 A → **404/40400**、reviewer 200、admin 404、未登录 401、B 列表不含 A；⑦ `pytest 297 passed`、`pytest -m integration 74 passed`（隔离 23 条） | ✅ 通过（生产 `/invoices` 接口在 S2-5 接同一函数，届时补手工换 id 验收） |
| S2-2 | `backend/app/{core/{security,redis}.py, services/{auth_service,token_store}.py, schemas/auth.py, api/deps.py, api/v1/auth.py, utils/text.py}`、`backend/alembic/versions/0002_audit_action_login_logout.py`、`scripts/create_user.py`、`backend/tests/{test_security,test_auth}.py`、`docs/{spec,plan}/s2-2-auth.md` | ① `POST /auth/register`（201，固定 submitter、用户名 trim+小写）/ `login`（200，`expires_in=1800`、`refresh_expires_in=604800`）/ `me`（角色来自数据库）/ `refresh`（轮换：旧 refresh 立即 401）/ `logout`（吊销 refresh，幂等）；② 登录失败未知账号与错口令**同码同文案**（401/40101），`disabled/locked` → 403/40301；③ `require_roles("reviewer")` 对 submitter 返回 **403**、reviewer 200；④ 真实 uvicorn + Redis 手工链路：login→redis 白名单 1 key→refresh 轮换→旧令牌 401→logout→refresh 401，冒烟用户与 Redis key 均已清理；⑤ `cat /dev/null` 无泄漏：失败日志只含 `username_sha256`，不含口令；⑥ Argon2id 实测 hash **59.4ms** / verify **45.7ms**（m=64MiB,t=3,p=4）；⑦ `pytest 288 passed`、`pytest -m integration 51 passed`、smoke **8 OK / 0 FAIL** | ✅ 通过（register 按 API.md 开放；审计枚举补 3 值 + 0002，但审计行留 S4-7，见 D-040） |
| S2-1 | `backend/app/models/`（`base.py`、`enums.py` + 16 个一表一文件 + `__init__.py`）、`backend/alembic/{alembic.ini,env.py,script.py.mako,versions/0001_init.py}`、`backend/app/core/database.py`、`backend/tests/{test_models,test_database,test_migration}.py`、`docs/{spec,plan}/s2-1-db-models.md` | ① `alembic upgrade head` 成功；`downgrade base` 后只剩 `alembic_version`，再 `upgrade head` 成功；② `alembic check` = **No new upgrade operations detected**；③ `\dt` 17 个关系 = **16 业务表 + alembic_version**，主键全部 `GENERATED ALWAYS AS IDENTITY`；④ 5 条部分唯一索引与 GIN/DESC 索引在库里逐条核对（`pg_indexes`）；⑤ 集成 23 条：重复发票号被拦 + `void` 后放行、重复 `sha256`、同单两条 active 签名、同票种两个 active 模板、同 `file_id`/`form_id` 信封、`verified = (verify_result='ok')`、非法枚举、空 `wrapped_key_b64`、密文双空、负数金额、十六进制格式、`canonical_json` 原样、`Decimal` 往返、identity 自增、坏外键；⑥ `pytest 264 passed`（新增 100 条单元）、`pytest -m integration 23 passed`；⑦ `scripts/smoke.py` **8 OK / 0 FAIL**（后端临时起停验证） | ✅ 通过（JSONB 未按任务卡提示词设 `'{}'::jsonb` 默认值，按 SCHEMA 契约执行，理由见 D-037） |
| S0-1 | 目录骨架（`backend/app/{api/v1,models,schemas,services,ocr/templates,crypto/sm,core,tasks,utils}`、`alembic/versions`、`tests`、`eval/{datasets,configs,results}`、`web/src/*`、`docs/{spec,plan}`、`deploy`、`scripts`、`samples`、`secrets`）、`.gitignore`、`.gitattributes`、`README.md` | `find . -type d` 与《完整方案》4.6 逐项对照；`git status` 干净 | ✅ 通过。`.gitignore` 已强制排除 `secrets/` 与 `samples/` 影像 |
| S0-2 | `AGENTS.md`（含 §5 OCR 高压线、§6 密码高压线、§10 编码细则、§10.8 一致性锚点表、§11 spec→plan 协议） | 通读确认"这个项目用什么、不用什么"能一句话说清 | ✅ 通过。锚点表 S0 阶段留空，S0-5 起回填 |
| S1-6 | `backend/eval/metrics.py`、`backend/eval/configs/baseline.yaml`、`scripts/ocr_baseline.py`、`backend/tests/test_metrics.py`、`backend/eval/results/baseline_report.md` | ① 60 张全链路基线：检测 **Hmean 0.951**、字符准确率 **0.819**、字段级 **0.895**、整单 **0.650**；② 预处理开关对照：Hmean 0.974（关）vs 0.951（开）、字段级 0.888 vs 0.895；③ 耗时 P50：preprocess 111ms + ocr 1422ms + layout 52ms + extract 1ms = 1591ms；④ 23 条指标单测全部手算断言；⑤ `pytest` 156 passed；⑥ Markdown 报告可直接贴论文 | ✅ 通过（整单 0.650，taxi 仍是最弱票种） |
| S1-5 | `backend/app/ocr/{extractor.py,validators.py}`、`templates/field_rules.yaml`、`scripts/{field_demo,field_eval}.py`、`backend/tests/{test_validators,test_extractor}.py` | ① 字段级准确率 **89.3%（384/430）**：check_code 100%、invoice_code 96.7%、invoice_date 96.7%、invoice_no 95%、buyer_name 93.3%、seller_tax_no 90%、passenger_name 90%、**amount 86.7%、tax_amount 83.3%**（D-033 约束反解后）、seller_name 86.0%、total_amount 76.7%；② 校验器 38 条单测（含"税号改一位必须报错"）；③ 抽取器 10 条单测（同优先级冲突保留候选、缺失必填、税号错折半）；④ `pytest` 133 passed | ✅ 通过 |
| S1-4 | `backend/app/ocr/layout.py`、`backend/app/ocr/templates/*.yaml`（5 个）、`scripts/{layout_demo,layout_eval}.py`、`backend/tests/test_layout.py` | ① 60 张票种判对率 **100%（60/60）**，unknown 0（任务卡要求 ≥95%）；② 区域划分：单张数电票 header 10 / body 31 / footer 2、行程单 header 1–3 / body 13–15 / footer 2–4；③ 13 条单测（五类票种、未知、真歧义、区域单调、归一化不变、block 聚类）；④ 顺带修 3 个预处理几何缺陷（退化/自交/单边裁切），OCR 行数恢复 20→20、39→39、41→41；⑤ `pytest` 85 passed | ✅ 通过 |
| S1-3 | `backend/app/ocr/{types,detector,recognizer}.py`、`scripts/ocr_demo.py`、`backend/tests/test_ocr_contract.py`、`docs/{spec,plan}/s1-3-ocr-engine.md` | ① 单张 `vat_digital_landscape_001`：**41 行**、平均置信度 ≈0.99、无乱码、**两次结果完全一致**；② 批量 20 张：行数均值 14.5（8–20）、平均置信度 0.958、推理 P50 944ms / P95 1541ms；③ 困难样本 `taxi_002` + 预处理：7 行、置信度 0.92–0.99（同图 Windows OCR 只能读出 0–2 个字段）；④ 7 条契约测试全过，含"3.x dict 结构必须报错"；⑤ `pytest` 72 passed | ✅ 通过 |
| S1-2 | `backend/app/ocr/preprocess.py` + `app/ocr/intake.py`（PDF 只渲染不预处理；照片走预处理 + 重拍闸门）、`scripts/{preprocess_demo,preprocess_eval}.py`、`backend/tests/{test_preprocess_exif,test_preprocess_geometry,test_intake}.py` | ① 23 条预处理/分流单测全过（EXIF 旋转、已知梯形还原、坐标回算、开关组合冒烟、降级不抛异常）；② 60 张数据集（**D-035 最新数字**）：正视角满幅 17 / 透视 21 / minAreaRect 兜底 22、失败 0；quality ok 54 / low 6；**建议重拍 18 张**；耗时 **P50 75ms / P95 201ms**（预算 300ms）；③ `preprocess_eval`：可比样本 OCR 命中率 88% → 88%（无样本变差）；④ 真实拍照样本 R-001 实测：四角点检出、quality=ok、印章 1 处、314ms；⑤ demo 输出 60 张前后对照图 | ✅ 通过（R-001 的"不躺倒不扭曲"需人工眼看对照图，见遗留 16） |
| S1-1 | `scripts/{sample_fonts,sample_data,invoice_layout,invoice_templates,perturb,make_samples,check_annotations,verify_samples}.py`、`scripts/ocr_probe.ps1`、`backend/eval/datasets/{annotations_synth_v1.jsonl,README.md}`、`samples/MANIFEST.md`、`docs/{spec,plan}/s1-1-samples.md`、3 个测试文件 | ① `make_samples.py` 生成 **60 张**（dev 36 / test 24，**5 票种 × 6 版式**，含 2026 主流的数电票 `vat_digital`）；② `check_annotations.py --strict` 全过；③ `verify_samples.py` **5 项全过**（渲染一致性 4.1×、OCR 字段命中率 干净 0.89 / 困难 0.86、标注自洽、成像覆盖、划分完整）；④ `pytest` 50 passed（新增 26 条）；⑤ 系统 OCR 能读出合成票的字段值 | ✅ 通过 |
| S0-7 | `scripts/smoke.py`（配置/端口/三件套/后端/迁移/前端六类检查，`--strict` 与 `-v`）、完整 `README.md`（环境要求、8 步快速开始、端口表、常用命令、**9 条 Windows 坑**、故障排查表、文档索引、已知限制）、`docs/DECISIONS.md` D-022（基础设施 ADR） | ① `smoke.py` 在后端未起时给出"后端没起 + 启动命令"而不是 traceback；② 故意写错 PG 口令 → 报"认证失败 + 两处 .env 要对齐"（与"服务没起"区分开）；③ 停掉整个 docker 栈 → 每项都提示 `docker compose up -d`；④ 全起后 OK 7 / WARN 1（迁移，S2-1 前正常）/ FAIL 0，退出码 0；⑤ `--strict` 下 WARN 使退出码为 1 | ✅ 通过 |
| S0-6 | `web/`（Vue 3.5 + Vite 8 + TS 6 + Element Plus + Pinia + Vue Router：`src/{main.ts,App.vue,api/http.ts,api/system.ts,router/index.ts,stores/user.ts,views/*}`、`package.json` 精确版本、`.env.{development,production,example}`）、`scripts/ui_smoke.mjs` | ① `npm run typecheck` 无错、`npm run build` 成功；② 干净浏览器访问 `/` → 守卫重定向到 `/login?redirect=/` 且登录页渲染；③ `/debug` 显示后端 health（4 个 tag 全 ok）+ trace_id；④ 点「触发一个 404」→ 弹出后端 message「资源不存在」并把 trace_id 回填；⑤ 停掉 MinIO → 页面显示 degraded + 一个 fail，恢复后回到全 ok；⑥ 控制台无 CORS 报错 | ✅ 通过 |
| S0-5 | `backend/app/{main.py,core/{config,logging,exceptions,health}.py,schemas/common.py,api/v1/health.py}`、`backend/{requirements.txt,requirements.lock.txt,pyproject.toml,.env.example}`、`backend/tests/{conftest,test_config,test_health,test_errors_trace}.py` | ① `pytest -q` 24 passed；② 起 uvicorn 后 `/api/v1/health` 真实连三件套返回 200（db/redis/minio 全 ok）；③ 不存在路由返回 `40400` 且响应头 `X-Trace-Id` 与 body 一致；④ 同一 trace_id 能在日志里串起错误日志 + 访问日志；⑤ 缺配置启动即抛 `ConfigError` 并列出缺哪几项 | ✅ 通过 |
| S0-4 | `deploy/docker-compose.dev.yml`（pg16 + redis7 + minio + 幂等 minio-init，全量 healthcheck）、`deploy/.env.example`、`deploy/init/01_extensions.sql`（pgcrypto）、`deploy/README.md`、`scripts/check_services.py`（纯标准库，5 项检查） | `docker compose up -d` 后 `python scripts/check_services.py` 输出 5/5 OK；`down` 不删卷再 `up` 后 bucket 仍在、`minio-init` 幂等不报错 | ✅ 通过 |
| S0-3 | `docs/SCHEMA.md`（641 行，16 张表，5 组，含「定稿清单」20 项）、`docs/API.md`（646 行，36 个接口，9 组，含 §12 定稿记录）、`docs/DECISIONS.md`（D-001~D-018）、`docs/EVAL.md`、`docs/PROGRESS.md`、`samples/README.md`，并把《完整方案》《实施计划》《AGENTS.md》纳入 `docs/` | ① 表数 16、业务不变式索引齐备；② 接口数 36、分组齐全、全部标注权限/类型/幂等/任务号；③ 两文档字段名交叉核对；④ **契约内无残留 `TODO 待定`** | ✅ 通过。S0-3 结束后无阻塞项 |

## 遗留问题（TECH DEBT）

> **S0-3 已把上一版 7 项全部拍板**（结论见下表），完整决策轨迹在 `docs/SCHEMA.md`「定稿清单」与 `docs/DECISIONS.md` D-011 ~ D-018。
> 这里改为“已定口径”，供回溯；新的遗留问题再往下追加。

| # | 原问题 | 已定结论 | 落地位置 |
|---|---|---|---|
| 1 | `invoices.status` 与方案 6.6 状态图口径冲突 | 票据级只留 5 值，报销单级状态落 `report_forms`；论文把 6.6 拆成两张状态机图 | SCHEMA 定稿 #1 / D-011 |
| 2 | `invoice_files.envelope_id` 循环外键 | **删除该列**，只保留 `envelopes.file_id` + 部分唯一索引 | SCHEMA 定稿 #4 / D-012 |
| 3 | 审计哈希链并发分叉 | 追加时取事务级咨询锁 `pg_advisory_xact_lock` | SCHEMA 定稿 #14 / D-015 |
| 4 | 单文件上限 10MB vs 20MB 口径不一 | **20MB**（`MAX_UPLOAD_MB=20`），单一来源 | API 上传入参 / D-018 |
| 5 | 金额字段人工修改权限未定 | **仅 `reviewer` 可改且需 `confirm_amount=true`**；`admin` 404 | API PATCH 备注 / D-017 |
| 6 | 财务服务器私钥口令托管方式 | 走**环境变量** `FINANCE_KEY_PASSPHRASE`，不经 HTTP | API 验签入参 / D-016 |
| 7 | 是否支持红冲 / 负数票 | **本期不支持**，`total_amount >= 0` 保留 | SCHEMA 定稿 #10 / D-013 |

当前**无阻塞项**。S2-1 已闭环：部分索引（`postgresql_where`）、GIN、`DESC` 表达式与 CHECK
在 Alembic 里的写法已实跑验证，并配集成测试从 `pg_constraint` / `pg_indexes` 回读。

S1-1 新增的遗留（不阻塞 S1-2）：

| # | 问题 | 影响 | 计划处理 |
|---|---|---|---|
| 1 | 真实样本 R-001 的票种与字段未人工补录 | 只能做定性对照，不能进评测 | 你方便时告知票种，我补进 MANIFEST |
| 2 | 合成集只有 40 张，`test` 仅 16 张 | 指标置信区间宽，只能做配置间相对比较 | S6 前若需要可 `--n 80` 扩量（脚本已支持） |
| 3 | 版式与真实票仍有差距（字体、栏线、防伪底纹未模拟） | 真实票据上指标会低于合成集 | 论文如实写"合成集为主 + 真实样本定性对照" |
| 4 | 数电票版式靠"真实 PDF 的坐标量测"复刻，未逐像素对照 | 合成数电票可能比真票"整齐"，检测框分布偏乐观 | S1-2 预处理阶段把 R-002 的 PDF 转图后与合成图并排比一次（人工） |
| 5 | 抽取规则需要按票种分支（数电票没有代码/校验码） | 影响 S1-5/S2-8 的规则设计 | S1-5 实现时按 `ticket_type` 分派 |
| 6 | 远景小票（纸面 ~300px 高）预处理后 OCR 仍读不出 | 这类样本的准确率会被拖低 | 属信息量不足，交给人机协同（低置信 → 人工确认）；S6 分组报告 |
| 7 | `preprocess` 最慢一张 447ms（2800px 原图） | 极端大图会超预算 | S3 的 Worker 里按任务超时兜底；必要时把 warp_max_side 降到 2000 |
| 8 | `preprocess_eval.py` 目前用 **Windows OCR** 当基准 | 结论偏保守（PaddleOCR 明显更强） | S6 前把该脚本的 OCR 后端换成 PaddleOCR，重跑一次"预处理增益" |
| 9 | PP-OCRv4 模型需首次下载（~18MB，到 `~/.paddleocr`） | 离线机器无法首次运行 | 已提供 `OCR_MODEL_ROOT`；S7 的冷启动检查里补"模型预置"步骤 |
| 10 | S1-4 只有 header/body/footer 三段，细分区（明细表/合计/印章）靠 S1-5 的关键字锚定 | 明细表区域定位不够精确 | S1-5 里用关键字 + 坐标邻近补足；必要时在模板里加 `zones` |
| 11 | amount/tax 在旧版票竖屏截图上漏抽 10 张（合计行被拆行 + 印章遮挡） | 这两字段准确率只有 56.7%/60% | 下轮加"按列对齐"的表结构解析，或用 amount+tax 交叉校验 |
| 12 | total_amount 错 11 张：合计行未检出时退到"金额"锚点 | 合计可能被取成明细金额 | 用 `amount + tax` 与候选交叉校验后择优 |
| 13 | 整单正确率 0.633：`taxi_ticket` 仍只有 0.200 | 论文里"整单全对率"受单一票种拖累 | 出租车票字太小（纸面约 300px 高），需 S3-3 的局部二次识别；S6 分组报告 |
| 13b | `total_amount` 76.7%（11 张错） | 合计行未检出时退到"金额"锚点 | 下轮用 amount+tax 与候选交叉校验择优 |
| 14 | 预处理对检测是负收益（Hmean -0.026） | 说明 S1-2 的部分步骤在"本来就没歪"的图上纯损失 | S6 做逐开关消融，按票种/扰动分组给出"什么情况下该开" |
| 15 | 真实样本 R-001 的票种仍未确认 → 判为 unknown，只抽出 1 个字段 | 论文里"真实样本定性对照"只有 1 个数字 | 你告知票种后补 MANIFEST；必要时为新票种补模板 |
| 16 | S1-2 的"肉眼确认不躺倒不扭曲"无法由 AI 完成 | 合成集与 R-001 的对照图需人工抽查一次 | 见 `backend/eval/results/preprocess/*_compare.jpg` 与 `layout/*_layout.jpg`（本地、不入库） |
| 17 | ✅ 已闭合（S3-1）：`intake.prepare_pages` 已接进 Worker，PDF 多页与照片重拍提示均走该入口 | — | — |
| 18 | 重拍提示在合成集上触发了 18/60（多为远景/贴边） | 合成集里"故意拍坏"的样本本来就多，属预期 | S3 的前端上传页展示引导与提示；真实用户场景触发率应远低于此 |
| 19 | 票种判对率是**合成集**上的 100% | 真实票据（尤其遮挡严重的）会更低 | 论文如实写"合成集 100%，真实样本仅定性"；S6 用真实样本抽检 |
| 20 | `audit_logs.action` 仍缺 `template_create` / `report_create` / `issue_cert` | API.md 要求写审计，SCHEMA 枚举未覆盖；S4-7 只做链、没写这些动作，未提前加枚举 | S5 写这些动作时按 D-040 流程补 SCHEMA + 迁移（S4-7 已确认加值不影响链） |
| 21 | `login_failed` / `logout` / `forbidden` 审计行尚未写入 | ✅ 链已就绪（S4-7）；但审计行接入需要改动 auth 路径并与业务同事务，属 S5 范围 | S5 在 auth 服务里调 `append_audit`（当前只有带 `username_sha256` 的应用日志，D-040） |
| 22 | 登出后 access token 在剩余 30min 内仍有效 | 无状态 JWT 的固有代价 | 论文"不足"如实写；要即时失效需引入黑名单/会话表（D-039） |
| 23 | 无登录失败计数 / 自动锁定 / 限流 | `users.status='locked'` 只能由管理动作设置 | S7 安全加固按需补；当前统一 401 文案，不泄露账号存在性 |
| 24 | 生产 `/invoices` 列表/详情接口尚未接入隔离层 | S2-3 只用测试专用路由证明 HTTP 404 语义（接口属 S2-5） | S2-5 必须调用 `filter_visible` / `get_visible_or_404`，并补"手工换 id 得 404"验收 |
| 25 | keyset/游标分页未做（当前 offset） | 5 万行深页（page=1000）p95 25.4ms，offset 够用 | S2-5/S5 若出现更深页或更大规模再评估，只改分页层、端点不动（D-042） |
| 26 | `q` 用 `ILIKE` 子串，无 trigram/GIN 索引 | 5 万行 p95 32.3ms，可接受 | 真实数据规模上升时评估 `pg_trgm`；论文里作为范围限定写明 |
| 27 | 字段修改的审计行（`action=confirm_field`）未写 | ✅ 链已就绪（S4-7）；行接入随 S5 的字段修改接口 | S5 在 `update_fields` 同事务调 `append_audit`，before/after 脱敏（D-040） |
| 28 | 真 HTTP 建单链路待 S2-6 | 契约建票是 `POST /invoices/upload`（S2-6） | S2-6 调用 `create_draft` 后补 curl 建单→改字段→详情；S3-6 复用本卡字段修改 |
| 29 | 字段修改的 42201 较严（任何未决冲突都回滚） | 契约字面如此；S3-6 的 confirm 改为逐字段列出，PATCH 仍整体回滚 | 若评审要求 PATCH 也逐字段回滚，先改 API.md（D-065） |
| 30 | 上传后的 `ocr_jobs` 只落 `pending`，未真正入队/执行 | ARQ 框架属 S2-7，当前没有 Worker | S2-7 用 ARQ 投递并在提交后入队；上传接口形状不变（D-044） |
| 31 | S4 前本地 MinIO 存明文（`is_cipher=false`） | 信封/SM4 未实现；prod 已禁止该配置 | S4-3/S4-4 接入后切密文、`is_cipher=true`、删除 `STORAGE_ALLOW_PLAINTEXT` 开关 |
| 32 | 不支持 TIFF | 契约白名单只有 jpeg/png/pdf（任务卡含 TIFF） | 业务确需时先改 API.md + 魔数白名单 |
| 33 | ✅ 已闭合（S3-1）：`ocr_task` 已接真实流水线，20 张实测全部 `succeeded` | — | — |
| 34 | 僵尸恢复仍用 `created_at` 近似 | 0004 已加 `started_at`，但恢复逻辑仍按 `created_at`；队列积压超 30min 可能误判 | 若要精确，把 `recover_stale_jobs` 的判据换成 `started_at`（需先确认积压场景） |
| 35 | 仍缺"重跑失败作业"入口 | `job_service` 对终态作业直接跳过，客户端重试会命中 sha256 去重（409） | S3-8（批量上传/单条重试）里补；当前按失败人工处理 |
| 36 | layout 的 `regions/aspect_ratio` 仍由 YAML 提供（DB 契约没有这两列） | 模板 CRUD 管不到版面区域 | ✅ S3-1 已定：分类/版式用 YAML，抽取规格用 DB `field_specs_json`（缺票种回退 YAML，`metrics.rules_source` 可见）；全入库需先改 SCHEMA |
| 37 | `template_create` 审计动作仍缺枚举值、未写审计行 | SCHEMA 枚举没有该值；S4-7 不写该动作，未提前加 | S5 与 `report_create`/`issue_cert` 一起补 SCHEMA + 迁移（D-040） |
| 38 | seed 是指令而非启动自动执行 | `create_app()` 启动不连库（测试/健康检查要能离线跑） | 部署脚本（S2-12/S7）调用 `scripts/seed_templates.py`；已有数据跳过 |
| 39 | 业务页面用占位（我的票据/上传/待核对/待审核/模板/规则/证书/审计） | S2-10/S2-11/S5 的页面还没做 | 对应任务卡逐个替换 `Placeholder.vue`；页面明确标"待实现"，不放假数据 |
| 40 | 前端没有单测框架 | 目前只有 `typecheck` + `build` + 浏览器冒烟 | 若后续交互复杂（上传/轮询/字段编辑），评估引入 Vitest（需用户确认新依赖） |
| 41 | access/refresh 存 localStorage（XSS 可读） | D-021 已接受的取舍 | 全项目禁止 `v-html` 渲染用户内容；S6 安全测试里验证 CSP/转义 |
| 42 | ✅ 已闭合（S3-1）：上传页 `waitForOcr` 已改调 `GET /invoices/{id}/ocr-status` 并显示进度百分比 | — | — |
| 43 | 批量上传完不自动跳详情 | 任务卡写"上传完成跳 /invoices/{id}"；20 个文件跳走会丢进度 | 逐行给"查看详情"链接；单文件也不强制跳，S2-11 详情页可用后再评估 |
| 44 | ✅ 已闭合（S3-5）：`GET /invoices/{id}/image` 已实现（明文仅本地，密文/prod 409 待 S4） | — | — |
| 45 | 详情页影像可见，但**仅限本地明文调试**（`STORAGE_ALLOW_PLAINTEXT=true` 且 `is_cipher=false`） | S4 信封未接入；生产/密文下接口返回 409，页面显示"待 S4" | S4-4/S4-6 接入后在此处解密；论文如实写"影像加密链路未完成前仅本地演示" |
| 46 | 字段编辑只改值，不提供冲突候选选择/批量确认 | S3-6 补了逐字段“确认”与整单确认；**冲突候选选择 UI 仍未做**（S3-5 只做了画框） | 候选选择留 S5/S7 前端打磨；当前冲突需手工改值后确认 |
| 47 | 字段中文标签/票种/状态文案维护在前端 | 后端没有字典接口（API.md 未定义） | 新增字段时同步 `web/src/utils/format.ts` 与后端 `INVOICE_FIELD_NAMES` |
| 48 | ✅ 已闭合（S4-7）：`anchor_audit_task` 每日真实复算审计链（断链记 error），不写审计行、不发明锚定表 | — | — |
| 49 | 成功执行后锁保留到 TTL（默认 1h），失败才释放 | 防止同一 tick 多副本重复执行；TTL 必须小于调度周期 | 默认每日任务下 1h 足够；若将来改高频 cron，必须同步调小 TTL |
| 52 | ✅ 已闭合（S3-4）：`ExtractedField.raw_value` + `field_value` 存票面原文、`normalized_value` 存归一值 | 推导值（`derived/paired`）没有单一原文，回退归一值 | — |
| 53 | 置信度阈值/权重初值来自任务卡（0.90/0.60、0.35/0.65），**未用真实分布校准** | 40 张合成集上 review 占 52%（110/213），队列偏长；权重调参只能靠经验 | S6 用真实票据的"低置信字段是否真错"分布回填阈值与权重（`confidence_eval.py` 可直接复用）；当前口径已记 D-058 |
| 54 | `field_reread` 审计行仍未写 | ✅ 链已就绪（S4-7）；行接入随 S5 的 reocr 路径 | S5 在 `run_reocr` 同事务调 `append_audit`（D-040） |
| 55 | `reocr` 契约只允许"本人"触发，reviewer 在核对台不能直接重识别 | API.md §2 原文如此；擅自放宽权限是安全变更 | 与 S3-6 的确认权限一起定；届时改契约 + 测试 |
| 56 | 任务卡的"整块区域重识别"接口未做 | API.md 没有对应入口，`reocr` 只覆盖按字段 | S3-5 画框时若需要再补契约（bbox 入参 + 候选返回） |
| 57 | 旧版本写入的 `invoice_fields.bbox_json` 是校正图坐标 | 本卡起新识别写原图坐标；本地旧测试票无字段行，不受影响 | 生产若已有旧数据，需一次性重跑识别或写脚本按 `to_original` 迁移（当前无旧数据，不写脚本） |
| 58 | S3-3 之前写入的 `invoice_fields.field_value` 是归一值而非票面原文 | 本卡起新识别按契约写原文；旧行不自动迁移（本地无字段行） | 生产如有旧数据需重跑识别；如需保留人工修正值则需逐行迁移脚本（当前无需求） |
| 59 | 详情页红底只覆盖"冲突候选 / manual 档"，**没有校验器错误文案**（如金额≠金额+税额） | 校验错误在确认动作（S3-6）才算，避免两处规则漂移 | S3-6 confirmation 接口返回 `FieldError{field,code,message}` 后接到详情页 |
| 60 | 行框接口 `GET /invoices/{id}/lines` 是本卡**新增契约** | API.md 原先没有行级读取接口；"点字定位字段"需要它 | 已写入 API.md §2；若评审认为多余可只保留字段框（前端失败自动降级） |
| 61 | `confirm_field` 审计行仍未写 | ✅ 链已就绪（S4-7）；行接入随 S5 字段修改接口 | S5 与 #27 一起补 |
| 62 | confirm 只有"本人"入口，reviewer 不能确认/反确认 | 契约如此；`confirmed` 后无回退路径（S5 才允许 void/审核） | S5 审核流再定 reviewer 复核与反确认；论文写明这是刻意的审计约束 |
| 63 | 逐字段“确认”按钮只做了 typecheck/build，未做浏览器点击自动化 | 它走的是已被 `test_confirm` 覆盖的 `PATCH /fields confirm=true` 路径 | 若后续引入前端测试框架（遗留 #40），补一条点击用例 |
| 64 | 重复票只能"发现"，不能跨用户合并/作废 | 本次只做检测 + confirm 阻断；作废是本人/管理动作 | S5/S6 审核流再定合并或作废入口；当前提示用户自行处理原票 |
| 65 | 弱重复只提示，没有"标记为不重复"的留痕 | 任务卡要求不阻断；reviewer 需人工判断 | S5 报销审核时加"忽略弱重复"审计动作（需扩展 `audit_action` 枚举） |
| 66 | 批量上传页的 30 文件浏览器点击未自动化 | 接口与服务层已测（30 文件验收）；前端只做 typecheck/build + 单文件路径沿用 S2-10 冒烟 | 若引入前端测试框架（#40），补一条 30 文件批量用例 |
| 67 | 排队作业靠 `after_job_end` + GET 轮询补位 | worker 崩溃时排队项会等下一次轮询/重启；无独立调度器 | 若将来上多 worker/更高并发，改为 Redis 队列 + 独立 dispatcher |
| 68 | 复核后不可退回（刻意） | 要改只能走 S5 报销单驳回/作废；无 `unreview` 接口 | S5 设计驳回时再定是否允许票据回到 draft |
| 69 | 审核人二审前改字段会触发 `extract_status=need_confirm` | 状态机仍是 confirmed；复核闸门按库内值重跑 | 若希望"二审前修改自动回到待确认列表"，需另定状态语义（当前不混用） |
| 50 | API.md 仍保留 S3/S4/S5 未实现接口的标注 | 对账只保证"已实现 → 有文档"这一个方向 | 后续任务把对应接口实现后，更新 API.md 的实现标记与测试覆盖 |
| 51 | 界面仍是第一版视觉，没有设计稿/完整设计系统 | 任务卡没有美化项，先保证主流程与角色落地 | S3-5 会重做详情工作区；其余页面按反馈继续打磨（颜色/密度/品牌素材） |
| 52 | 登录页品牌区是文字 + 字母 S 占位 | 没有校徽/系统 Logo 素材 | 答辩前可替换为学校/项目 Logo；布局不需要改 |
| 53 | 管理端模板页没有删除/启用切换 | 后端没有 DELETE 模板接口；启用通过升版本 + active 控制 | 如确需删除旧版本，先补后端接口与权限；当前保留历史版本符合审计需要 |
| 54 | 不支持增值税专用发票/数电专票、汽车票、过路费、财政电子票据 | 方案明确 5 类范围，unknown 只做低置信兜底 | 真实样本若出现这些票种，先扩 SCHEMA + 模板 + 样本，再开放管理端新建 |
| 70 | `hmac_sm3` 未实现（AGENTS §10.4 的对外清单里有它） | 本卡只做摘要；Encrypt-then-MAC 属 S4-3 | S4-3 在 `crypto/sm/sm3.py` 补 `hmac_sm3(key, data)`（SM3 块长 64B）并加国标/篡改用例 |
| 71 | `canonical_json` 拒绝 `|int| > 2^53-1` | 业务载荷若把 BIGINT id 当裸整数会抛 `CanonicalJSONError` | S5 组装签名载荷时 id/金额一律用 `str`；若确需大整数，先改 D-074 与 Node 参考实现（BigInt） |
| 72 | 跨语言校验靠命令行脚本，不在 pytest 里 | `pytest` 不依赖 Node 运行时（测试套件禁止隐式外部依赖） | 若 CI 有 Node，可加一条 `@pytest.mark.skipif(shutil.which("node") is None)` 的对照用例 |
| 73 | `sm3_hash` 返回 hex 再 `bytes.fromhex`，多一次解码（≈几 µs） | 不影响签名链路预算（2MB 影像的瓶颈在 SM4，不在摘要） | S6-3 性能实验里如实记录单次 SM3 耗时；如成为瓶颈再评估直接取内部 state |
| 74 | 签名固定使用国标默认 ID `1234567812345678`（ZA 全用户相同） | 身份区分靠 `cert_id`（S4-6）与记录里的公钥，不靠 ZA | 若要求"按用户 ID 绑定"，签名/验签必须同期改造并记决策（D-075 已写明） |
| 75 | `sm2.sign_bytes` 暴露 `k_hex`（只为国标向量/确定性测试） | 生产误传会复用 K → 私钥可恢复（SM2 与 ECDSA 同理） | S5 只调 `sign_canonical`（无 k 参数）；上线前可加"业务层禁止直接 import sm2"的检查 |
| 76 | `VerifyResult` 目前不含 `cert_invalid` | 证书链/吊销校验属 S4-6 | S4-6 完成后 `verify_record` 接入证书校验并补该取值 |
| 77 | SM2 性能未测（gmssl 纯 Python 点运算，无常数时间保证） | 论文性能与侧信道分析靠 S6-3 / S6-4 | S6-3 补 sign/verify p50/p95；S6-4 如实写"无常数时间实现" |
| 78 | 对称层未做流式加密（一次性读入内存） | 票据影像上限 20MB，可接受；2MB 目标在 S4-4 验收 | 若将来支持大文件/视频，评估分块 + 分段 MAC |
| 79 | `mac_key = SM3(key‖"mac")` 是简易 KDF | 不是 HKDF，但独立于加密 key，满足 EtM 分离 | S4-9 的 AES-GCM/RSA 对照里可并列 HKDF 版本，论文写取舍 |
| 80 | HMAC-SM3 无国标向量（HMAC 非国标定义内容） | 用 OpenSSL 3 交叉向量替代，测试注释已写明出处 | 保持"不冒充国标"的写法；S6-4 可补第三方实现交叉验证 |
| 81 | CBC 解密失败仍会执行一次 CBC 运算（MAC 通过后才解密） | 攻击者无 key 无法构造 MAC 合法密文，不构成 padding oracle | 论文安全分析里写明"先验 MAC"的因果 |
| 82 | ✅ **已闭合（S4-4）**：SM4 换 `cryptography`（OpenSSL）、SM3/HMAC 换 `hashlib`（OpenSSL）；2MB seal+open **0.095s** | 纯 Python 往返 28.1s 的偏差已消除；gmssl 保留为 SM3 回退 | 保持 `cryptography==50.0.2` 锁版本；若平台 OpenSSL 缺 SM4 启动即报错 |
| 83 | 对称层加密一次性读入内存（无流式） | 20MB 上限下内存峰值约 3 倍明文，可接受 | 若 S4-4 换 OpenSSL 后端，可顺带评估分块 |
| 84 | SM2 加密是"gmssl 点运算 + 自写 KDF"组合（gmssl 的 `encrypt` 无法注入 K 且用 `random`） | 组合代码需在升级 gmssl 时回归（测试已覆盖往返/互操作/边界） | gmssl 升级前先跑 `test_sm2.py`；S4-9 的 RSA-OAEP 对照可佐证流程等价 |
| 85 | 2MB <1s 的性能断言在极慢机器/CI 上可能抖动 | 实测 0.095s，约 10 倍余量 | 若 CI 抖动，改为基准脚本 + 宽松阈值，但论文数字用手测实测值 |
| 86 | ✅ **已解决（2026-11-21，D-078）**：本机没有 `openssl` 命令行 | S4-5 密钥生成、S4-6 证书都要求 OpenSSL CLI | 统一用 `scripts/openssl.sh`（Docker 内 OpenSSL 3.5.9），已实测 SM2 密钥/CA/证书/签名验签；任务卡流程不改成 `_kg` 或自写 PKCS#8 |
| 87 | 信封只到"字节 → Envelope 记录"，大影像的 MinIO 对象名（`ciphertext_storage_key`）未做 | 属业务层（S5）与存储层 | S5 封装接口时决定：小载荷进 `ciphertext_b64`、影像密文存 MinIO 并登记对象名 |
| 88 | **OpenSSL 的 `pkeyutl` 默认 SM2 ID 为空**：直接 `-verify -rawin -digest sm3` 验不过我们的签名 | 互操作联调容易误判为"签名错" | 交叉验签必须显式 `-pkeyopt distid:1234567812345678`（已实测）；写进 S4-6/S5 验收与论文"互操作"一节 |
| 89 | `key_records` 未落库（本卡只产出加密文件与公开元数据） | S5 的密钥初始化流程才能登记；SCHEMA 该表无 `key_fp` 列 | S5 封装/签名接口按 SCHEMA 写 `storage_path`；`key_fp` 留在加密文件里 |
| 90 | 无私钥轮换、无 HSM、无密钥吊销流程 | 单机口令保护，防的是"库/备份泄露"而非"主机被控" | 论文"不足"如实写；轮换/吊销属 S5/S7 |
| 91 | `certificates` 表未落库（本卡只产出证书文件 + 校验函数） | S5 封装/签名接口才能登记；SCHEMA 无 role 列（角色在 subject OU 里） | S5 按 SCHEMA 写 `cert_pem/serial_no/fingerprint_sha256/not_before/not_after/public_key_pem`；角色从 OU 取，不新增列 |
| 92 | **不做证书链验签、无 CRL/OCSP** | 单 CA 拓扑下只信本地登记表；多级 CA/跨机构互信不适用 | 部署期用 `bash scripts/ca_init.sh verify <cert>` 做一次链校验；论文"安全边界"如实写（D-080） |
| 93 | **CA 私钥未做口令保护**（`secrets/ca/ca.key.pem` 明文，仅 chmod 600） | 拿到主机即可重签任意证书；与用户私钥的 PBKDF2 保护不对等 | 演示可接受；生产应离线保存/HSM；若要口令保护可复用 keystore 的包裹逻辑（S5+） |
| 94 | 角色来自证书的 OU | 改角色必须重签证书；没有"角色变更"流程 | S5 管理动作：重签 + 旧证书置 revoked |
| 95 | 证书有效期 10 年、无自动轮换 | 与任务卡"演示周期内不过期"的取舍一致 | 生产应缩短并做轮换；论文写明 |
| 96 | **业务动作的审计行尚未接入**（登录/登出/越权/字段确认/重读/复核等） | 链目前只有测试行；演示时 `verify-chain` 可能显示 0 条 | S5 各接口在同一事务里调 `append_audit`（#21/#27/#54/#61 已重定向） |
| 97 | `verify_chain` 是全表 O(n) 扫描 | 当前数据量下毫秒级；十万行以上会变慢 | 需要增量校验/分段锚时再评估（S7）；论文如实写 O(n) |
| 98 | 没有外部锚（时间戳/公证） | 删掉整条链无法发现；"每日锚定"只是再复算一次 | 生产可接可信时间戳；论文"安全边界"如实写 |
| 99 | **审计 HMAC key 轮换无方案** | 换 `AUDIT_HMAC_KEY` 会让历史链全部验不过 | 生产需要每行记录 key_id + 多 key 验证；本期不做，论文写明 |

## 变更日志

### 2026-11-21

- **S4-8 完成**：密码模块单测与国标向量收口。
  · 集中向量 `test_vectors.py`：SM3（GB/T 32905 附录 A 三条）、SM4（GB/T 32907 A.1 单块）、
    SM2（GB/T 32918.5 A.1 固定 k 复现 r/s）、HMAC-SM3（明确标注"OpenSSL 交叉向量，非国标"）、
    canonical 字节向量；
  · 端到端 `test_pipeline.py`：canonical → SM3 → 签名 → 信封 → 解封 → 验签 → 摘要比对，
    **7 类 1 bit 篡改**全部断言具体错误归类；
  · 跨实现：`scripts/crypto_cross_check.py` 用 OpenSSL 3.5 验证我们的签名 →
    `Signature Verified Successfully`（显式 distid）；
  · 覆盖率：`app/crypto` **94%**（760 行缺 43），certs 98% / keystore 99%；新增测试期依赖
    `pytest-cov==7.1.0` + `coverage==7.17.4`（D-082）；
  · 单测 703 / 集成 259 全绿。

- **S4-7 完成**：审计日志哈希链。
  · `services/audit.py`：`entry_hash = HMAC-SM3(key, canonical_json({prev_hash, ts, actor_id,
    actor_role, action, target_type, target_id, payload_sha256}))`；`payload_sha256` 从
    `detail_json` 重算（不落列）；创世 `prev_hash=0×64`；写入事务内 `pg_advisory_xact_lock(8071701)`
    后取链尾，保证并发不分叉；
  · `GET /admin/audit`（筛选/分页）与 `GET /admin/audit/verify-chain`（返回首个断点
    `broken_at/broken_id/reason`），仅 admin；新增必填配置 `AUDIT_HMAC_KEY`；
  · `scripts/verify_chain.py` 命令行校验（断链退出码 1）；`anchor_audit_task` 由占位改为
    **每日真实复算**（不写审计行、不发明锚定表）；
  · 实测：3 条链正常；改 detail/entry_hash/删行都报正确断点；**并发 50 条仍是单链**；
    HTTP 权限与 422 边界齐全；单测 667、集成 259 全绿。

- **S4-6 完成**：X.509 自建 CA 与角色证书。
  · `scripts/ca_init.sh`：`init` 建 CA（SM2-SM3，CA:TRUE + keyCertSign/cRLSign）、
    `issue <name> <role>` 签发用户证书（KeyUsage DS/NR/KE + EKU clientAuth + SKI/AKI，
    subject `CN=name/O=学校/OU=role`）、`show` / `verify`；
  · **私钥不落盘**：`unlock_key.py` 把 `.p8.enc` 解锁成 PKCS#8 PEM 只写 stdout，
    `ca_init.sh` 用 `openssl req -new -key /dev/stdin` 直接做 CSR，没有临时密钥文件；
  · `crypto/certs.py`：运行期只做指纹/有效期/公钥一致性/吊销/角色五项，**不做链验签**
    （单 CA 拓扑，部署期用 `openssl verify` 做一次，D-080）；SM2 公钥从证书 DER 的 SPKI 取
    （cryptography 不支持 SM2 公钥）；
  · 夹具：`backend/tests/test_crypto/fixtures/` 放预生成的**公开证书**（CA/alice/bob），测试不依赖 Docker；
  · 实测：`openssl verify` OK；`x509 -text` 的 KeyUsage/EKU/签名算法（sm2sign-with-SM3）齐全；
    签发后公钥一致性自检通过；`pytest tests/test_crypto` 220 passed、全量 646 passed。

- **S4-5 完成**：SM2 密钥生成与私钥口令保护。
  · 密钥**由 OpenSSL 3.5 生成**（`scripts/openssl.sh`，Docker 内，D-078）：
    `genpkey -algorithm SM2` → PKCS#8；`pkey -pubout` 与 `dG` 推导交叉核对；
  · `keystore.py`：PBKDF2-HMAC-SHA256（≥200000、16B 盐）→ 32B KEK → AES-256-GCM 包裹 PKCS#8 DER；
    AAD 绑定 `{algo, curve, kdf, iterations, key_fp}`（调低迭代/换算法即解密失败）；
    解锁失败统一 `KeystoreError("口令错误")`（不区分错口令与密文篡改）；
  · PKCS#8 解析用 pycryptodomex 的 DER 解码原语（不手写 ASN.1）；`key_fp = SHA-256(0x04‖X‖Y)`；
  · `scripts/keygen.py`：生成 → 加锁 → 解锁 → **签名自检**；临时 PEM `finally` 删除；
    口令只从环境变量/`getpass` 读（不进 shell 历史），输出绝不含口令与私钥；
  · 实测：产物只有 `sign.p8.enc`（498B JSON 密文）与 `sign.pub.pem`；
    **OpenSSL 独立验签通过**（`pkeyutl -verify -rawin -digest sm3 -pkeyopt distid:1234567812345678`
    → Signature Verified Successfully；不传 distid 会失败——OpenSSL 默认 ID 为空，已记遗留）；
  · 无接口/无迁移/无表变更（`key_records` 落库属 S5）；全量 631 passed（D-079）。

### 2026-11-20

- **S4-4 完成**：数字信封（SM2 包裹 CEK + SM4-CBC EtM）。
  · 性能改造（任务卡 <1s 的硬要求）：SM3/HMAC 走 `hashlib`（OpenSSL，2MB 0.008s，gmssl 回退），
    SM4-CBC 走 `cryptography`（OpenSSL，2MB 0.03s）；纯 Python 版本实测 28.1s，差两个数量级；
  · `sm2.py` 补 `encrypt_bytes/decrypt_bytes`：C1C3C2、C1 不带 04 前缀、只加密 1–32B 会话密钥、
    K 用 `secrets`（gmssl 的 `encrypt` 用 `func.random_hex` 且无法注入 K），KDF 用 SM3 计数器模式；
  · `envelope.py`：CEK 每封信封新生成；MAC 覆盖 `header‖wrapped_key‖IV‖密文‖证书指纹`
    （比 SCHEMA 多绑 wrapped_key，用来发现"只改 C3 段"的篡改）；拆封顺序 解 CEK → 验 MAC 解密 → sha256；
  · 实测：**2MB seal+open 0.095s**（254KB 真实票据 22.4ms）；与 gmssl 的 SM2 加解密双向互操作；
  · 无接口/无迁移/无表变更；`pytest tests/test_crypto` 181 passed、全量 607 passed（D-077）。

- **S4-3 完成**：SM4-CBC + HMAC-SM3 的 Encrypt-then-MAC 对称层。
  · `sm3.py` 补 `hmac_sm3`（RFC 2104，块长 64B，key>64B 先哈希）——AGENTS §10.4 清单里一直缺的那个；
  · `sm4.py`：PKCS7 + CBC + 随机 IV（`secrets`）+ `mac_key = SM3(key‖"mac")` +
    `mac = HMAC-SM3(mac_key, aad‖IV‖密文)`；解密严格按"验 MAC → 解密 → 严格去填充"；
  · 自写 CBC 链接与 PKCS7 的原因：gmssl `crypt_cbc` 无条件填充，且
    `pkcs7_unpadding = data[:-data[-1]]`（`gmssl/func.py:16`）完全不校验，满足不了"填充失败抛异常"；
    SM4 分组本体仍用 gmssl（国标向量证明）；
  · 实测：GB/T 32907 A.1 向量通过；固定 IV 的 CBC 与 Node/OpenSSL `sm4-cbc` 逐字节一致；
    HMAC-SM3 4 组向量与 Node `createHmac('sm3')` 一致；
  · 无接口/无迁移/无表变更；`pytest tests/test_crypto` 146 passed、全量 572 passed（D-076）。

- **S4-2 完成**：SM2 签名与验签（签名链路打通）。
  · `crypto/sm/sm2.py`：唯一入口 `sign_bytes` / `verify_bytes`，**只走 `sign_with_sm3`**
    （`e = SM3(ZA‖M)`，国标 ZA 绑定），不签外部摘要；
  · 随机源：`secrets.token_hex(32)` + 拒绝采样进 `[1, n-1]`，**绝不用 `func.random_hex`**；
  · 签名值 DER(SEQUENCE{r,s}) hex，记录里存 base64（对齐 SCHEMA "约 70–72 字节"）；
  · 处理 gmssl 七条坑：`mode` 默认 0、`lstrip("04")` 按字符集剥离（x 以 04 开头时 128→126 实测）、
    `sign()` 不重试、verify 不查 r/s 范围（`s+n` 可塑）、畸形签名抛异常、固定默认 ID；
  · `crypto/signature.py`：`SignatureRecord`（frozen/extra=forbid，`signed_at` 必须带时区）+
    `VerifyResult`（ok/digest_mismatch/bad_signature）+ sign/verify 两个链路函数；
  · 实测：GB/T 32918.5 A.1 公开向量 `r=F5A03B06…`、`s=B1B6AA29…` 逐位一致；
    双重哈希陷阱用例（摘要当消息 → 原消息验不过）固化为测试；
  · 无接口/无迁移/无表变更；`pytest tests/test_crypto` 105 passed、全量 531 passed（D-075）。

- **S4-1 完成**：SM3 摘要与 canonical JSON 规范化（密码链路起点）。
  · `crypto/canonical.py`：键按 Unicode 码点升序、`(",", ":")` 无空格、`ensure_ascii=False` + UTF-8；
    **拒 float/Decimal**（金额先格式化成字符串）、拒非 str 键、拒 `|int|>2^53-1`（JS `Number` 精度）、
    拒 `bytes/tuple/set/自定义对象`、循环引用、孤立代理项；`canonical_sha256` 仅作对照；
  · `crypto/sm/sm3.py`：`sm3_digest` 恒 32 字节、`sm3_hex` 仅日志；封装 gmssl 的
    **原地修改入参**（传 `list()` 副本）与 hex↔bytes 转换，业务不再直接 import gmssl；
  · 依赖：`gmssl==3.2.2`（+ 传递依赖 `pycryptodomex==3.24.2`）装并锁进 requirements/lock；
    装入后 numpy 1.26.4 / paddle 2.6.2 / paddleocr 2.7.3 未变，全量单测 426 → **480 passed**；
  · 实测：SM3 国标向量（`abc`/512bit/空串）全过；`100`/`"100"`/`"100.00"` 三种字节三种摘要；
    跨语言 `canonical_cross_check.{py,mjs}` 三字段逐字相等（`sm3=ae5c73b0…`），
    而 JS 默认 UTF-16 排序会得到 `1504e4f3…`（fixture 含 `～`/`😀` 键，专门抓这个坑）；
  · 无接口/无迁移/无表变更；`alembic check` 零差异（D-073/D-074）。

### 2026-11-19

- **票种范围确认**：维持 5 类真实票据 + `unknown` 兜底；管理端已移除「新建模板」入口，日常只做升版本；
  新增票种需先扩展 SCHEMA 枚举/迁移/模板/样本（D-054）。
- **内容页宽度修正（用户反馈）**：页面根 `el-space` 默认 `inline-flex` 导致右侧空 226px；
  统一 `width:100%` 并去掉 1280 上限，1440/1920 下内容页均撑满（D-053）。
- **管理端模板页补充（用户反馈）**：新增 `/templates` 管理页（列表/详情抽屉/JSON 规格/规则编辑/升版本/试跑），
  规则随模板维护，`/rules` 重定向；root 登录可管理 5 个 seed 模板，试跑走真 OCR。
  `typecheck`/`build` 通过（D-052）。
- **UI 整理（S2 后，用户反馈）**：登录页分栏品牌化、深色侧栏、按角色落地业务页、移除概览/联调菜单、
  统一卡片/表格/空态与页面标题；不改业务逻辑、不加依赖。实测：typecheck/build 通过；三角色登录分别落到
  票据列表/票据列表/票种模板；1440px 无横向溢出；上传页拖拽区正常（D-051）。
- **S2-12 完成**：定时任务骨架与阶段收口（S2 阶段结束）。
  · APScheduler 3.11.1 随 API lifespan 启动，注册每日 02:00（Asia/Shanghai）锚定任务；
  · 多副本：任务开始抢 Redis `SET NX EX`，**成功后锁保留到 TTL**（时段去重标记），失败释放 + Lua token 校验；
  · 双进程实测：`*/10`、TTL=5，4 个 tick 共执行 4 次、另一侧 4 次 skipped，证明同 tick 只跑一次；
  · 配置全部进 `Settings`（开关/时区/cron/锁 key/TTL），测试默认关；
  · 文档对账：新增 `test_api_docs.py`，OpenAPI 实际路由逐条能在 API.md 找到；
  · 单测 341 / 集成 146 / smoke 9 OK；`alembic check` 零差异。
- **S2-11 完成**：前端票据列表与详情。
  · 列表：状态/抽取状态/票种/关键字/日期区间/排序 + 分页；筛选以 `route.query` 为唯一状态源（刷新/后退不丢）；
  · 详情：票据头信息、字段表（置信度<85% 标红、待确认/冲突）、草稿行内编辑（`version` 乐观锁、
    reviewer 改金额需 `confirm_amount`）、`40900` 自动刷新、`42201` 保留输入并提示；
  · 影像预览按 D-049 占位"待 S3-5（加密信封落地后）"，不临时开明文接口；
  · 后端补漏：`InvoiceListQuery.invoice_type` 加 `vat_digital` + 筛选用例；
  · 实测：`typecheck`/`build` 通过；列表→详情→改 buyer_name→保存→刷新可见（DB `source=manual`、version 0→1）；
    reviewer 菜单"票据查询"可看全量；URL 筛选刷新保持；`smoke.py` 9 OK。
- **S2-10 完成**：前端上传页。
  · `Upload.vue`：拖拽/多选（≤20）、缩略图、本地校验（JPG/PNG/PDF、≤20MB）、串行上传、
    单文件与总进度、逐文件状态；409 去重走**绿色提示 + 查看链接**（请求 `silent`，不弹全局红条）；
  · `api/files.ts`（FormData + 上传进度）、`api/invoices.ts`（`waitForOcr`：3s 起指数退避、上限 15s、总 120s）、
    `types/invoice.ts`；
  · 轮询用 `GET /invoices/{id}` 的 `ocr_status`/`latest_job`（`ocr-status` 专属接口待 S3-1，D-048）；
  · 修复 Vue 响应式 bug：push 后改原始 task 对象导致界面永远"上传中 0%"，改为操作数组代理；
  · 实测：真实上传 JPEG → 行显示"识别失败：TaskNotReadyError: OCR 流水线未接入（S3-1）"；
    重复上传 → "已上传 + 查看链接"；`.txt` → 本地拒绝；`smoke.py` **9 OK / 0 FAIL**。
- **S2-9 完成**：前端登录与主框架。
  · `api/http.ts`：401 自动 refresh **一次**（并发复用同一个 Promise，轮换式 token 不会互相失效），
    失败才清会话跳登录（提示 1.5s 节流）；不解析 JWT exp，只信 401；
  · `stores/user.ts`：access/refresh/user 持久化、`role`/`permissions`/`hasRole`/`setSession`；
  · `router`：`meta.roles` 生效、刷新只剩 token 时 `/auth/me` 水合、无权限 → `/403`；
  · `MainLayout`：三角色菜单（我的票据/上传；待核对/待审核；模板/规则/证书/审计），未实现页明确占位；
  · `Login.vue`：Element Plus 表单 + 前端校验 + 真实登录；
  · `scripts/ui_smoke.mjs` 增加 `--fill`/`--clear`，支持真实登录冒烟；
  · 实测：`npm run typecheck` / `npm run build` 通过；三角色登录菜单不同；admin → `/upload` 得 `/403`；
    `ACCESS_TOKEN_TTL_SECONDS=1` 下并发两个 `/auth/me` 只刷新 **1** 次且都成功；`smoke.py` **9 OK / 0 FAIL**。
- **S2-8 完成**：票种模板与规则管理。
  · 契约补 `vat_digital`（SCHEMA §9.6 + enum + `0003` 迁移只放开两个 CHECK）；
  · `GET/POST/PUT /templates` + `/templates/{id}/test`：写仅 admin、reviewer 只读、submitter 403；
  · 版本只增不改（PUT 生成 v+1 新行、旧行保留、规则复制+按 field 覆盖）；同票种 active 互斥；
  · `TemplateRegistry` 60s 缓存，写后立即失效；`/test` 复用 S1 extractor（OCR 可注入），不建票、不落 MinIO；
  · seed 用幂等脚本（5 模板 / 37 规则），已有数据不覆盖；
  · 顺手修 bug：`extractor` 用 `findall` 遇到带捕获组的正则会返回 tuple 并崩，改为 `finditer+group(0)`；
  · 实测：`pytest 332 passed`、`pytest -m integration 146 passed`；真实 OCR `/test` 抽 10 字段、conf 0.881；
    smoke 8 OK。
- **S2-7 完成**：ARQ 异步任务框架。
  · `job_service`：`pending→running→succeeded|failed` 单入口；可重试失败回 `pending`；
    幂等双保险（终态跳过 + Redis `SET NX EX 600`）；退避 5s/30s；永久错误直接 failed；
  · `tasks/worker.py`：`WorkerSettings`（`max_tries=3`、`job_timeout=180`、启动恢复僵尸作业），
    注册 `ocr_task` + `anchor_audit_task`（S4-7 占位）；
  · 上传提交后入队；入队失败把 job 置 `failed` 并返回 503（不返回假成功）；
  · `ocr_task` 在 S3-1 前**如实失败**（`TaskNotReadyError`），不伪造 succeeded；
  · 依赖：`arq==0.28.0` 强制 `redis<6`，redis-py 8.1.0 → **5.3.1**（服务端 Redis 7 不变，D-045）；
  · 实测：`pytest 323 passed`、`pytest -m integration 137 passed`；真实 ARQ worker + uvicorn：
    上传 202 → `ocr_jobs failed / retry_count=1 / TaskNotReadyError(S3-1)`；smoke 8 OK。
- **S2-6 完成**：文件上传 + MinIO + sha256 去重。
  · `POST /invoices/upload`：分块 sha256 → 魔数/Pillow/pypdf 校验 → **去重先于写对象** →
    MinIO（`invoices/yyyy/mm/<32hex>.bin`）→ 同一事务写 file+invoice+job（pending）→ 202；
  · 去重命中 409/40901 且 `data.invoice_id` 指向既有票据，MinIO 不新增对象；
  · 落库失败/并发同 sha 补偿删除对象；文件名清洗（路径穿越不进入对象名）；
  · S4 前本地明文调试（`is_cipher=false`，prod 启动拒绝，D-044）；ARQ 入队留 S2-7；
  · 新增依赖 `python-multipart==0.0.34`（FastAPI multipart 硬依赖），锁文件只多 1 行；
  · 实测：`pytest 311 passed`、`pytest -m integration 129 passed`；真实 uvicorn+MinIO 手工
    202 → 二次 409 → 对象 643B → 清理干净；smoke 8 OK / 0 FAIL。
- **S2-5 完成**：票据 CRUD、状态机与契约版字段修改。
  · `transition()` 单一状态机（D-011 的 5 值；非法迁移 409；`ocr_status` 不混入）；
  · 列表：复用 S2-3 隔离 + S2-4 分页；submitter 本人 / reviewer 全量 / admin 404；
    7 类筛选、默认排除 void、金额两位小数字符串、永不返回 `storage_key`；
  · 详情：`{invoice, fields, latest_job}`；软删 `void` 幂等、`in_report/archived` 409、
    void 释放 `(code,no)` 唯一索引后可重建；
  · 字段修改：乐观锁 + D-017 权限矩阵 + `confirm_amount`（42202）+ 交叉校验冲突回滚（42201），
    `source=manual`、`confirm` 控制 `need_review`；
  · 契约冲突处理：不新增 `POST /invoices`（建票走 S2-6）、不新增 `PATCH /invoices/{id}`
    （用契约的 `PATCH /{id}/fields`），任务卡状态图按 D-011 修正（D-043）；
  · 实测：`pytest 310 passed`、`pytest -m integration 118 passed`，`alembic check` 零差异。
- **S2-4 完成**：分页、筛选与排序协议。
  · `PageParams`（page≥1 / page_size 1–100 / 白名单 sort_by / order Literal）与 `PageResult`；
  · `paginate()`：同一 `Select` 上求 total（`order_by(None).subquery()`）再 offset/limit，
    从根上消除"列表与 total 两套 where"；排序加 `id asc` 兜底，翻页不重不漏；
  · `q` 的 LIKE 通配符转义（`escape_like`），用户输入 `%`/`_` 不再是通配符；
  · 关联字段用**显式 join**（不改 S2-1 的无 relationship 锚点），语句计数断言每页恰好 2 条 SELECT；
  · 5 万行基准（`scripts/bench_list.py`）：四组 p50/p95 = 13.4/15.9、20.1/25.4、19.3/21.9、27.6/32.3 ms，
    EXPLAIN 走 `Index Scan using ix_invoices_uploader_created`；
  · 实测：`pytest 310 passed`、`pytest -m integration 87 passed`，`alembic check` 零差异。
- **S2-3 完成**：行级数据隔离。
  · 统一入口 `services/repository.py`：`scope_for` / `filter_visible` / `select_visible` /
    `get_visible_or_404`，列表与详情共用同一谓词，杜绝"详情改了列表忘了"；
  · 规则：submitter 仅本人行；reviewer 票据/文件全量、报销单仅 `submitted/approved/rejected/archived`；
    admin 业务表一律 404；**未登记模型 `false()` 默认拒绝**；
  · 越权语义：对外统一 404/40400（不泄露存在性），内部区分 `NotFoundError` / `PermissionDeniedError`，
    为 S4-7 的 `action=forbidden` 审计留语义；
  · 决策 D-041：应用层过滤而非 RLS（连接池 + `SET LOCAL` 串身份风险、policy 复杂度、可测试性）；
  · 实测：`pytest 297 passed`、`pytest -m integration 74 passed`（隔离 23 条，含 8 个报销单状态参数化、
    四角色列表/详情一致性、HTTP 404 语义）；无库结构变更，`alembic check` 仍零差异。
- **S2-2 完成**：JWT 认证 + RBAC 三角色。
  · 依赖：`PyJWT==2.15.1` + `passlib[argon2]==1.7.4` + `argon2-cffi==25.1.0`（锁文件只新增 2 行）；
  · 令牌：HS256，access 30min / refresh 7d，载荷含 `typ`（防 refresh 当 access 用）；`jti` 走 `secrets`；
    解码显式 `algorithms=["HS256"]`，防 `alg=none` 与算法混淆；
  · 会话：refresh **轮换式** + Redis 白名单 `auth:refresh:{jti}`；登出删白名单；多端登录各自独立 `jti`；
  · 口令：Argon2id（m=64MiB / t=3 / p=4），实测 hash **59.4ms** / verify **45.7ms**；
    账号不存在时也跑一次 dummy verify，抹平枚举侧信道；
  · 错误语义：未知账号与错口令**同码同文案**（401/40101）；`disabled/locked` 登录 403/40301；
    角色级拒绝 403（新增 `RoleForbiddenError`，与资源级越权的 404 区分开）；
  · 契约冲突处理：register 按 API.md 开放（用户拍板）；审计动作补 `login_failed/logout/forbidden`
    到 SCHEMA + `0002` 迁移，但审计行留 S4-7（D-040）；
  · 实测：`pytest 288 passed`、`pytest -m integration 51 passed`、真实 uvicorn+Redis 手工链路通过、
    smoke 8 OK / 0 FAIL；测试数据与 Redis key 已清理。
- **S2-1 完成**：16 张表模型 + 首个 Alembic 迁移。
  · 依赖：`SQLAlchemy==2.0.58` + `alembic==1.20.1`；锁文件 diff 只有 4 行新增（alembic/greenlet/Mako/SQLAlchemy），既有版本零变动；
  · 模型：`models/{base,enums}.py` + 16 个一表一文件；枚举 `StrEnum` 单一来源、CHECK 文本由 `enum_check()` 拼；
    主键 `BIGINT GENERATED ALWAYS AS IDENTITY`、时间 `TIMESTAMPTZ + now()`、金额 `NUMERIC(12,2)`、JSONB 按契约；
  · 迁移：`0001_init.py` 由 autogenerate 起草后**逐条对照 SCHEMA 人工核对**；`alembic check` 零差异；
    `upgrade → downgrade base → upgrade` 往返通过，`\dt` = 16 业务表 + `alembic_version`；
  · 业务不变式下沉到库：发票号 `(invoice_code, invoice_no) WHERE status <> 'void'`、每单一条 active 签名、
    每票种一版 active 模板、文件/报销单各一个信封、`(invoice_id, field_name)` / `(invoice_id, page_no, line_no)` /
    `(form_id, invoice_id)` 复合唯一、`verified = (verify_result = 'ok')` 一致性；
  · 驱动：不装 psycopg2，`core/database.py` 把 `postgresql://` 映射成 `postgresql+psycopg://`（D-036）；
  · 实测：`pytest 264 passed`（新增 100 条单元 + 23 条集成）；`alembic check` 无差异；smoke 8 OK / 0 FAIL；
  · 与任务卡提示词的唯一差异：JSONB **不设** `'{}'::jsonb` 默认值（SCHEMA 未定义默认值，按契约优先；D-037）。

### 2026-11-18

- **S1 收尾补强第二轮**（D-035，用户反馈两件事：① 行程单 007 校正后很歪且字被切；② PDF 不需要预处理、照片拍不好应提示重拍）。
  · 根因不是"旋转裁切"，而是**四角点被检成错切四边形**：上下边 685 vs 1106（差 38%），
    旧的对边容差 60% 放它过去 → warp 出歪图并切掉边缘文字。收紧到 30%、内角收到 [55,125] 后走 `minAreaRect` 兜底，
    **1200×700 → 1200×700 零裁切**。
  · 透视 warp 前给四角点**外扩 2%**（安全边距）：四角点小 4% 就会切掉贴边的字。
  · 新增 `app/ocr/intake.py`：**按魔数**判 PDF/照片；PDF 只渲染（250 DPI）**跳过全部预处理**；
    照片走预处理，并给"拍前引导"（`PHOTO_GUIDE`）与"拍后重拍判断"（`needs_retake`+`retake_hints`，
    覆盖糊/暗/低对比/过曝/认不出边界/边缘贴画面）。重拍只是建议，不阻断流水线。
  · 指标同步改善：字段级 0.893→**0.895**、整单 0.633→**0.650**、检测 Hmean 0.948→**0.951**；
    demo 现在会报"建议重拍 18 张"（多是远景/贴边样本，符合预期）。
- **S1 收尾补强**（按"确保阶段一完成"的审计结果处理）。
  · **D-032 排除超市购物小票**：唯一真实样片 R-001 经确认是购物小票，无发票代码/号码、无税务监制章，
    不是合规报销凭证 → 移出数据集、不做该版式模板与评测；只保留像素统计作成像参照。
    系统范围明确为**五类合规票据**（数电票 / 旧版增值税电子普通发票 / 铁路电子客票 / 航空行程单 / 出租车票）。
  · **D-033 金额/税额约束反解**：旧版票竖屏截图没有"合计"行，金额税额散在相邻行 → 用
    "邻近两个金额之和 = 合计"反解（唯一解才采用、税额取较小者、置信度 0.6、`rule_id=paired:sum:*`、带 warning）。
    **amount 60.0% → 86.7%、tax_amount 56.7% → 83.3%、总体 85.6% → 89.3%**。
  · **D-034 去斜取舍**：把阈值降到 1.0° 并让兜底路径也去斜，实测字段准确率掉 3–5 点（细笔画被重采样糊掉）
    → 回退到 1.5°，并在代码注释里写明"为什么不再收紧"。用户判据"字看得清"已满足。
  · **整单正确率 0.500 → 0.633**（vat_general 0.35→0.75、vat_digital 0.70→0.70、taxi 仍 0.20）。
- **S1-6 完成（S1 阶段收口）**：端到端基线脚本与指标计算。
  · 指标全部在 `backend/eval/metrics.py`（纯函数 + 23 条手算单测），脚本只调指标 → **打分与产结果分离**；
  · 三个口径问题当场修掉：① 标注在原图、OCR 在校正图 → 用 `to_original` 逆矩阵映射（否则 Hmean 0.09）；
    ② 标注是元素级、输出是行级 → 保持元素级真值 + **覆盖匹配**（合并成行反而掉到 0.12）；
    ③ 报告里写明匹配口径，避免读者误以为是纯 IoU 匹配；
  · **关键发现（S6 的素材）**：预处理对"检测"是负收益（Hmean 0.974 → 0.948），对"字段"是正收益
    （0.847 → 0.856）。原因：合成图本来就没躺倒，透视重采样只有插值损失；而光照/背景归一化确实帮了字段抽取。
    **不能只报"预处理有用"**——这条要写进论文的消融讨论。
  · 瓶颈定位：整单 0.500 主要由 `taxi_ticket`（0.20）与 `vat_general`（0.35）拖累，
    与 S1-5 的 amount/tax 短板一致（小票字小、旧版票合计行被印章压住）。
- **S1-5 完成**：关键字锚定 + 规则字段抽取 + 校验器。
  · 13 条显式规则（anchors 顺序即优先级、direction、regex、max_distance、zone、value_index、occurrence、
    exclude、whole_line_anchors、if_multiple、tickets），**禁止"取最后一行金额"这类模糊规则**；
  · 四个实测踩出来的实现点：① 锚点匹配要**忽略 OCR 插入的空格**（"金 额""铁 路"）；
    ② 距离要按方向度量（"合计"与其右侧右对齐金额水平差 800px，曼哈顿距离会判成过远）；
    ③ 表头"项目名称"会把 seller_name 的"名称"锚点带跑，用 `exclude` 排除；
    ④ 千分位被读成空格（"¥1 547.37"）会把 1547.37 解析成 547.37，解析前先合并 3 位一组；
  · 校验器全部 Decimal：金额三件套（容差 0.01）、中文大写金额（容差 0）、日期、代码/号码位数、
    USCC 校验位（GB 32100）；失败把该字段置信度折半并把 errors 全量返回；
  · 冲突消解顺序：锚点优先级 > 显式规则（value_index/if_multiple）> 报冲突并保留 candidates（不静默替换）。
- **S1-4 完成**：票种识别与版面区域划分。
  · 5 个 YAML 模板（归一化区域 + 关键字权重 + 互斥词 + 长宽比先验）；分类打分为
    "关键字×标题加成(3×) − 互斥词 + 长宽比"，归一化 <0.25 或与第二名差距 <0.08 → **unknown**
    （宁可判不出，也不猜错——猜错会让整页字段用错模板）；
  · 标题常被检测切成 2–3 段，所以标题区所有行**拼接后再匹配一次**（否则"增值税电子普通发票"永远命不中）；
  · **区域按内容框归一化**（不是整图高度）：透视校正多框进 13% 桌面就会让 footer 永远落空；
  · 判票种依赖"标题读得出来"，于是暴露出 S1-2 的三个几何缺陷（D-029）：退化四边形（其实是三角形，
    面积 50%、角度正常，全部阈值都能过，warp 后 OCR 20 行 → **0 行**）、四角点自交、单边裁切。
    补上四项校验后：票种判对率 91.7% → **100%**，而 S1-2 的"几何校正成功率"从 85% 掉到 65%
    ——**这个下降是好事**，原来的 85% 里混着"warp 成功但把内容裁坏"的样本；
  · 顺带把检测改到 1000px 小图做、重采样用双线性：预处理 P50 77ms / P95 195ms（回到预算内）。
- **S1-3 完成**：PaddleOCR 检测识别最小闭环。
  · `OcrEngine` 进程内单例（懒加载 + 互斥锁，模型加载 5–10s 只付一次）；`parse_paddle_result` 把 2.7 的
    `[[[box,(text,score)]]]` 解析成契约结构，**按 y 分桶再按 x 排序**给可复现的 `line_no`；
    遇到 3.x 的 dict（`rec_texts/...`）**抛 `OcrContractError`**，绝不静默返回空；
  · `recognize_line()` 供 S3-3 局部二次识别复用；空裁剪返回 `("", 0.0)` 不抛异常；
  · `drop_score=0` 是刻意的：引擎层丢行会让低置信字段无从标记（S3-2 的队列会空）；
  · 依赖踩坑（D-027）：装 paddleocr 时 numpy 被 `scipy/scikit-image/pandas` 的最新版顶到 2.4.6，
    cv2 与 paddle 双双 ABI 报错 → 把这三个包压到 numpy 1.x 兼容线（1.11.4 / 0.21.0 / 2.1.4），
    并清掉误装的 `opencv-python-headless 5.1`；结论：**锁直接依赖不够，还要锁会把关键传递依赖顶走的包**；
  · 重要发现：S1-2 里判为"读不出"的远景小票，换成 PaddleOCR + 预处理后**完整读出 7 行**——
    之前的"不可用"是 Windows OCR 的能力上限，不是数据本身没信息。S6 的评测基线必须改用 PaddleOCR。
- **S1-2 完成**：图像预处理与透视校正。
  · `preprocess()` 全链路：EXIF 方向 → 缩放（检测用小图）→ 票据四角点（Otsu 亮区 + 边缘双路候选）→ 透视 /
    `minAreaRect` / 原图直送三档降级 → 小票放大 → 残差去斜 → 照度校正 → 印章标记（**不 inpaint**）；
  · 返回 `to_original`（3×3 单应矩阵，校正图 → 原图）：S1-3 在校正图检测，前端要在原图显示高亮框；
  · 三处实现修正（D-026，都是被"OCR 前后对比"逼出来的）：
    ① **原图重采样**（先缩放再裁切会把字缩小，某张票 OCR 命中率 56% → 0%）；
    ② **恒等快捷**（四角点≈图像四角时不做透视，避免插值把加粗数字抹花，100% → 67% → 恢复）；
    ③ **照度限幅**（目标取 p90、增益 0.75–1.6 且只施加 70%，否则深色背景压暗整图 / 浅灰小票被提亮成白纸）；
  · 依赖三角（D-025）：`opencv-python==4.6.0.66` 与 numpy 2 不兼容 → numpy 锁 **1.26.4**，同时满足 paddleocr 2.7.3 的 pin。
- **S1-1 完成（含一次范围修正）**：合成票据集与标注规范。
  · **修正**：真实样本 R-002 是**数电票**（20 位号码、无发票代码、无校验码、210×140mm），
    而首版模板做的是旧版增值税电子普通发票。按 D-023 新增 `vat_digital` 版式为主流票种，
    旧版降级为"历史形态"，合成集由 40 张扩为 **60 张**（6 版式 × 10 张，dev 36 / test 24）；
    数电票模板的版面几何取自真实 PDF 的**坐标量测**（只取相对位置，未读取字段值）。
  · 生成器 6 个模块：字体探测、字段值生成（金额大写 / 统一社会信用代码校验位 / 假身份证号）、
    版式原语（**绘制即记录坐标**）、5 种票面、扰动（背景桌面 + 几何 + 光度 + 印章/折痕）、CLI；
  · **几何扰动同步变换标注框**（单测钉死 90° 旋转的期望位置），这是"图歪框正"这类隐形 bug 的唯一防线；
  · 场景补了"贴到桌面"：纸面占画面 23%–100% 三档，才能真正考验 S1-2 的票据定位；
  · 真实样本 R-001 建档：像素统计定性为"手机拍摄、强红偏、纸面仅占 30% 的困难工况"，票种待人工确认，
    图片不入库、票面文字不进日志；
  · 验收做成 5 项程序化检查（不依赖人眼看图），并**在过程中修正了两处判据**：
    ① 渲染一致性用"局部自适应墨迹"而非全局暗像素（深色背景下随机框也"暗"，首版判据失效）；
    ② 可读性用"字段值命中率"而非整页字符相似度（键名表头不该进分母），并修掉归一化未处理全角标点的问题
       （OCR 把 `¥666.36` 读成 `¥ 666 ． 36`，导致金额字段被判为未读出）。
- **S0-7 完成**：环境冒烟与 README，**S0 阶段收口**（tag `m0-env-ready`）。
  · `scripts/smoke.py`：配置 → 端口 → PostgreSQL(版本/库/迁移) → Redis(PING/AOF) → MinIO(bucket) → 后端 /health → 前端(可跳过)；
    **区分"没起"与"配错"**（连接被拒 vs 认证失败给出完全不同的下一步）；任何失败都不吐 traceback；`--strict` 让 WARN 也失败；
    `-v` 打印实际地址且**对 DSN 口令打码**；
  · 迁移项刻意做成 WARN：S2-1 之前没有 `alembic_version` 表是正常的，S2-1 之后同一脚本自动升级为 FAIL（靠"有没有迁移文件"判断）；
  · README 补全：环境要求（Python **3.11** 的硬约束写在表里）、8 步快速开始（每步带验证命令与期望输出）、端口表、
    常用命令、目录结构、**Windows 特有问题 9 条**（paddle wheel、镜像拉取、命名卷、CRLF、端口占用、MSYS 缺 tee/nohup、
    Edge dump-dom、中文路径、控制台编码）、故障排查表（按冒烟输出对照）、已知限制；
  · DECISIONS D-022：基础设施组合 ADR（PG16 + Redis7 + MinIO + ARQ 的逐项理由与代价）。
- **S0-6 完成**：前端最小可运行。
  · 技术栈按 AGENTS 落地：Vue 3.5 / Vite 8 / TS 6 / Element Plus 2.14 / Pinia / Vue Router，`package.json` 全部精确版本；
  · `api/http.ts` 是唯一出口：注入 `Authorization`、统一解包 `{code,message,data,trace_id}`、401 清会话跳登录、
    403 提示、其余弹后端 message；错误归一成 `ApiError`，并**携带后端 payload**（否则 503 降级信息会被丢）；
  · 路由守卫：未登录跳 `/login?redirect=...`；`meta.roles` 先声明不生效（S2-3 实装，避免假的安全感）；
  · `/debug` 联调自检页：展示 health 三依赖、版本、trace_id（可复制）、原始响应壳，并能一键触发 404 验证错误路径；
  · 新增 `scripts/ui_smoke.mjs`：无头 Edge + CDP 读**渲染后**正文与控制台（Windows 版 Edge 的 `--dump-dom` 不输出到 stdout），
    支持 `--expect` / `--click` / `--eval` / `--wait`，供 S7 冷启动检查复用。
  · 过程中修掉两个问题：① 前端把 503 当纯错误丢掉 payload → 降级态显示成"未请求"，改为 `ApiError.payload` 透传；
    ② 冒烟脚本无 `--expect` 时 0.4s 就断言，早于异步请求返回 → 改为先固定宽限再轮询。
- **S0-5 完成**：后端最小可运行。
  · `create_app()` 工厂 + `Settings`（pydantic-settings，缺项启动即抛 `ConfigError` 并列出缺哪几项；prod 拒绝示例 JWT_SECRET）；
  · JSON 结构化日志（stdlib only，不引 loguru/structlog），`trace_id` 用 contextvar + 中间件注入，
    **同一条链路可 grep**：错误日志、访问日志、业务日志都带同一个 trace_id；
  · 统一错误壳 `{code,message,data,trace_id}`：404→40400、422→42200、未捕获→50000 且**堆栈只进日志**；
  · `/health` 与 `/api/v1/health` 同一实现，检查 DB/Redis/MinIO，任一挂了返回 **503 degraded**（探针可直接判活）；
  · 24 个单测覆盖：配置校验 7、健康检查 5、错误壳与 trace 12。
  · 修掉一个真 bug：未捕获异常时 `Exception` 处理器在最外层 ServerErrorMiddleware 里执行，
    此时 contextvar 已 reset → 日志和响应里的 trace_id 变成 `-`；改用 `request.state.trace_id` 传递。
- **S0-4 完成**：基础服务三件套起在本机。
  · 端口与已有 study-agent 错开：PG **5433** / Redis **6380** / MinIO **9002**（控制台 9003），全部只绑 `127.0.0.1`；
  · 数据全落命名卷（`study-ocr-pgdata/redisdata/miniodata`），不用相对路径绑定挂载（Windows 上必炸）；
  · `deploy/init/*.sql` 只在数据卷首次创建时执行，已确认 `pgcrypto` 装上（日志有 `[init] pgcrypto ready`）；
  · `minio-init` 一次性容器用 `mc mb --ignore-existing` 幂等建 bucket 并置 private，重复 `up -d` 不报错；
  · `scripts/check_services.py` 纯标准库（此时后端 venv 还没建）：compose 可用性 / PG `select 1` + pgcrypto /
    Redis AUTH+PING+appendonly / MinIO 健康探针 / MinIO 对象写入-读取-删除，**5 项全 OK**；
  · 镜像从 `pgvector/pgvector:pg16` 换成契约里的 `postgres:16`（重建后自检仍 5/5）；
  · 解决"Docker Hub 直连超时"：`docker pull <裸名>` 会回退到 registry-1 而失败，改用**镜像源全名 + retag**，
    封装为 `scripts/pull_image.sh`（实测 daocloud 源可用，已顺带拉好 S7-2 要用的 `nginx:alpine`）；
  · 决策记 D-019（端口错开 + 镜像拉取路线 + 离线兜底）。
- **S0-1 完成**：初始化 monorepo 骨架；`.gitignore` 覆盖 Python/Node/IDE/日志，并**强制排除** `secrets/`、`samples/` 影像与 `backend/eval/results/` 生成物；补 `.gitattributes`（统一 LF，避免 Windows CRLF 抖动）；README 写明环境要求（**必须 Python 3.11**）、目录说明、文档索引与启动步骤占位。
- **S0-2 完成**：`AGENTS.md` 落盘。除常规技术栈/规范/禁止事项外，把两条高压线写死——OCR（EXIF 先修、降级链、`unclip_ratio ≤ 1.8`、坐标与置信度不丢）与密码（只许 `secrets`、`mode=1`、`sign_with_sm3` 不许混用、Encrypt-then-MAC、不依赖 C3、私钥不落库）。
- **S0-3 定稿**：待复核 20 项 + 接口待定 14 项 + 遗留问题 7 项已逐条拍板，见 SCHEMA「定稿清单」、API §12.1、DECISIONS D-011~D-018。
- **S0-3 完成**：`docs/SCHEMA.md`（16 表）与 `docs/API.md`（36 接口）定稿草案；同步产出 `DECISIONS.md`（D-001~D-010，含与开题不符的三处调整理由）、`EVAL.md`（指标口径与数字纪律）、`PROGRESS.md`、`samples/README.md`。
- 契约阶段敲定的具体口径：
  - 角色只有三个：`submitter` / `reviewer` / `admin`；**财务服务器不是用户**，是 `certificates.owner_type='server'` 的证书主体；
  - 枚举统一 `TEXT + CHECK`（D-007）；金额 `NUMERIC(12,2)` + `Decimal`，禁浮点（D-008）；
  - 业务不变式下沉到库：发票 `(invoice_code, invoice_no) WHERE status<>'void'` 部分唯一、每单只有一条 `active` 签名、`report_forms.idempotency_key` 唯一、`invoice_files.sha256` 唯一（D-009）；
  - 新增三列为契约补缺：`invoices.version` / `report_forms.version`（乐观锁）、`report_forms.report_no`（业务单号）、`invoice_fields.source_page_no`（多页定位）；
  - `invoice_fields.source` 定为 5 值（含二次识别的 `ocr_retry`）；
  - `/health` 与 `/api/v1/health` 两个都提供（容器探针 + 业务前缀），同一实现。
- 未提交/未推送：本次改动均在本地，按约定 **`git push` 由你执行**。