# study-OCR

基于 OCR 的高校报销票据识别与加密系统（本科毕业设计）。

票据图片/PDF 进 → 预处理 → 文本检测识别（带坐标与置信度）→ 关键要素抽取与校验 → 人工确认 → SM3 摘要 + SM2 签名 + SM4 数字信封 → 财务端验签解密 → 审核留痕。

> 完整设计见 [docs/OCR票据识别与加密系统-毕业设计完整方案.md](docs/OCR票据识别与加密系统-毕业设计完整方案.md)，
> 分步实施与任务卡见 [docs/OCR票据识别与加密系统-AI协作开发实施计划.md](docs/OCR票据识别与加密系统-AI协作开发实施计划.md)（任务编号 S0-1 ~ S7-5 均出自该文件）。

## 当前状态

| 里程碑 | 状态 |
|---|---|
| M0 立项与可行性验证 | 进行中（S0 阶段） |
| M1 基础框架 | 未开始 |
| M2 识别流水线上线 | 未开始 |
| M3 密码模块 | 未开始 |
| M4 业务闭环与联调 | 未开始 |
| M5 对照实验与性能测试 | 未开始 |
| M6 论文定稿与答辩 | 未开始 |

已完成任务：S0-1 仓库与工程骨架、S0-2 AGENTS.md、S0-3 SCHEMA.md + API.md。
详细进度与遗留问题见 [docs/PROGRESS.md](docs/PROGRESS.md)。

## 目录结构

```
study-OCR/
├─ backend/                  # 后端（FastAPI）
│  ├─ app/
│  │  ├─ api/v1/             # 路由：auth invoices templates reports finance certs admin eval
│  │  ├─ core/               # 配置、日志(trace_id)、安全、依赖注入、异常
│  │  ├─ models/             # SQLAlchemy 模型（16 张表，见 docs/SCHEMA.md）
│  │  ├─ schemas/            # Pydantic：接口出入参 + 抽取结果 + 签名载荷
│  │  ├─ services/           # 业务服务层
│  │  ├─ ocr/                # 预处理 / 检测 / 识别 / 版面 / 抽取 / 校验 + templates/
│  │  ├─ crypto/             # canonical / sm(2,3,4) / signature / envelope / keystore / certs
│  │  ├─ tasks/              # ARQ 异步任务
│  │  └─ utils/              # 通用工具
│  ├─ alembic/versions/      # 数据库迁移
│  ├─ tests/                 # pytest
│  └─ eval/                  # 离线评测：datasets / configs / results
├─ web/                      # 前端（Vue 3 + TS + Element Plus）
│  └─ src/{api,components,views,stores,router,utils,types}
├─ docs/                     # 契约与记忆：SCHEMA / API / PROGRESS / DECISIONS / EVAL
│  ├─ spec/                  # 功能设计 spec（任务级）
│  └─ plan/                  # 实现 plan（任务级）
├─ deploy/                   # docker-compose、nginx.conf、Dockerfile
├─ scripts/                  # 合成票据、标注转换、基线跑批、冷启动检查
├─ samples/                  # 票据样本（图片不入库，只入库 MANIFEST.md）
└─ secrets/                  # 私钥仓（整目录被 .gitignore 排除）
```

## 环境要求

| 组件 | 版本 | 说明 |
|---|---|---|
| Python | **3.11**（不要用 3.13/3.14） | `paddlepaddle` 尚无 3.13 wheel；本机已有 `py -3.11` |
| Node.js | ≥ 20 | 前端（S0-6 起） |
| Docker Desktop | 最新稳定版 | PostgreSQL 16 / Redis 7 / MinIO（S0-4 起） |

> Windows 上请用 `py -3.11 -m venv .venv` 建虚拟环境，不要用系统默认的 `python`（本机默认是 3.13）。

## 启动步骤

> 待 S0-4（基础服务）、S0-5（后端最小可运行）、S0-6（前端最小可运行）完成后补齐本节；
> S0-7 会用 `scripts/smoke.py` 与 `scripts/cold_start.sh` 把本节补成"新机器 10 分钟跑起来"的版本。

```bash
# 占位：以下命令在对应任务完成前不可用
# 1) 基础服务
# docker compose -f deploy/docker-compose.dev.yml up -d
# 2) 后端
# py -3.11 -m venv .venv && .venv\Scripts\activate
# pip install -r backend/requirements.txt
# alembic upgrade head
# uvicorn app.main:app --reload --port 8000    # 工作目录 backend/
# curl http://localhost:8000/api/v1/health
# 3) 前端
# cd web && npm install && npm run dev
```

## 文档索引

| 文档 | 作用 |
|---|---|
| [AGENTS.md](AGENTS.md) | 项目宪法：技术栈、目录、编码规范、禁止事项、验收习惯（AI 助手必读） |
| [docs/SCHEMA.md](docs/SCHEMA.md) | 数据模型契约，建表与迁移的唯一依据 |
| [docs/API.md](docs/API.md) | 接口契约，前后端的共同语言 |
| [docs/PROGRESS.md](docs/PROGRESS.md) | 进度看板与遗留问题 |
| [docs/DECISIONS.md](docs/DECISIONS.md) | 选型与取舍记录（论文"方案选型"章节的草稿） |
| [docs/EVAL.md](docs/EVAL.md) | 评测口径与实验数字（只读契约，数字只能手工填） |

## 常见问题

> 待 S0-7 冷启动检查后补齐（Windows 特有问题、paddlepaddle 安装、Docker 卷路径等）。