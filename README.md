# study-OCR

基于 OCR 的高校报销票据识别与加密系统（本科毕业设计）。

票据图片/PDF 进 → 预处理 → 文本检测识别（带坐标与置信度）→ 关键要素抽取与校验 → 人工确认 →
SM3 摘要 + SM2 签名 + SM4 数字信封 → 财务端验签解密 → 审核留痕。

> 设计文档：[docs/OCR票据识别与加密系统-毕业设计完整方案.md](docs/OCR票据识别与加密系统-毕业设计完整方案.md)
> 分步实施：[docs/OCR票据识别与加密系统-AI协作开发实施计划.md](docs/OCR票据识别与加密系统-AI协作开发实施计划.md)（任务编号 S0-1 ~ S7-5 出自该文件）

## 当前状态

**S0 阶段（项目启动）已完成**：仓库骨架、项目宪法、数据/接口契约、三件套、后端与前端骨架、环境冒烟。

| 里程碑 | 状态 |
|---|---|
| **M0 立项与可行性验证** | 🟡 环境与骨架就绪（S0 完成）；S1 的 OCR 技术验证未开始 |
| M1 基础框架 | ⬜ 未开始（S2） |
| M2 识别流水线上线 | ⬜ 未开始（S3） |
| M3 密码模块 | ⬜ 未开始（S4） |
| M4 业务闭环 | ⬜ 未开始（S5） |
| M5 实验与评测 | ⬜ 未开始（S6） |
| M6 论文与答辩 | ⬜ 未开始（S7） |

已完成任务：S0-1 骨架 / S0-2 AGENTS.md / S0-3 契约 / S0-4 三件套 / S0-5 后端骨架 / S0-6 前端骨架 / S0-7 冒烟与本文档。
进度与遗留问题见 [docs/PROGRESS.md](docs/PROGRESS.md)。

---

## 1. 环境要求

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | **3.11**（不要用 3.13 / 3.14） | `paddlepaddle` 没有 3.13+ 的 wheel（S1 会用）；本机已有 `py -3.11` |
| Node.js | ≥ 20（实测 25.2） | 前端构建 |
| Docker Desktop | 最新稳定版 | 跑 PostgreSQL / Redis / MinIO |
| OpenSSL | ≥ 3.0 | S4 生成 SM2 密钥与证书时才用到 |
| 操作系统 | Windows 10/11（开发）、Linux（部署） | 本文命令以 Windows + Git Bash 为主，Linux 替换路径分隔符即可 |

> 磁盘：Docker 镜像约 1.5 GB，`node_modules` 约 300 MB，虚拟环境约 200 MB。第一次装会慢，之后再装别的不会重复下载。

## 2. 快速开始（首次，约 10 分钟）

按顺序执行，每步都有可验证的结果。命令都在**仓库根目录**下执行。

```bash
# ── 步骤 1：准备配置文件（两个 .env 都不入库）────────────────────────────
cp deploy/.env.example deploy/.env
cp backend/.env.example backend/.env
# 首次务必改掉 backend/.env 里的 JWT_SECRET（示例值是占位符，prod 环境会直接拒绝启动）
python -c "import secrets;print(secrets.token_hex(32))"   # 生成一个，填进 JWT_SECRET

# ── 步骤 2：起基础服务（PostgreSQL 16 / Redis 7 / MinIO）──────────────────
docker compose -f deploy/docker-compose.dev.yml up -d
docker compose -f deploy/docker-compose.dev.yml ps        # 期望三个都 (healthy)

# ── 步骤 3：Python 虚拟环境 + 后端依赖 ───────────────────────────────────
py -3.11 -m venv .venv
.venv/Scripts/python.exe -m pip install -r backend/requirements.txt
# 验证：.venv/Scripts/python.exe -V   → Python 3.11.x

# ── 步骤 4：起后端 ───────────────────────────────────────────────────────
cd backend
../.venv/Scripts/python.exe -m uvicorn app.main:app --reload --port 8000
# 另开一个终端验证：
#   curl http://127.0.0.1:8000/api/v1/health    → code=0，checks 三项全 ok
#   curl http://127.0.0.1:8000/docs             → OpenAPI 文档
cd ..

# ── 步骤 5：装前端依赖并起前端 ───────────────────────────────────────────
cd web
npm install
npm run dev                     # http://127.0.0.1:5173
# 打开 http://127.0.0.1:5173 → 被守卫送到 /login；
# 开发态可点「写入占位 token 并进入」，或直接访问 http://127.0.0.1:5173/debug?dev_token=smoke
cd ..

# ── 步骤 6：一条命令验收整个环境 ─────────────────────────────────────────
.venv/Scripts/python.exe scripts/smoke.py
# 期望：FAIL 0；WARN 1（数据库迁移，S2-1 起才需要）
```

