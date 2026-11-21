# 共用：把仓库挂进 alpine/openssl 容器执行命令。
#
# `scripts/openssl.sh` 与 `scripts/ca_init.sh` 都 source 本文件，镜像/挂载/路径转换只定义一次。
# 注意：容器内工作目录是 /work（= 仓库根），所以**所有传给 openssl 的路径都必须是仓库相对路径**。
_openssl_docker_impl() {
  local stdin_flag="$1"
  shift || true
  local root image mount_root

  root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
  image="${OPENSSL_IMAGE:-alpine/openssl:3.5.9}"

  if ! docker info >/dev/null 2>&1; then
    echo "Docker 没在运行：本脚本用容器里的 OpenSSL，请先启动 Docker Desktop。" >&2
    return 1
  fi

  # Docker Desktop for Windows 需要 Windows 形式的挂载源路径；Git Bash/MSYS2 会改写
  # `/work` 这类参数，必须关掉转换，否则 -v 与容器内路径都会坏。
  if command -v cygpath >/dev/null 2>&1; then
    mount_root="$(cygpath -w "${root}")"
  else
    mount_root="${root}"
  fi

  MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
    docker run --rm ${stdin_flag:+$stdin_flag} --network none \
      -v "${mount_root}:/work" \
      -w /work \
      --entrypoint openssl \
      "${image}" "$@"
}

# 普通调用：scripts/openssl.sh version
openssl_docker() {
  _openssl_docker_impl "" "$@"
}

# 需要从 stdin 读私钥（CSR 场景）：ca_init.sh 用它把解锁后的 PEM 管道送进 openssl。
openssl_docker_stdin() {
  _openssl_docker_impl "-i" "$@"
}