# deploy/ — 开发环境基础服务

本目录只负责 **PostgreSQL 16 / Redis 7 / MinIO** 三件套的本地开发环境（S0-4）。
应用本身的容器化部署（api / worker / nginx）在 S7-2 再补 `docker-compose.prod.yml`，不要混在这个文件里。

## 快速开始

```bash
# 1) 首次：准备环境变量（.env 不入库）
cp deploy/.env.example deploy/.env

# 2) 起服务
docker compose -f deploy/docker-compose.dev.yml up -d

# 3) 自检（5 项全 OK 才算通）
python scripts/check_services.py
```

停止 / 销毁：

```bash
# 停容器、保留数据
docker compose -f deploy/docker-compose.dev.yml down

# 连数据一起清（重建库、重新执行 init/*.sql）
docker compose -f deploy/docker-compose.dev.yml down -v
```

## 端口与服务

| 服务 | 容器内 | 宿主机（默认） | 说明 |
|---|---|---|---|
| PostgreSQL 16 | 5432 | **5433** | 业务库 `studyocr` |
| Redis 7 | 6379 | **6380** | 缓存 + ARQ 队列，开 AOF |
| MinIO S3 API | 9000 | **9002** | 票据影像密文对象存储 |
| MinIO 控制台 | 9001 | **9003** | 浏览器打开 `http://127.0.0.1:9003` |

> **为什么不用 5432/6379/9000/9001**：本机已有另一套项目（study-agent）占着这些端口，
> 错开之后两个项目可以同时运行。端口都能在 `deploy/.env` 里改，改完 `up -d` 生效。

所有端口都只绑定 `127.0.0.1`，不对局域网暴露。

## 数据与持久化

| 卷 | 用途 | 说明 |
|---|---|---|
| `study-ocr-pgdata` | PostgreSQL 数据目录 | **命名卷**，不是相对路径绑定挂载 |
| `study-ocr-redisdata` | Redis AOF 文件 | 队列与缓存重启不丢 |
| `study-ocr-miniodata` | MinIO 对象数据 | bucket 与对象 |

> **Windows 上不要改成 `./data:/var/lib/postgresql/data` 这种绑定挂载**：NTFS 权限与换行会让
> PostgreSQL 初始化直接失败（`initdb: cannot be run as root` / 权限拒绝），用命名卷最省事。

## 初始化行为（重要）

- `deploy/init/*.sql` 只在 **数据卷首次创建** 时由 postgres 官方 entrypoint 执行一次；
  之后改 SQL 不会重跑，要生效必须 `down -v`（会清空开发库）。
  目前只有 `01_extensions.sql`（`pgcrypto`）。
- `minio-init` 是一次性容器，用 `mc mb --ignore-existing` **幂等**创建 bucket，并把 bucket 设为私有；
  每次 `up -d` 都会重跑一遍，已存在不报错、不重复建。
- 真正的建表（16 张业务表）由 **S2-1 的 Alembic 迁移**负责，不放在这里——DDL 只有一处来源。

## 自检脚本做什么

`scripts/check_services.py` 只依赖 Python 标准库（此时后端虚拟环境还没建），检查项：

1. `docker compose` 可用、compose 文件能解析；
2. PostgreSQL：在容器内 `select 1`，并确认 `pgcrypto` 已装上（证明 init 目录真的挂进去了）；
3. Redis：`AUTH` + `PING`，并确认 `appendonly=yes`；
4. MinIO：健康探针 `/minio/health/live`；
5. MinIO 对象读写：在 bucket 里真实写入 → 读取 → 删除一个测试对象。

对 PG / MinIO 的操作都通过 `docker compose exec/run` 在**容器内**执行，验证的是容器本身，
而不是宿主机上某个恰好占了同端口的进程。加 `-v` 可以看到每条实际命令。

## 常见问题

