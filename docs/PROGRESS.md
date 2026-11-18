# PROGRESS.md — 项目进度

> 唯一进度依据。每个任务完成后更新：勾选任务、记录实际产出文件、登记遗留问题。
> 任务编号出自 `docs/OCR票据识别与加密系统-AI协作开发实施计划.md`（S0-1 ~ S7-5，共 59 张卡）。

## 当前状态

- 阶段：**阶段 0（项目启动）**，M0 前段
- 最近完成：**S0-4 基础服务三件套**（PostgreSQL 16 / Redis 7 / MinIO，5 项自检全通过）
- 下一步：**S0-5 后端最小可运行**（config / logging+trace_id / 统一异常 / `/api/v1/health`）
- 已知阻塞：无
- 环境提醒：后端必须用 **Python 3.11**（`py -3.11 -m venv .venv`）；本机默认 `python` 是 3.13，`paddlepaddle` 无对应 wheel

## 任务清单

```
## 阶段 0：项目启动（M0 前半）
[x] S0-1 创建仓库骨架与 .gitignore
[x] S0-2 写项目宪法 AGENTS.md
[x] S0-3 写契约文档 SCHEMA.md + API.md ★关键
[x] S0-4 起基础服务（PostgreSQL 16 + Redis 7 + MinIO）
[ ] S0-5 后端最小可运行：config + 日志/trace_id + 统一异常
[ ] S0-6 前端最小可运行 + axios 封装 + 路由守卫
[ ] S0-7 冒烟脚本与 README 完整化

## 阶段 1：OCR 技术验证（M0 后半）
[ ] S1-1 票据样本集、标注规范与合成票据脚本 ★关键
[ ] S1-2 图像预处理与透视校正 ★关键
[ ] S1-3 PaddleOCR 检测识别最小闭环 ★关键
[ ] S1-4 票种识别与版面区域划分
[ ] S1-5 关键字锚定 + 规则字段抽取 + 校验器 ★关键
[ ] S1-6 端到端基线脚本与指标计算 ★关键

## 阶段 2：基础框架（M1）
[ ] S2-1 模型与首批迁移（16 张表）
[ ] S2-2 JWT 认证 + RBAC 三角色 ★关键
[ ] S2-3 行级数据隔离
[ ] S2-4 分页、筛选与排序
[ ] S2-5 票据表与 CRUD（invoices / invoice_files）
[ ] S2-6 文件上传 + MinIO + sha256 去重 ★关键
[ ] S2-7 ARQ 异步任务框架（状态机 + 重试 + 幂等）
[ ] S2-8 票种模板与规则管理
[ ] S2-9 前端登录与主框架
[ ] S2-10 前端上传页
[ ] S2-11 前端票据列表与详情
[ ] S2-12 定时任务骨架与阶段收口

## 阶段 3：识别流水线上线（M2）
[ ] S3-1 OCR Worker 串起全链路 ★关键
[ ] S3-2 字段级置信度聚合与低置信度确认队列 ★关键
[ ] S3-3 局部二次识别（crop 放大重识别）
[ ] S3-4 识别结果 JSON Schema 与落库
[ ] S3-5 票据详情页：图像与字段并排 + 框选高亮 ★关键
[ ] S3-6 字段修正与确认 API
[ ] S3-7 重复报销检测
[ ] S3-8 批量上传与进度

## 阶段 4：密码模块（M3）
[ ] S4-1 SM3 摘要与 canonical JSON 规范化 ★关键
[ ] S4-2 SM2 签名与验签（防双重哈希） ★关键
[ ] S4-3 SM4-CBC + HMAC-SM3 的 Encrypt-then-MAC 对称层 ★关键
[ ] S4-4 数字信封（SM2 包裹会话密钥） ★关键
[ ] S4-5 密钥生成与私钥口令保护 ★关键
[ ] S4-6 X.509 自建 CA 与角色证书
[ ] S4-7 审计日志哈希链 ★关键
[ ] S4-8 密码模块单测与国标测试向量验证 ★关键
[ ] S4-9 RSA-OAEP+PSS 与 AES-GCM 对照实现

## 阶段 5：报销业务闭环（M4）
[ ] S5-1 报销单创建与明细
[ ] S5-2 签名 → 加密提交链路 ★关键
[ ] S5-3 财务端待审列表
[ ] S5-4 验签与解密 ★关键
[ ] S5-5 审核决策与驳回
[ ] S5-6 归档与检索
[ ] S5-7 端到端联调与异常场景

## 阶段 6：实验与评测（M5）
[ ] S6-1 识别对比实验（CRNN vs SVTR + 预处理消融）
[ ] S6-2 字段抽取对比（纯正则 vs 正则+语义匹配）
[ ] S6-3 密码性能实验 ★关键
[ ] S6-4 安全测试用例
[ ] S6-5 评测报告导出

## 阶段 7：部署、调优与论文支撑（M6）
[ ] S7-1 性能压测与 CPU 推理优化
[ ] S7-2 Docker 一键部署 + 备份
[ ] S7-3 冷启动检查与临时明文零残留扫描
[ ] S7-4 论文图表数据导出
[ ] S7-5 演示环境与录屏脚本
```

