# S4-6 X.509 自建 CA 与角色证书 — 设计（spec）

> 任务卡：`docs/OCR票据识别与加密系统-AI协作开发实施计划.md` 第 7 章 S4-6。
> 阶段 4 规则：只写 `backend/app/crypto/`、`backend/tests/test_crypto/` 与 `scripts/`；
> 证书口径在这里定死，S5 只登记/校验、不重新发明。
> 状态：**已实现（2026-11-21）**，实测见 `PROGRESS.md` S4-6 行。

## 1. 目标

每个用户一张证书；运行期验签/封装前能判**指纹、有效期、公钥一致性、吊销**。

```
scripts/ca_init.sh init                      → secrets/ca/{ca.key.pem, ca.crt.pem}（自签 CA，SM2-SM3）
scripts/ca_init.sh issue alice submitter     → secrets/certs/alice.crt.pem
        │  身份私钥（S4-5 的口令加密 .p8.enc）经 stdin 解锁 → CSR → CA 签发
        ▼
backend/app/crypto/certs.py  ← 运行期只读证书：parse_certificate / check_certificate
```

范围外：完整 X.509 链验签、CRL/OCSP、证书轮换（刻意的能力边界，D-080）。

## 2. 已拍板口径

| # | 问题 | 结论 | 理由 |
|---|---|---|---|
| Q1 | 谁签发 | **OpenSSL 3.5 命令行**（`scripts/openssl.sh` 的 Docker 封装，D-078） | 任务卡要求；Python 不实现 CA |
| Q2 | CSR 从哪来 | 用 S4-5 的 `.p8.enc` 解锁出 PKCS#8 PEM，**经 stdin**（`-key /dev/stdin`）交给 `openssl req -new`；私钥**不落盘** | 实测可行；比"临时 PEM 文件"少一处明文落盘面（AGENTS §4.3） |
| Q3 | 证书扩展 | `basicConstraints=critical,CA:FALSE`、`keyUsage=critical,digitalSignature,keyEncipherment,nonRepudiation`、`extendedKeyUsage=clientAuth`、SKI/AKI | 任务卡原文；实测 `openssl x509 -text` 可见 |
| Q4 | subject | `CN=<用户名>/O=StudyOCR University/OU=<role>` | 任务卡原文；`role` 从 OU 取（SCHEMA `certificates` 没有 role 列） |
| Q5 | 有效期 | CA 3650 天、用户证书 3650 天（测试夹具同） | 演示/答辩周期内不过期；真实系统应更短并轮换（写进不足） |
| Q6 | `certs.py` 做什么 | ① 指纹 SHA-256；② 有效期；③ 证书公钥 == 登记公钥 hex；④ 吊销；⑤ 从 OU 取角色 | 任务卡三件事 + 吊销/角色（SCHEMA 需要） |
| Q7 | **不做**链验签 | 只信"本 CA 登记表 + 指纹"，不验证签名链 | 任务卡/SCHEMA §2.2 明确的简化：单 CA 拓扑，链校验在部署期由 OpenSSL 做一次；调用方可用 `scripts/openssl.sh verify` 复核 |
| Q8 | 吊销怎么判 | `check_certificate(..., revoked: bool)`，由登记簿的 `revoked/revoked_at` 传入 | 不引入 CRL/OCSP；SCHEMA 已定 |
| Q9 | 公钥怎么取 | `cryptography` 解析证书；但 `cert.public_key()` 对 SM2 抛 `UnsupportedAlgorithm` → 从证书 DER 的 tbsCertificate 取 SPKI BIT STRING（pycryptodomex 原语） | 实测；不手写 ASN.1 |
| Q10 | 失败取值 | `CertStatus{ok, cert_expired, cert_not_yet_valid, cert_revoked, cert_public_key_mismatch, cert_role_mismatch}` | 任务卡要求 `cert_revoked`；S5/信封聚合为 `cert_invalid` |
| Q11 | 测试夹具 | 预生成的**公开证书**（CA/alice/bob）放 `tests/test_crypto/fixtures/`，含预期指纹/公钥/序列号常量 | 测试不依赖 Docker；cert 是公开物，不含私钥 |

## 3. `certs.py` API

```python
class CertStatus(StrEnum):
    OK = "ok"
    EXPIRED = "cert_expired"
    NOT_YET_VALID = "cert_not_yet_valid"
    REVOKED = "cert_revoked"
    PUBLIC_KEY_MISMATCH = "cert_public_key_mismatch"
    ROLE_MISMATCH = "cert_role_mismatch"

@dataclass(frozen=True)
class CertificateInfo:
    subject_dn: str
    issuer_dn: str
    serial_no: str            # 小写十六进制
    fingerprint_sha256: str   # 64 位小写 hex（SHA-256 of DER）
    public_key_hex: str       # 128 hex，不带 04
    role: str | None          # 从 OU 取
    not_before: datetime
    not_after: datetime

def parse_certificate(cert_pem: bytes | str) -> CertificateInfo
def check_certificate(
    cert_pem: bytes | str,
    *,
    expected_public_key_hex: str,
    expect_role: str | None = None,
    revoked: bool = False,
    now: datetime | None = None,
) -> CertStatus
```

