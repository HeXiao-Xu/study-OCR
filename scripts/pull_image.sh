#!/usr/bin/env bash
# 通过镜像源拉取镜像并打上标准名（Docker Hub 直连超时时用）。
#
# 为什么需要它：本机 Docker 已配置 registry-mirrors，但 `docker pull postgres:16`
# 仍会回退到被墙的 registry-1.docker.io 并超时；而用「镜像源全名」拉取不会走那段回退逻辑。
# 拉完再 docker tag 成标准名，compose 里就能继续写 postgres:16。
#
# 用法：
#   bash scripts/pull_image.sh postgres:16
#   bash scripts/pull_image.sh nginx:alpine docker.m.daocloud.io
#   bash scripts/pull_image.sh minio/minio:latest docker.1ms.run docker.xuanyuan.me
#
# 退出码：0 成功；1 所有镜像源都失败。
set -uo pipefail

IMAGE="${1:?用法: pull_image.sh <repo:tag> [mirror ...]}"
shift || true

# 默认镜像源（按实测成功率排序；华为 SWR 只缓存部分镜像，放最后）
DEFAULT_MIRRORS=(
  "docker.m.daocloud.io"
  "docker.xuanyuan.me"
  "docker.1ms.run"
  "swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io"
)
MIRRORS=("$@")
[ ${#MIRRORS[@]} -eq 0 ] && MIRRORS=("${DEFAULT_MIRRORS[@]}")

# Docker 官方镜像（postgres:16、nginx:alpine）在 registry 里的路径带 library/ 前缀
if [[ "$IMAGE" == *"/"* ]]; then
  REPO="$IMAGE"
  REPO_PATH="$IMAGE"
else
  REPO="$IMAGE"
  REPO_PATH="library/$IMAGE"
fi

# 已经有了就不重复拉
if docker image inspect "$IMAGE" >/dev/null 2>&1; then
  echo "[skip] 本地已有 $IMAGE"
  exit 0
fi

for mirror in "${MIRRORS[@]}"; do
  candidate="${mirror}/${REPO_PATH}"
  echo "[try ] $candidate"
  if docker pull "$candidate"; then
    docker tag "$candidate" "$REPO"
    echo "[ok  ] $REPO  <-  $candidate"
    echo "       如需清理镜像源别名：docker rmi $candidate"
    exit 0
  fi
  echo "[fail] $candidate"
done

echo "[dead] 所有镜像源都失败：$IMAGE" >&2
echo "       兜底方案：用本机已缓存的镜像，在 deploy/.env 里改 *_IMAGE 变量（见 deploy/README.md）。" >&2
exit 1