## 已完成任务的实际产出

| 任务 | 产出文件 | 验收方式 | 结论 |
|---|---|---|---|
| S0-1 | 目录骨架（`backend/app/{api/v1,models,schemas,services,ocr/templates,crypto/sm,core,tasks,utils}`、`alembic/versions`、`tests`、`eval/{datasets,configs,results}`、`web/src/*`、`docs/{spec,plan}`、`deploy`、`scripts`、`samples`、`secrets`）、`.gitignore`、`.gitattributes`、`README.md` | `find . -type d` 与《完整方案》4.6 逐项对照；`git status` 干净 | ✅ 通过。`.gitignore` 已强制排除 `secrets/` 与 `samples/` 影像 |
| S0-2 | `AGENTS.md`（含 §5 OCR 高压线、§6 密码高压线、§10 编码细则、§10.8 一致性锚点表、§11 spec→plan 协议） | 通读确认"这个项目用什么、不用什么"能一句话说清 | ✅ 通过。锚点表 S0 阶段留空，S0-5 起回填 |
| S0-4 | `deploy/docker-compose.dev.yml`（pg16 + redis7 + minio + 幂等 minio-init，全量 healthcheck）、`deploy/.env.example`、`deploy/init/01_extensions.sql`（pgcrypto）、`deploy/README.md`、`scripts/check_services.py`（纯标准库，5 项检查） | `docker compose up -d` 后 `python scripts/check_services.py` 输出 5/5 OK；`down` 不删卷再 `up` 后 bucket 仍在、`minio-init` 幂等不报错 | ✅ 通过 |
| S0-3 | `docs/SCHEMA.md`（641 行，16 张表，5 组，含「定稿清单」20 项）、`docs/API.md`（646 行，36 个接口，9 组，含 §12 定稿记录）、`docs/DECISIONS.md`（D-001~D-018）、`docs/EVAL.md`、`docs/PROGRESS.md`、`samples/README.md`，并把《完整方案》《实施计划》《AGENTS.md》纳入 `docs/` | ① 表数 16、业务不变式索引齐备；② 接口数 36、分组齐全、全部标注权限/类型/幂等/任务号；③ 两文档字段名交叉核对；④ **契约内无残留 `TODO 待定`** | ✅ 通过。S0-3 结束后无阻塞项 |

## 遗留问题（TECH DEBT）

> **S0-3 已把上一版 7 项全部拍板**（结论见下表），完整决策轨迹在 `docs/SCHEMA.md`「定稿清单」与 `docs/DECISIONS.md` D-011 ~ D-018。
> 这里改为“已定口径”，供回溯；新的遗留问题再往下追加。

| # | 原问题 | 已定结论 | 落地位置 |
|---|---|---|---|
| 1 | `invoices.status` 与方案 6.6 状态图口径冲突 | 票据级只留 5 值，报销单级状态落 `report_forms`；论文把 6.6 拆成两张状态机图 | SCHEMA 定稿 #1 / D-011 |
| 2 | `invoice_files.envelope_id` 循环外键 | **删除该列**，只保留 `envelopes.file_id` + 部分唯一索引 | SCHEMA 定稿 #4 / D-012 |
| 3 | 审计哈希链并发分叉 | 追加时取事务级咨询锁 `pg_advisory_xact_lock` | SCHEMA 定稿 #14 / D-015 |
| 4 | 单文件上限 10MB vs 20MB 口径不一 | **20MB**（`MAX_UPLOAD_MB=20`），单一来源 | API 上传入参 / D-018 |
| 5 | 金额字段人工修改权限未定 | **仅 `reviewer` 可改且需 `confirm_amount=true`**；`admin` 404 | API PATCH 备注 / D-017 |
| 6 | 财务服务器私钥口令托管方式 | 走**环境变量** `FINANCE_KEY_PASSPHRASE`，不经 HTTP | API 验签入参 / D-016 |
| 7 | 是否支持红冲 / 负数票 | **本期不支持**，`total_amount >= 0` 保留 | SCHEMA 定稿 #10 / D-013 |