判定顺序：**吊销 → 未生效/已过期 → 公钥不一致 → 角色不一致**（已吊销的证书不该再给任何"有效"的理由）。
`now` 必须带时区（默认 UTC）；非法 PEM/DER → `ValueError`。

## 4. 脚本

- `scripts/_openssl_docker.sh`：`openssl_docker` / `openssl_docker_stdin` 两个函数，`openssl.sh` 与
  `ca_init.sh` 共用（镜像、挂载、`OPENSSL_IMAGE` 只定义一次）；
- `scripts/openssl.sh`：瘦身为 `source` + 调用（行为不变）；
- `scripts/unlock_key.py`：把 `.p8.enc` 解锁成 PKCS#8 PEM **输出到 stdout**（只给管道用，不写文件）；
  口令走 `STUDYOCR_KEY_PASSPHRASE`/`getpass`；
- `scripts/ca_init.sh`：
  - `init [--force]`：建 CA（`req -x509 -newkey sm2 -sm3`，CA:TRUE + keyCertSign/cRLSign）；
  - `issue <name> <role>`：解锁 → `req -new -key /dev/stdin`（私钥不落盘）→ `x509 -req -sm3 -CA … -extfile`；
    输出 `secrets/certs/<name>.crt.pem`，打印 subject/serial/指纹；
  - 所有路径都相对仓库根（容器工作目录 `/work`），脚本先 `cd "$ROOT"`。

## 5. 测试（`test_certs.py`，默认跑、不需要 Docker）

| # | 用例 | 断言 |
|---|---|---|
| 1 | 解析 alice 证书 | `subject_dn` 含 `CN=alice`/`OU=submitter`；`role == "submitter"`；serial/not_before/not_after 与夹具记录一致 |
| 2 | 指纹 | 与 `openssl x509 -fingerprint -sha256` 记录值逐字一致 |
| 3 | 公钥提取 | 与 `alice.pub.pem` 的 128 hex 一致；`cryptography.public_key()` 抛 `UnsupportedAlgorithm`（证明必须走 DER） |
| 4 | 扩展 | KeyUsage=digitalSignature/nonRepudiation/keyEncipherment；EKU=clientAuth；CA 证书 CA:TRUE |
| 5 | `check_certificate` 正常 | `OK` |
| 6 | 过期 / 未生效 | `now=not_after+1s` → EXPIRED；`now=not_before-1s` → NOT_YET_VALID |
| 7 | 吊销 | `revoked=True` → REVOKED（即使时间/公钥都对） |
| 8 | 公钥不匹配 | 拿 bob 的公钥 → PUBLIC_KEY_MISMATCH |
| 9 | 角色不匹配 | `expect_role="reviewer"` 验 alice → ROLE_MISMATCH |
| 10 | 非法输入 | 非 PEM/损坏 DER → `ValueError`；naive `now` → `ValueError` |

## 6. 验收

1. `pytest backend/tests/test_crypto -q` 全绿（S4-1~S4-5 的 205 条不回归）；
2. `scripts/ca_init.sh init` + `issue alice submitter` 真跑：证书产出、`openssl x509 -text` 可见
   KeyUsage/EKU/SM2 公钥、`openssl verify -CAfile ca.crt` 返回 OK；
3. 私钥不落盘：temp 目录只有 CSR（公开），签发后无明文私钥文件；
4. 配错私钥（拿 bob 的公钥对 alice 证书）→ `cert_public_key_mismatch`；
   吊销参数 → `cert_revoked`；
5. 全量单测无回归；`alembic check` 零差异（`certificates` 落库属 S5）；
6. 文档收口：PROGRESS / DECISIONS（D-080）/ AGENTS / HANDOFF。

## 7. 已知限制（论文"安全边界"一节）

- **不做证书链验证**：只信本地登记表 + 指纹 + 有效期 + 吊销位；不防"CA 被仿冒"以外的伪造？
  单 CA 拓扑下这是刻意简化，多级 CA/跨机构互信不适用；
- 吊销只查本地 `certificates`，无 CRL/OCSP（SCHEMA 已注明）；
- 证书有效期长（10 年），没有自动轮换；
- `keyUsage=clientAuth` 只用于客户端身份，没有 serverAuth 证书；
- 用户证书的 OU 是角色来源，**角色变更需要重签证书**（S5 管理动作）。