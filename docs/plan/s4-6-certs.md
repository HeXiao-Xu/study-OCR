# S4-6 X.509 自建 CA 与角色证书 — 实现计划（plan）

> 对应 spec：[`docs/spec/s4-6-certs.md`](../spec/s4-6-certs.md)｜
> 任务卡：实施计划第 7 章 S4-6｜状态：**已实现（2026-11-21）**，Task 0–3 全部执行并验证
> 只动 `backend/app/crypto/`、`backend/tests/test_crypto/`、`scripts/` 与文档；
> 不碰业务接口、不加表/迁移（`certificates` 落库属 S5）。

## Global Constraints

- 证书由 **OpenSSL 3.5 命令行**（Docker 内，D-078）签发；Python 侧只做运行期校验；
- **不做完整链验签**（单 CA 拓扑，D-080）；吊销只查本地登记；
- 签发时**私钥不落盘**：解锁的 PKCS#8 PEM 只走 stdin（`-key /dev/stdin`）；
- 测试不依赖 Docker：夹具是预生成的**公开证书**；
- 不新增依赖（`cryptography`/`pycryptodomex` 已锁版本）。

## 文件结构

| 文件 | 职责 | 新增/改 |
|---|---|---|
| `backend/app/crypto/certs.py` | `CertStatus` / `CertificateInfo` / `parse_certificate` / `check_certificate`（DER 取 SPKI 公钥） | 新 |
| `backend/tests/test_crypto/test_certs.py` | 10 组用例（spec §5） | 新 |
| `backend/tests/test_crypto/fixtures/{ca,alice,bob}.crt.pem` + `{alice,bob}.pub.pem` | 公开证书夹具 | 新 |
| `scripts/_openssl_docker.sh` | `openssl_docker` / `openssl_docker_stdin`（单一定义） | 新 |
| `scripts/openssl.sh` | 改为 source 共享函数（行为不变） | 改 |
| `scripts/unlock_key.py` | `.p8.enc` → PKCS#8 PEM 到 stdout（只给管道） | 新 |
| `scripts/ca_init.sh` | `init` / `issue` / `show` | 新 |
| `docs/{spec,plan}/s4-6-certs.md`、`docs/{PROGRESS,DECISIONS,HANDOFF}.md`、`AGENTS.md` | 收口 | 新/改 |

## 夹具（已生成，公开物）

| 文件 | 内容 |
|---|---|
| `ca.crt.pem` | CA 自签证书（CN=StudyOCR-CA / O=StudyOCR University，CA:TRUE） |
| `alice.crt.pem` | CN=alice / OU=submitter（KeyUsage + clientAuth） |
| `bob.crt.pem` | CN=bob / OU=reviewer |
| `alice.pub.pem`、`bob.pub.pem` | 对应公钥（比对用） |

> 测试常量（指纹/序列号/有效期/公钥 hex）从生成时的 OpenSSL 输出抄入，注明出处。

---

### Task 0: 探针（已执行）

- [x] `req -x509 -newkey sm2 -sm3` 建 CA；`req -new -key /dev/stdin` + `x509 -req -sm3 -extfile` 签发成功
- [x] `openssl x509 -text`：KeyUsage（DS/NR/KE）、EKU clientAuth、SKI/AKI 均在；`openssl verify` OK
- [x] `cryptography` 能解析证书/扩展/有效期/指纹，但 `cert.public_key()` 抛 `UnsupportedAlgorithm`
      → 公钥必须从 DER 的 tbsCertificate SPKI BIT STRING 取（pycryptodomex）

---

### Task 1: `certs.py`（先红后绿）

- [x] **Step 1**: 写 `test_certs.py`（10 组用例）→ 先红（模块不存在）。
- [x] **Step 2**: 实现 `parse_certificate`（cryptography 解析 + DER 取公钥）与 `check_certificate`（判定顺序）。
- [x] **Step 3**: 转绿。

---

### Task 2: 脚本（共享 docker 封装 + 解锁 + CA 签发）

- [x] **Step 1**: `_openssl_docker.sh`；`openssl.sh` 改为 source 它并回归（`version`/`genpkey`）。
- [x] **Step 2**: `unlock_key.py`（stdout 输出 PEM，错误走 stderr；不打印口令/私钥到日志）。
- [x] **Step 3**: `ca_init.sh`：
  ```bash
  bash scripts/ca_init.sh init
  bash scripts/ca_init.sh issue alice submitter
  bash scripts/ca_init.sh show secrets/certs/alice.crt.pem
  ```
- [x] **Step 4**: 真跑：CA + alice 证书；`openssl verify`；确认 temp 目录无明文私钥。

---

### Task 3: 回归、文档与提交

- [x] **Step 1**: `pytest tests/test_crypto -q` + 全量 `pytest` + `alembic check`。
- [x] **Step 2**: PROGRESS（勾选/产出/遗留）、DECISIONS **D-080**（无链验签的边界、stdin 签发、
      role 从 OU 取、证书落库属 S5）、AGENTS 锚点、HANDOFF（下一步 S4-7 审计链）。
- [x] **Step 3**: 提交 `feat(s4-6): 自建 CA 与角色证书（OpenSSL 签发 + 运行期校验）`；**push 由用户做**。

## 风险与兜底

| 风险 | 兜底 |
|---|---|
| 夹具证书过期导致测试随日期失败 | 有效期 3650 天；过期/未生效用例显式传 `now`，不依赖当前时间 |
| `cryptography` 版本变化导致 `*_utc` API 不可用 | 已锁 50.0.2；测试固定 API |
| 私钥经管道泄漏到日志 | `unlock_key.py` 只写 stdout、日志不打印内容；`ca_init.sh` 只把 stdout 接给 openssl |
| 路径错位（容器工作目录是 `/work`） | 所有脚本先 `cd "$ROOT"`，只用仓库相对路径（S4-5 踩过坑） |
| 证书扩展缺失 | 测试直接断言 KeyUsage/EKU；`openssl x509 -text` 人工复核 |