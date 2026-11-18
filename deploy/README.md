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
| 拉不到镜像 | 本机已缓存 `pgvector/pgvector:pg16`、`redis:7-alpine`、`minio/minio:latest`、`minio/mc:latest`；离线可直接起，别改镜像名 |
| Redis 报 `NOAUTH` | `deploy/.env` 的 `REDIS_PASSWORD` 与容器内不一致；改完 `up -d` 重建容器 |
| 自检第 5 项失败 | 先跑 `docker compose -f deploy/docker-compose.dev.yml logs minio-init`，再手动 `run --rm minio-init` 看 `mc` 的报错 |

## 镜像版本

`deploy/.env` 里四个镜像都用变量，默认值是**本机已缓存、离线可用**的 tag；
有网、想钉版本时只改 `.env` 即可（compose 文件不用动）。导入生产前建议钉到具体 release tag。