| 现象 | 原因与处理 |
|---|---|
| `port is already allocated` | 端口被占。改 `deploy/.env` 的 `POSTGRES_PORT` 等，再 `up -d` |
| `pgcrypto 未安装` | 数据卷不是新建的。`down -v` 后重来（会清库） |
| `minio-init` 反复重启 | 检查 `MINIO_ROOT_USER/PASSWORD` 是否 ≥8 位；`logs minio-init` 看具体错误 |
| 拉不到镜像 / `registry-1.docker.io` 超时 | 走镜像源全名拉取：`bash scripts/pull_image.sh <镜像名>`（详见上一节）；兜底用本机已缓存镜像 |
| Redis 报 `NOAUTH` | `deploy/.env` 的 `REDIS_PASSWORD` 与容器内不一致；改完 `up -d` 重建容器 |
| 自检第 5 项失败 | 先跑 `docker compose -f deploy/docker-compose.dev.yml logs minio-init`，再手动 `run --rm minio-init` 看 `mc` 的报错 |

## 镜像拉取（直连 Docker Hub 会超时，必须走镜像源）

**现象**：`docker pull postgres:16` 卡住几十秒后报
`Get "https://registry-1.docker.io/v2/": net/http: request canceled`。

**原因**：Docker Desktop 虽然配了 `registry-mirrors`，但 `docker pull <裸镜像名>` 在 mirror 列表里
逐个尝试失败后仍会**回退到 registry-1.docker.io**，而该地址在本网络下不可达；报错只显示最后一次尝试，
看起来像"镜像源没生效"。

**解法：用镜像源全名拉，再 `docker tag` 成标准名。** 这样不会触发那段回退逻辑。
仓库里已封装成脚本：

```bash
bash scripts/pull_image.sh postgres:16          # 自动依次尝试多个镜像源、成功即打标准 tag
bash scripts/pull_image.sh nginx:alpine
bash scripts/pull_image.sh minio/minio:latest docker.1ms.run   # 也可手动指定用哪个源
```

脚本行为：本地已有该镜像 → 直接 `[skip]`；否则按 `docker.m.daocloud.io` → `docker.xuanyuan.me` →
`docker.1ms.run` → 华为 SWR 的顺序尝试，成功后自动 `docker tag`，失败才返回非 0。

**本机实测（2026-11-18）**：

| 源 | 结果 |
|---|---|
| `docker.m.daocloud.io` | ✅ 可用（`postgres:16`、`nginx:alpine` 均拉取成功） |
| `docker.xuanyuan.me` | 未逐个验证，脚本里作为备选 |
| `docker.1ms.run` | ⚠️ 小镜像可拉（hello-world 成功），大镜像层出现 `unknown blob` |
| `swr.cn-north-4.myhuaweicloud.com/ddn-k8s/docker.io` | ⚠️ 只缓存了部分镜像（本机 minio 来自这里），`library/postgres:16` 报 manifest not found |

**手工等价命令**：

```bash
docker pull docker.m.daocloud.io/library/postgres:16   # 官方镜像要带 library/ 前缀
docker tag  docker.m.daocloud.io/library/postgres:16 postgres:16
docker rmi  docker.m.daocloud.io/library/postgres:16   # 可选：清掉别名，只留标准名
```

> 非官方镜像（`minio/minio:latest`、`minio/mc:latest`）没有 `library/` 前缀，
> 直接 `docker pull docker.m.daocloud.io/minio/minio:latest` 即可。

**兜底**：实在拉不到就用本机已缓存的镜像——`deploy/.env` 里四个 `*_IMAGE` 都是变量，
改成已有 tag 即可（本机当前有 `postgres:16`、`redis:7-alpine`、`minio/minio:latest`、
`minio/mc:latest`、`nginx:alpine`、`pgvector/pgvector:pg16`）。

## 镜像版本

`deploy/.env` 里四个镜像都用变量，改版本只动 `.env`，compose 文件不用改。
当前默认：`postgres:16`、`redis:7-alpine`、`minio/minio:latest`、`minio/mc:latest`。

> `minio/minio` 与 `minio/mc` 用的是 `latest`（MinIO 官方按日期发 RELEASE tag，且本机缓存的
> 就是这个 tag）。**S7-2 生产化前要钉到具体 RELEASE tag**，并重跑一次自检——MinIO 跨版本行为有变化风险。
> 本机另有一个 `pgvector/pgvector:pg16`（基于 postgres:16，多带 pgvector 扩展），本课题不需要，
> 留作备用镜像。