**S0-7 之后能看到的画面**：

- `http://127.0.0.1:5173/debug`：后端 `/health` 三项依赖状态 + `trace_id`（可复制）+ 原始响应壳；
  点「触发一个 404」可验证统一错误提示。
- `curl http://127.0.0.1:8000/api/v1/health`：`{"code":0,...,"checks":{"db":{"ok":true},...}}`。

## 3. 端口与数据

| 服务 | 宿主机端口 | 容器端口 | 说明 |
|---|---|---|---|
| PostgreSQL 16 | **5433** | 5432 | 库 `studyocr` |
| Redis 7 | **6380** | 6379 | 缓存 + ARQ 队列，开 AOF |
| MinIO S3 API | **9002** | 9000 | 票据影像密文 |
| MinIO 控制台 | **9003** | 9001 | `http://127.0.0.1:9003` |
| 后端 API | **8000** | — | `/api/v1/*`、`/docs` |
| 前端 dev | **5173** | — | Vite |

> 端口刻意与默认值错开（5432/6379/9000/9001 常被本机其它项目占用，本机就有一套 study-agent 占着）。
> 全部只绑 `127.0.0.1`，不对局域网暴露。改端口只改 `deploy/.env`。

数据落在**命名卷**：`study-ocr-pgdata` / `study-ocr-redisdata` / `study-ocr-miniodata`。
`docker compose down` 保留数据；**`down -v` 会清空开发库**（初始化 SQL 也只在卷首次创建时执行）。

## 4. 常用命令

```bash
# 基础服务
docker compose -f deploy/docker-compose.dev.yml up -d / down / logs -f postgres / ps
docker compose -f deploy/docker-compose.dev.yml exec postgres psql -U studyocr -d studyocr

# 环境自检（两层：容器级 / 全链路）
python scripts/check_services.py            # S0-4：只看容器与对象存储读写
.venv/Scripts/python.exe scripts/smoke.py    # S0-7：配置 + 三件套 + 后端 + 迁移

# 后端
cd backend && ../.venv/Scripts/python.exe -m pytest -q
cd backend && ../.venv/Scripts/python.exe -m pytest -m integration -q   # 需真实服务

# 前端
cd web && npm run typecheck && npm run build
cd web && npm run dev

# 前端 UI 冒烟（无头浏览器，需先起前后端 + 一个带调试端口的 Edge）
# 见 scripts/ui_smoke.mjs 头部注释
```

## 5. 目录结构

```
study-OCR/
├─ backend/                      # FastAPI
│  ├─ app/
│  │  ├─ api/v1/                 # 路由（当前只有 health）
│  │  ├─ core/                   # config / logging(trace_id) / exceptions / health
│  │  ├─ models/ schemas/ services/
│  │  ├─ ocr/                    # 预处理 / 检测 / 识别 / 版面 / 抽取 / 校验（S1 起）
│  │  ├─ crypto/                 # canonical / sm / signature / envelope / keystore（S4 起）
│  │  ├─ tasks/                  # ARQ 任务（S2-7 起）
│  │  └─ main.py                 # create_app() + X-Trace-Id 中间件
│  ├─ tests/                     # pytest（24 个用例）
│  ├─ eval/                      # 离线评测数据集与结果
│  ├─ requirements.txt           # 直接依赖，全部锁版本
│  └─ requirements.lock.txt      # 全量 freeze（可精确复现）
├─ web/                          # Vue 3 + Vite + TS
│  └─ src/{api,router,stores,views,types}
├─ docs/                         # 契约与记忆：SCHEMA / API / PROGRESS / DECISIONS / EVAL
├─ deploy/                       # docker-compose.dev.yml、init SQL、部署说明
├─ scripts/                      # check_services / smoke / ui_smoke（+ 后续合成票据、基线跑批）
├─ samples/                      # 票据样本（图片与 PDF 不入库，只入库 MANIFEST）
└─ secrets/                      # 私钥仓（整目录被 .gitignore 排除，S4-5 起使用）
```

## 6. Windows 特有问题（踩过的坑，按疼排序）

