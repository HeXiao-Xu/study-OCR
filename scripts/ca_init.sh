#!/usr/bin/env bash
# S4-6：自建 CA 与角色证书（OpenSSL 命令行；宿主机不需要装 openssl，见 D-078）。
#
# 用法（在任意目录都能跑，脚本自己切到仓库根；路径都用仓库相对路径）：
#   bash scripts/ca_init.sh init [--force]          # 建/重建 CA（secrets/ca/）
#   bash scripts/ca_init.sh issue <name> <role>     # 给已生成的用户密钥签发证书
#   bash scripts/ca_init.sh show <cert.pem>         # 打印证书全文
#   bash scripts/ca_init.sh verify <cert.pem>       # 用 CA 做一次链校验（部署期动作）
#
# 前置：用户密钥由 `scripts/keygen.py --name <name>` 生成（secrets/keys/<name>/）。
# 签发时私钥**不落盘**：脚本用 `unlock_key.py` 解锁，PEM 直接经 stdin 交给 openssl 做 CSR。
#
# 环境变量：CERT_DAYS（默认 3650）、CERT_ORG（subject 的 O，默认 StudyOCR University）、
#           OPENSSL_IMAGE（镜像源，见 D-078/README）。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
source "$ROOT/scripts/_openssl_docker.sh"

CA_DIR="secrets/ca"
CERT_DIR="secrets/certs"
DAYS="${CERT_DAYS:-3650}"
ORG="${CERT_ORG:-StudyOCR University}"
PYTHON="${PYTHON:-.venv/Scripts/python.exe}"
if [ ! -x "$PYTHON" ]; then
  PYTHON=".venv/bin/python"
fi

usage() {
  sed -n '4,10p' "$0" | sed 's/^# \{0,1\}//'
  exit 1
}

cmd_init() {
  local force=0
  if [ "${1:-}" = "--force" ]; then force=1; fi
  mkdir -p "$CA_DIR"
  if [ -f "$CA_DIR/ca.crt.pem" ] && [ "$force" -ne 1 ]; then
    echo "CA 已存在：$CA_DIR/ca.crt.pem（要重建请加 --force）"
    return 0
  fi

  echo "生成 CA（SM2-SM3，${DAYS} 天）……"
  openssl_docker req -x509 -newkey sm2 -sm3 -days "$DAYS" -nodes \
    -keyout "$CA_DIR/ca.key.pem" -out "$CA_DIR/ca.crt.pem" \
    -subj "/CN=StudyOCR-CA/O=$ORG" \
    -addext "basicConstraints=critical,CA:TRUE" \
    -addext "keyUsage=critical,keyCertSign,cRLSign"
  chmod 600 "$CA_DIR/ca.key.pem" 2>/dev/null || true
  echo "CA 已生成：$CA_DIR/ca.crt.pem"
  openssl_docker x509 -in "$CA_DIR/ca.crt.pem" -noout -subject -serial -fingerprint -sha256
}

cmd_issue() {
  local name="${1:-}"
  local role="${2:-}"
  [ -n "$name" ] && [ -n "$role" ] || usage
  if ! [[ "$name" =~ ^[a-z0-9][a-z0-9_-]{0,31}$ ]]; then
    echo "错误：name 只能是小写字母/数字/下划线/短横线（1–32 位）" >&2
    exit 1
  fi
  case "$role" in
    submitter|reviewer|admin) ;;
    *) echo "错误：role 只能是 submitter / reviewer / admin" >&2; exit 1 ;;
  esac

  local public_key="secrets/keys/$name/sign.pub.pem"
  if [ ! -f "$public_key" ]; then
    echo "错误：找不到 $public_key；先跑 $PYTHON scripts/keygen.py --name $name" >&2
    exit 1
  fi
  if [ ! -f "$CA_DIR/ca.crt.pem" ]; then
    echo "错误：还没有 CA；先跑 bash scripts/ca_init.sh init" >&2
    exit 1
  fi

  mkdir -p "$CERT_DIR"
  local work="$CERT_DIR/.issue-$name-$$"
  mkdir -p "$work"
  trap "rm -rf '$work'" EXIT

  cat > "$work/ext.cnf" <<'EOF'
basicConstraints = critical,CA:FALSE
keyUsage = critical,digitalSignature,keyEncipherment,nonRepudiation
extendedKeyUsage = clientAuth
subjectKeyIdentifier = hash
authorityKeyIdentifier = keyid,issuer
EOF

  echo "解锁私钥 → CSR（私钥只走 stdin，不落盘）……"
  "$PYTHON" scripts/unlock_key.py --name "$name" \
    | openssl_docker_stdin req -new -key /dev/stdin -sm3 \
        -subj "/CN=$name/O=$ORG/OU=$role" -out "$work/$name.csr.pem"

  echo "CA 签发证书……"
  openssl_docker x509 -req -in "$work/$name.csr.pem" -sm3 \
    -CA "$CA_DIR/ca.crt.pem" -CAkey "$CA_DIR/ca.key.pem" \
    -CAcreateserial -CAserial "$CA_DIR/ca.srl" -days "$DAYS" \
    -extfile "$work/ext.cnf" -out "$CERT_DIR/$name.crt.pem"

  # 公钥一致性自检：证书里的 SPKI 必须与 keygen 登记的公钥 PEM 逐字节一致（防配错私钥）。
  openssl_docker x509 -in "$CERT_DIR/$name.crt.pem" -pubkey -noout -out "$work/cert.pub.pem"
  if ! cmp -s "$work/cert.pub.pem" "$public_key"; then
    echo "错误：证书公钥与 $public_key 不一致（私钥/公钥配错）" >&2
    exit 1
  fi

  rm -rf "$work"
  trap - EXIT
  echo "证书已签发：$CERT_DIR/$name.crt.pem"
  openssl_docker x509 -in "$CERT_DIR/$name.crt.pem" -noout \
    -subject -issuer -serial -fingerprint -sha256 -dates
  echo "公钥一致性自检：通过"
}

cmd_show() {
  local cert="${1:-}"
  [ -n "$cert" ] || usage
  openssl_docker x509 -in "$cert" -noout -text
}

cmd_verify() {
  local cert="${1:-}"
  [ -n "$cert" ] || usage
  openssl_docker verify -CAfile "$CA_DIR/ca.crt.pem" "$cert"
}

case "${1:-}" in
  init) shift; cmd_init "$@" ;;
  issue) shift; cmd_issue "$@" ;;
  show) shift; cmd_show "$@" ;;
  verify) shift; cmd_verify "$@" ;;
  *) usage ;;
esac