当前**无阻塞项**。S2-1 建迁移前还需人工复核的只有一项：部分索引与 CHECK 在 Alembic 里的写法（属实现细节，见 SCHEMA 定稿清单末尾）。

## 变更日志

### 2026-11-18

- **S0-4 完成**：基础服务三件套起在本机。
  · 端口与已有 study-agent 错开：PG **5433** / Redis **6380** / MinIO **9002**（控制台 9003），全部只绑 `127.0.0.1`；
  · 数据全落命名卷（`study-ocr-pgdata/redisdata/miniodata`），不用相对路径绑定挂载（Windows 上必炸）；
  · `deploy/init/*.sql` 只在数据卷首次创建时执行，已确认 `pgcrypto` 装上（日志有 `[init] pgcrypto ready`）；
  · `minio-init` 一次性容器用 `mc mb --ignore-existing` 幂等建 bucket 并置 private，重复 `up -d` 不报错；
  · `scripts/check_services.py` 纯标准库（此时后端 venv 还没建）：compose 可用性 / PG `select 1` + pgcrypto /
    Redis AUTH+PING+appendonly / MinIO 健康探针 / MinIO 对象写入-读取-删除，**5 项全 OK**；
  · 镜像从 `pgvector/pgvector:pg16` 换成契约里的 `postgres:16`（重建后自检仍 5/5）；
  · 解决"Docker Hub 直连超时"：`docker pull <裸名>` 会回退到 registry-1 而失败，改用**镜像源全名 + retag**，
    封装为 `scripts/pull_image.sh`（实测 daocloud 源可用，已顺带拉好 S7-2 要用的 `nginx:alpine`）；
  · 决策记 D-019（端口错开 + 镜像拉取路线 + 离线兜底）。
- **S0-1 完成**：初始化 monorepo 骨架；`.gitignore` 覆盖 Python/Node/IDE/日志，并**强制排除** `secrets/`、`samples/` 影像与 `backend/eval/results/` 生成物；补 `.gitattributes`（统一 LF，避免 Windows CRLF 抖动）；README 写明环境要求（**必须 Python 3.11**）、目录说明、文档索引与启动步骤占位。
- **S0-2 完成**：`AGENTS.md` 落盘。除常规技术栈/规范/禁止事项外，把两条高压线写死——OCR（EXIF 先修、降级链、`unclip_ratio ≤ 1.8`、坐标与置信度不丢）与密码（只许 `secrets`、`mode=1`、`sign_with_sm3` 不许混用、Encrypt-then-MAC、不依赖 C3、私钥不落库）。
- **S0-3 定稿**：待复核 20 项 + 接口待定 14 项 + 遗留问题 7 项已逐条拍板，见 SCHEMA「定稿清单」、API §12.1、DECISIONS D-011~D-018。
- **S0-3 完成**：`docs/SCHEMA.md`（16 表）与 `docs/API.md`（36 接口）定稿草案；同步产出 `DECISIONS.md`（D-001~D-010，含与开题不符的三处调整理由）、`EVAL.md`（指标口径与数字纪律）、`PROGRESS.md`、`samples/README.md`。
- 契约阶段敲定的具体口径：
  - 角色只有三个：`submitter` / `reviewer` / `admin`；**财务服务器不是用户**，是 `certificates.owner_type='server'` 的证书主体；
  - 枚举统一 `TEXT + CHECK`（D-007）；金额 `NUMERIC(12,2)` + `Decimal`，禁浮点（D-008）；
  - 业务不变式下沉到库：发票 `(invoice_code, invoice_no) WHERE status<>'void'` 部分唯一、每单只有一条 `active` 签名、`report_forms.idempotency_key` 唯一、`invoice_files.sha256` 唯一（D-009）；
  - 新增三列为契约补缺：`invoices.version` / `report_forms.version`（乐观锁）、`report_forms.report_no`（业务单号）、`invoice_fields.source_page_no`（多页定位）；
  - `invoice_fields.source` 定为 5 值（含二次识别的 `ocr_retry`）；
  - `/health` 与 `/api/v1/health` 两个都提供（容器探针 + 业务前缀），同一实现。
- 未提交/未推送：本次改动均在本地，按约定 **`git push` 由你执行**。