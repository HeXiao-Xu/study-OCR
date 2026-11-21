#!/usr/bin/env bash
# 在 Docker 里执行 OpenSSL 3.x 命令行（宿主机没有 openssl，也不需要安装）。
#
# 为什么这么做：S4-5/S4-6 的任务卡要求用 `openssl genpkey -algorithm SM2` 生成密钥、
# 用 `openssl req -x509 -newkey sm2 -sm3` 建 CA 与角色证书；而本机 PATH / MSYS2 / Git
# 都没有 openssl.exe。Docker 是本项目的既有依赖（PG/Redis/MinIO 都在跑），用容器里的
# OpenSSL 3.5 既满足"OpenSSL 3.x 命令行"的要求，又不在宿主机装任何东西。
#
# 用法（路径一律用**仓库相对路径**；容器内工作目录 = /work = 仓库根）：
#   scripts/openssl.sh version
#   scripts/openssl.sh genpkey -algorithm SM2 -out secrets/keys/alice-sign.pem
#   scripts/openssl.sh pkey -in secrets/keys/alice-sign.pem -pubout -out secrets/keys/alice-sign.pub.pem
#   scripts/openssl.sh dgst -sm3 -binary somefile            # 独立复算摘要
#
# 注意：
#   * 写到 /tmp 的文件随容器销毁——**密钥必须写到 secrets/** 这类挂载目录；
#   * 镜像固定 tag（`OPENSSL_IMAGE` 可覆盖），避免"今天能跑明天组会跑不了"；
#   * 不挂网络（`--network none`），openssl 本来也不需要网络。
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
IMAGE="${OPENSSL_IMAGE:-alpine/openssl:3.5.9}"

if ! docker info >/dev/null 2>&1; then
  echo "Docker 没在运行：本脚本用容器里的 OpenSSL，请先启动 Docker Desktop。" >&2
  exit 1
fi

# Docker Desktop for Windows 需要 Windows 形式的挂载源路径；Git Bash/MSYS2 会把
# `/work` 这类参数自动改写成 Windows 路径，必须关掉转换，否则 -v 与容器内路径都会坏。
if command -v cygpath >/dev/null 2>&1; then
  MOUNT_ROOT="$(cygpath -w "${ROOT}")"
else
  MOUNT_ROOT="${ROOT}"
fi
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL='*'

exec docker run --rm --network none \
  -v "${MOUNT_ROOT}:/work" \
  -w /work \
  --entrypoint openssl \
  "${IMAGE}" "$@"