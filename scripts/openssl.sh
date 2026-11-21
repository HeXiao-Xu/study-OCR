#!/usr/bin/env bash
# 在 Docker 里执行 OpenSSL 3.x 命令行（宿主机没有 openssl，也不需要安装）。
#
# 为什么这么做：S4-5/S4-6 的任务卡要求用 `openssl genpkey -algorithm SM2` 生成密钥、
# 用 `openssl req -x509 -newkey sm2 -sm3` 建 CA 与角色证书；而本机 PATH / MSYS2 / Git
# 都没有 openssl.exe。Docker 是本项目的既有依赖（PG/Redis/MinIO 都在跑），用容器里的
# OpenSSL 3.5 既满足"OpenSSL 3.x 命令行"的要求，又不在宿主机装任何东西（D-078）。
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
#   * 不挂网络（`--network none`），openssl 本来也不需要网络；
#   * 与 `scripts/openssl.ps1`（Windows PowerShell）等价，实现共用 `_openssl_docker.sh`。
set -euo pipefail

source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_openssl_docker.sh"
openssl_docker "$@"