| # | 现象 | 原因与处理 |
|---|---|---|
| 1 | `pip install paddlepaddle` 报 "No matching distribution" | 系统默认 `python` 是 3.13/3.14，paddle 只到 cp312。**必须 `py -3.11 -m venv .venv`** |
| 2 | `docker pull postgres:16` 卡住后报 `registry-1.docker.io` 超时 | 镜像源回退逻辑。用 `bash scripts/pull_image.sh postgres:16`（镜像源全名 + retag），详见 `deploy/README.md` |
| 3 | PostgreSQL 容器起不来 / `initdb: cannot be run as root` | 别把数据目录改成 `./data:/var/lib/postgresql/data` 绑定挂载；用命名卷（已默认） |
| 4 | 每次改动都显示整文件 diff | 仓库有 `.gitattributes`（`* text=auto eol=lf`），如果你的编辑器写 CRLF，`git add` 后会提示转换，属正常；不要在编辑器里关掉 |
| 5 | 端口被占 `port is already allocated` | 本机另一套项目占了默认端口；本项目已错开到 5433/6380/9002，冲突就改 `deploy/.env` |
| 6 | 终端里的 `tee` / `nohup` / `cygpath` / `timeout` 找不到 | 本机 Git Bash（MSYS2）是精简版；用 `> file 2>&1` 重定向代替 `tee`，用 `run_in_background` 代替 `nohup` |
| 7 | `msedge --headless --dump-dom` 没有任何输出 | Windows 版 Edge 不把 DOM 打到 stdout；用 `scripts/ui_smoke.mjs`（CDP 直连） |
| 8 | 中文路径导致工具异常 | 仓库放在纯 ASCII 路径下（当前 `E:\BiSheXZY\study-OCR` 是安全的） |
| 9 | 控制台中文乱码 | PowerShell/CMD 用 `chcp 65001`；Python 脚本内已 `reconfigure(encoding="utf-8")` |

## 7. 故障排查

先跑 `scripts/smoke.py`，它会把问题分成三类并给出下一步：

```bash
.venv/Scripts/python.exe scripts/smoke.py -v     # -v 打印实际检查的地址（口令已打码）
.venv/Scripts/python.exe scripts/smoke.py --strict
```

| 冒烟输出 | 含义 | 处理 |
|---|---|---|
| `[FAIL] 配置文件 缺少 ...` | `.env` 没建 | `cp backend/.env.example backend/.env` 等 |
| `[FAIL] PostgreSQL 连不上` | 服务没起 | `docker compose -f deploy/docker-compose.dev.yml up -d` |
| `[FAIL] PostgreSQL 认证失败` | 两个 `.env` 口令不一致 | 对齐 `DATABASE_URL` 与 `POSTGRES_PASSWORD` |
| `[FAIL] MinIO bucket ... 不存在` | 初始化容器没跑成功 | `up -d minio-init`，看 `logs minio-init` |
| `[FAIL] 后端 /health 不可达` | 后端没起 | 见 README 第 2 节步骤 4 |
| `[FAIL] 后端 /health 503 degraded` | 后端起着但下游挂了 | 看返回体 `checks` 字段 |
| `[WARN] 数据库迁移 尚未建表` | S2-1 之前正常 | S2-1 起改为必须通过（`alembic upgrade head`） |

## 8. 文档索引

| 文档 | 作用 |
|---|---|
| [AGENTS.md](AGENTS.md) | 项目宪法：技术栈、目录、编码规范、OCR/密码高压线、验收习惯（AI 助手必读） |
| [docs/SCHEMA.md](docs/SCHEMA.md) | 数据模型契约（16 张表 + 定稿清单），建表与迁移的唯一依据 |
| [docs/API.md](docs/API.md) | 接口契约（38 个接口），前后端的共同语言 |
| [docs/PROGRESS.md](docs/PROGRESS.md) | 进度看板与遗留问题 |
| [docs/DECISIONS.md](docs/DECISIONS.md) | 选型与取舍记录（D-001 ~ D-022），论文"方案选型"的草稿 |
| [docs/EVAL.md](docs/EVAL.md) | 评测口径与实验数字（只读契约，数字只能手工填） |
| [deploy/README.md](deploy/README.md) | 基础服务的启动/重置/镜像拉取与常见问题 |

## 9. 已知限制

- 登录接口尚未接入（S2-2）；当前登录页是占位页，开发态可用占位 token 进入。
- 数据库还没有任何业务表（S2-1 的 Alembic 迁移才建 16 张表）。
- OCR 与密码模块尚未开始（S1 / S4）；`requirements.txt` 里**故意不含** `paddleocr` / `paddlepaddle`。
- 前端未做生产部署（S7-2 的 Nginx + Compose 才收口）；当前只有 dev server。