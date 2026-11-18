# PROGRESS.md — 项目进度

> 唯一进度依据。每个任务完成后更新：勾选任务、记录实际产出文件、登记遗留问题。
> 任务编号出自 `docs/OCR票据识别与加密系统-AI协作开发实施计划.md`（S0-1 ~ S7-5，共 59 张卡）。

## 当前状态

- 阶段：**阶段 0（项目启动）**，M0 前段
- 最近完成：**S0-3 契约文档（SCHEMA.md + API.md）**
- 下一步：**S0-4 起基础服务**（PostgreSQL 16 / Redis 7 / MinIO，`docker compose -f deploy/docker-compose.dev.yml up -d`）
- 已知阻塞：无
- 环境提醒：后端必须用 **Python 3.11**（`py -3.11 -m venv .venv`）；本机默认 `python` 是 3.13，`paddlepaddle` 无对应 wheel

## 任务清单

```
## 阶段 0：项目启动（M0 前半）
[x] S0-1 创建仓库骨架与 .gitignore
[x] S0-2 写项目宪法 AGENTS.md
[x] S0-3 写契约文档 SCHEMA.md + API.md ★关键
[ ] S0-4 起基础服务（PostgreSQL 16 + Redis 7 + MinIO）
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
| S0-3 | `docs/SCHEMA.md`（622 行，16 张表，5 组）、`docs/API.md`（618 行，36 个接口，9 个业务分组）、`docs/DECISIONS.md`（D-001~D-010）、`docs/EVAL.md`、`docs/PROGRESS.md`、`samples/README.md`，并把《完整方案》《实施计划》《AGENTS.md》纳入 `docs/` | ① 表数 16、三处业务不变式索引齐备；② 接口数 36、分组齐全、全部标注权限/类型/幂等/任务号；③ 两文档字段名交叉核对，缺口全部落在「待复核清单」/「待定项清单」并有定稿任务 | ✅ 通过。剩余待定项见下 |

## 遗留问题（TECH DEBT）

> 完整清单以 `docs/SCHEMA.md` 文末「待复核清单」与 `docs/API.md` §12「待定项清单」为准，这里只列**会影响后续任务节奏**的几项。

| # | 问题 | 影响 | 计划处理 |
|---|---|---|---|
| 1 | `invoices.status` 与《完整方案》6.6 状态图口径不一致（6.6 把报销单级状态画进了票据级） | 论文状态机图要重画；迁移以 SCHEMA 的 5 值为准 | S2-1（并回改方案 6.6 的图） |
| 2 | `invoice_files.envelope_id` ↔ `envelopes.file_id` 循环外键 | 建表顺序会死锁 | S2-1：只保留 `envelopes.file_id` 外键 |
| 3 | `audit_logs` 哈希链并发写入可能分叉 | 链完整性校验会误报断链 | S2-1/S4-7：单写者队列或 `FOR UPDATE` 锁最后一行 |
| 4 | 单文件大小上限 10MB vs 20MB 两处口径不一（方案 3.2 A1 写 20MB，实施计划写 10MB） | 前端与后端校验不一致 | S2-6 定稿，写进 `.env.example` 与 API.md |
| 5 | 金额字段人工修改的权限边界未定（是否允许 admin 兜底、是否二次确认） | 影响 S3-6 权限矩阵 | S3-6 |
| 6 | 财务服务器私钥口令的托管方式（请求体传入 vs 服务端环境变量） | 影响 S5-4 接口形态与安全说明 | S5-4 |
| 7 | 是否支持红冲/负数票 | 影响 `total_amount >= 0` 约束与 `is_red` 标记 | S1-5 前定（当前按不支持处理） |

## 变更日志

### 2026-11-18

- **S0-1 完成**：初始化 monorepo 骨架；`.gitignore` 覆盖 Python/Node/IDE/日志，并**强制排除** `secrets/`、`samples/` 影像与 `backend/eval/results/` 生成物；补 `.gitattributes`（统一 LF，避免 Windows CRLF 抖动）；README 写明环境要求（**必须 Python 3.11**）、目录说明、文档索引与启动步骤占位。
- **S0-2 完成**：`AGENTS.md` 落盘。除常规技术栈/规范/禁止事项外，把两条高压线写死——OCR（EXIF 先修、降级链、`unclip_ratio ≤ 1.8`、坐标与置信度不丢）与密码（只许 `secrets`、`mode=1`、`sign_with_sm3` 不许混用、Encrypt-then-MAC、不依赖 C3、私钥不落库）。
- **S0-3 完成**：`docs/SCHEMA.md`（16 表）与 `docs/API.md`（36 接口）定稿草案；同步产出 `DECISIONS.md`（D-001~D-010，含与开题不符的三处调整理由）、`EVAL.md`（指标口径与数字纪律）、`PROGRESS.md`、`samples/README.md`。
- 契约阶段敲定的具体口径：
  - 角色只有三个：`submitter` / `reviewer` / `admin`；**财务服务器不是用户**，是 `certificates.owner_type='server'` 的证书主体；
  - 枚举统一 `TEXT + CHECK`（D-007）；金额 `NUMERIC(12,2)` + `Decimal`，禁浮点（D-008）；
  - 业务不变式下沉到库：发票 `(invoice_code, invoice_no) WHERE status<>'void'` 部分唯一、每单只有一条 `active` 签名、`report_forms.idempotency_key` 唯一、`invoice_files.sha256` 唯一（D-009）；
  - 新增三列为契约补缺：`invoices.version` / `report_forms.version`（乐观锁）、`report_forms.report_no`（业务单号）、`invoice_fields.source_page_no`（多页定位）；
  - `invoice_fields.source` 定为 5 值（含二次识别的 `ocr_retry`）；
  - `/health` 与 `/api/v1/health` 两个都提供（容器探针 + 业务前缀），同一实现。
- 未提交/未推送：本次改动均在本地，按约定 **`git push` 由你执行**。