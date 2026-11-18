# AI 协作开发实施计划
## 面向《基于 OCR 的高校报销票据识别与加密系统》的分步执行手册（指导 AI 编码用）

> 配套文档：`OCR票据识别与加密系统-毕业设计完整方案.md`（做什么 / 为什么这么做）
> 本文档解决的是：**怎么做、按什么顺序做、每一步怎么跟 AI 说话、怎么验收。**
>
> 适用工具：Cursor / Claude Code / Codex / DSH 等任意 AI 编码助手。
> 核心思想一句话：**把"记忆"写进仓库文件，把"任务"切成半天能完成的小卡片，把"验收"变成可执行的命令。**

---

## 目录

- [第 0 章 先读这一章：AI 协作开发的铁律](#第-0-章-先读这一章ai-协作开发的铁律)
- [第 1 章 会话标准流程（每次开发都照做）](#第-1-章-会话标准流程每次开发都照做)
- [第 2 章 仓库的"记忆系统"：6 个文件让 AI 不失忆](#第-2-章-仓库的记忆系统6-个文件让-ai-不失忆)
- [第 3 章 阶段 0：项目启动（S0，7 个任务）](#第-3-章-阶段-0项目启动s07-个任务)
- [第 4 章 阶段 1：OCR 技术验证（S1，6 个任务，最重要）](#第-4-章-阶段-1ocr-技术验证s16-个任务最重要)
- [第 5 章 阶段 2：基础框架（S2，12 个任务）](#第-5-章-阶段-2基础框架s212-个任务)
- [第 6 章 阶段 3：识别流水线上线（S3，8 个任务）](#第-6-章-阶段-3识别流水线上线s38-个任务)
- [第 7 章 阶段 4：密码模块（S4，9 个任务）](#第-7-章-阶段-4密码模块s49-个任务本课题重点)
- [第 8 章 阶段 5：报销业务闭环（S5，7 个任务）](#第-8-章-阶段-5报销业务闭环s57-个任务)
- [第 9 章 阶段 6：实验与评测（S6，5 个任务）](#第-9-章-阶段-6实验与评测s65-个任务)
- [第 10 章 阶段 7：部署、调优与论文支撑（S7，5 个任务）](#第-10-章-阶段-7部署调优与论文支撑s75-个任务)
- [第 11 章 提示词模板库（直接复制用）](#第-11-章-提示词模板库直接复制用)
- [第 12 章 让 AI 干活时最容易翻车的 12 个地方](#第-12-章-让-ai-干活时最容易翻车的-12-个地方)
- [第 13 章 什么必须你自己做（AI 不能替）](#第-13-章-什么必须你自己做ai-不能替)
- [第 14 章 全部任务总表（可当进度看板）](#第-14-章-全部任务总表可当进度看板)
- [附录 A AGENTS.md 完整模板（复制即用）](#附录-a-agentsmd-完整模板复制即用)
- [附录 B PROGRESS.md / API.md / DECISIONS.md 模板](#附录-b-progressmd--apimd--decisionsmd-模板)
- [附录 C 每周节奏与打卡表](#附录-c-每周节奏与打卡表)

---

## 第 0 章 先读这一章：AI 协作开发的铁律

这 13 条是从"AI 把项目写崩"的教训里提炼的，**违背任何一条，两周内必然返工**。本课题比一般 Web 项目多两个雷区（OCR 库版本、密码协议细节），所以第 7、8、13 条要比别的项目更严。

| # | 铁律 | 为什么 |
|---|---|---|
| 1 | **契约先行**：先写 `SCHEMA.md`（16 张表）和 `API.md`（接口清单），再让 AI 写实现 | 没有契约，AI 会一边写 `invoice_code` 一边写 `invoiceCode`，前后端对不上 |
| 2 | **一次一个任务卡**：单次会话只做一件半天能完成、能验收的事 | 一次给 5 个需求，AI 会挑简单的做，半成品混进代码 |
| 3 | **每步可运行**：任何时刻 `git pull` 下来项目都能启动 | 长时间半成品状态是灾难，报错会叠加到无法定位 |
| 4 | **不相信"已完成"**：AI 说完成不算，跑验收命令、点界面、查库才算 | AI 会"汇报式完成"——文件建了、函数写了、但没跑通 |
| 5 | **小步提交**：一个任务卡一个 commit，message 写清做了什么 | 出问题能 `git revert`，也方便写论文工作量说明 |
| 6 | **禁止顺手重构**：明确告诉 AI"只改这几个文件" | AI 的"优化"会波及已完成模块 |
| 7 | **关键算法自己读懂**：预处理兜底链、字段锚定打分、信封封装格式、MAC 覆盖范围、规范化规则——必须能对着代码口述 | 这五处是本课题的技术含量所在，答辩只问这些 |
| 8 | **不把密钥和真实票据给 AI**：私钥、口令、`.env`、**含个人信息的票据原图**都不进对话、不进仓库 | 泄漏风险 + 提交历史污染 + 个人信息合规问题。需要给 AI 看图时，用合成票据或脱敏图 |
| 9 | **不让 AI 造假数据**：实验数字、抽查结果、问题记录必须真实 | 学术诚信红线，代价是毕业 |
| 10 | **报错给原文**：贴完整堆栈 + 相关代码 + 期望现象，别只说"报错了" | 信息不足时 AI 会瞎猜，改错地方浪费一小时 |
| 11 | **前后端分开做**：先做后端接口用 `curl` 自测通过，再做页面 | 一起做出问题不知道怪谁，调试成本翻倍 |
| 12 | **文档跟着代码走**：每个任务完成后更新 `PROGRESS.md` / `API.md` | 仓库文档就是 AI 的长期记忆，文档滞后 = 下一轮开始瞎编 |
| 13 | **能用现成的就别自己写**，但**业务逻辑必须自己写** | 复用库省时间；而"链路怎么搭、规则怎么定"正是论文本身（展开见下） |

### 第 13 条展开：复用优先（但要划清"库"与"业务"的线）

**判断标准：这个库会不会接管我的主流程？**

| 类型 | 态度 | 例子 |
|---|---|---|
| 原子能力库 | ✅ 直接用 | OpenCV、NumPy、Pillow、gmssl、cryptography、jieba |
| 基础设施 | ✅ 直接用 | PostgreSQL、Redis、MinIO、Nginx、Docker |
| 业务脚手架 | ✅ 直接用 | FastAPI、SQLAlchemy、Alembic、Element Plus、pytest |
| 黑盒服务 | ⚠️ 慎用 | 商业票据 OCR API（数据出网、结果不可解释、论文体现不了工作量） |

**三个判断问题**（任一条答"不能"，就换掉或自己写）：

1. 它出错时，我能看到中间结果吗？（PaddleOCR 能给每行坐标与分数 ✅；云 API 只给最终 JSON ❌）
2. 我要改它的默认行为时，容易吗？（阈值、预处理参数、抽取规则都要能改 ✅）
3. 论文里我能说清"这一层我做了什么"吗？

**本项目"必须自己写"的 8 块**（论文工作量所在）：
预处理策略与失败兜底链、版面分块与票种判定、字段锚定打分与语义兜底、校验规则库与冲突消解、置信度聚合与二次识别触发、签名域规范化、信封封装格式与 Encrypt-then-MAC、审计哈希链与评测脚本。

**本项目"绝对不要自己写"的 5 样**：卷积网络、椭圆曲线运算、ASN.1/PEM 编解码、JWT 与密码哈希、分页。

**给你的下任务卡话术**：在每张任务卡的提示词开头加一句 ——

```
本任务优先复用成熟库。如果你打算自己实现某个通用能力（图像预处理算子、
密码算法、证书解析、分页、认证、定时任务），请先停下来告诉我：
有没有现成的库？推荐哪个？为什么？不要直接开写。
```

### 三条本课题专属的额外铁律

| # | 铁律 | 为什么 |
|---|---|---|
| 14 | **全部依赖锁死版本号**（尤其 `paddleocr==2.7.3`、`gmssl==3.2.2`） | PaddleOCR 2.7 与 3.x 的 `ocr()`/`predict()` 返回结构完全不同；`gmssl` 与 `gmssl-python` 是两个不同的包。**版本一漂，代码全废** |
| 15 | **密码代码必须过"标准测试向量"才算完成** | "自己能签自己能验"是循环自证，等于没测。SM3/SM4 用国家标准向量，SM2 用固定密钥的往返 + 篡改用例 |
| 16 | **临时明文零残留** | 识别链路会解出明文影像，任何 `tmp/` 写入都必须 `finally` 删除；S7 有专门的扫描任务 |

### 判断 AI 是否在糊弄你（出现任一，立刻停下来追问）

- 说"已完成"但没给出**运行/测试命令和输出**
- 一次性改了 8 个以上文件，却说不出每个文件为什么改
- 用了仓库里不存在的依赖、字段、函数名（**幻觉 API**）
- 说"用 `gmssl` 生成密钥对"——`gmssl`（纯 Python 版）**没有**密钥生成接口，这是典型幻觉
- 说"用 SM4-GCM 加密"——`gmssl` **只支持 ECB/CBC**，没有 GCM
- 说"验签通过了"但**没跑过标准测试向量**，只是自己签自己验
- 把功能"简化"了但没告诉你（比如说好的"先验 MAC 再解密"变成了"解完再说"）
- 报错时加个 `try/except: pass` 就说"修好了"
- 单元测试写得像复读机（`assert result is not None`）
- 说"这个功能已经实现"，但你点界面上没有入口

---

## 第 1 章 会话标准流程（每次开发都照做）

每次坐下来写代码，都走这 7 步。熟练后约 2 分钟准备 + 3 分钟收尾，能省掉大量返工。

### 步骤 1：开场（让 AI 加载记忆）
```
请先读以下文件，然后告诉我项目当前状态与你理解的技术约束：
- AGENTS.md
- docs/PROGRESS.md
- docs/API.md（只看与本任务相关的部分）
- docs/SCHEMA.md（只看相关表）
读完后用 5 句话总结：项目做什么、当前完成到哪、下一步是什么、有哪些硬约束。
不要写任何代码。
```

### 步骤 2：下达任务卡（一次一张）
```
本次任务：S4-2 SM2 签名与验签封装（见 docs/PLAN.md 的任务卡）
目标：对规范化 JSON 完成 SM2 签名与验签，能通过标准测试向量
产出文件：backend/app/crypto/signature.py、tests/test_signature.py
约束：只新增上述文件，不要修改其他已有文件；不要引入新依赖；
      统一使用 sign_with_sm3 / verify_with_sm3，不要混用 sign(data, K)
先给我一个执行计划（改哪些文件、每个文件做什么、怎么验证），等我确认后再写代码。
```

### 步骤 3：审计划（这一步不能省）

重点看四件事：
1. 要改的文件是否**超出任务卡范围**？超了就砍。
2. 是否用了**不存在的依赖/字段/API**？对照 `requirements.txt` 和 `SCHEMA.md` 检查（密码相关的 API 尤其容易被编出来）。
3. 是否遗漏了**错误处理 / 边界情况**（空结果、超时、空图、坏填充、MAC 不匹配）？
4. 密码相关：**随机数从哪来？模式参数写了吗？完整性怎么保证？**

### 步骤 4：执行

小步走。如果 AI 一次输出超过 300 行新代码，要求拆成两次：先接口 + 骨架，再实现细节。

### 步骤 5：验收（必须动手跑）

| 任务类型 | 验收命令 |
|---|---|
| 后端接口 | `pytest tests/test_xxx.py -q` + `curl` 打接口 + `psql` 查库 |
| OCR | 跑一张真实/合成票据，**打开输出图与文本框肉眼核对**（不能只看数字） |
| 密码 | 跑标准测试向量 + 往返 + 篡改用例（改 1 bit 必须失败） |
| 前端 | 浏览器点一遍主流程 + 刷新看状态是否持久 |
| 数据 | `SELECT count(*) FROM invoice_fields WHERE source='manual';` 确认真的入库 |

**把输出结果发给 AI**，让它确认或修复。

### 步骤 6：收尾（让 AI 更新记忆）
```
任务 S4-2 已完成并通过验收。请：
1) 更新 docs/PROGRESS.md：把 S4-2 标记完成，记录实际产出文件与遗留问题
2) 如接口有变化，更新 docs/API.md 对应条目；如表有变化，更新 docs/SCHEMA.md
3) 如新增了环境变量/依赖，更新 .env.example 与 requirements.txt
4) 生成一条符合 Conventional Commits 的 commit message
不要修改业务代码。
```

### 步骤 7：提交
```bash
git add -A
git commit -m "feat(crypto): SM2 签名验签封装与标准向量自测"
```

> **每周结束时**：打一次 tag（`git tag m3-crypto-done`），写 3 行周报（完成 / 卡点 / 下周）。周报是后期论文"工作量说明"的原始材料。
> **每两周**：把周报整理成一页纸发给导师（完成 / 计划 / 卡点 / 需协助）。

---

## 第 2 章 仓库的"记忆系统"：6 个文件让 AI 不失忆

长周期项目最大的敌人不是技术难度，是 **AI 的上下文丢失**。解决办法是把"记忆"从对话搬到文件里。

| 文件 | 作用 | 谁维护 | 更新频率 |
|---|---|---|---|
| `AGENTS.md` | 项目宪法：技术栈、目录、编码规范、禁止事项、验收习惯 | 你（附录 A 有完整模板） | 一次写好，重大变更时改 |
| `docs/PROGRESS.md` | 进度看板：任务状态、产出文件、遗留问题 | AI 每次任务后更新 | 每个任务 |
| `docs/API.md` | 接口契约：路径、方法、入参、出参、错误码 | AI 随接口开发更新 | 每次改接口 |
| `docs/SCHEMA.md` | 数据模型：16 张表的字段、类型、索引、枚举 | 你自己定，AI 只能读 | 需求变更时 |
| `docs/DECISIONS.md` | 决策记录：选型理由、放弃的方案、为什么 | 你（关键决策） | 不定期 |
| **`docs/EVAL.md`** | **评测口径**：指标定义、数据集清单、基线数字、每次实验的配置与结果 | 你（**AI 只能读，不能改数字**） | 每次实验 |

> 第 6 个文件是本课题特有的。**识别准确率、密码耗时这些数字如果让 AI "顺手填"，你的实验章节就废了。** `EVAL.md` 必须是只读契约：AI 可以帮你写脚本算指标，但表格里的数字只能由你跑完后手工填。

### 为什么这 6 个文件比"把代码全贴给 AI"更有效

- **token 效率**：贴全量代码要几万 token，读 6 个文档只要几千。
- **一致性**：`API.md` 是前后端的共同语言，AI 不会一边写 `invoice_id` 一边写 `invoiceId`。
- **可交接**：换工具、换电脑、隔两周回来，加载文档就能续上。
- **论文素材**：`DECISIONS.md` 直接就是论文"系统设计"里"方案选型"部分的草稿；`EVAL.md` 直接就是第 6 章的数据源。

### 两个必须额外维护的文件（不是给 AI 看的，是给你的合规与复现兜底）

| 文件 | 内容 | 为什么 |
|---|---|---|
| `samples/MANIFEST.md` | 每张票据样本的来源、是否合成、脱敏方式、授权说明 | 论文"数据集"章节要写；也是合规证据 |
| `scripts/synth_invoice.py` | 合成票据生成脚本（版式 + 随机字段 + 扰动） | 样本不够时可无限生成，且标注天然准确 |

### 一个重要习惯：先让 AI 读，再让 AI 写

永远不要让 AI 凭记忆改代码。会话开头加一句：

```
动手前，请先用 grep/read 查看与本次任务相关的现有文件，确认现有实现方式后再写代码。
对于你引用的每个函数/字段，请标注它来自哪个文件。
```

**密码模块尤其要这样**：AI 很容易"记得"某个加密库有某个方法（其实没有），让它先 `grep` 一遍现有封装再动手。

---

## 第 3 章 阶段 0：项目启动（S0，7 个任务）

> 本阶段只搭骨架：仓库、契约文档、三个基础服务、前后端空壳，**不写任何业务逻辑**。代码落在 `backend/app/{core,models,schemas,api/v1}`、`web/src/{api,router,stores}`、`deploy/`、`scripts/`。
> **周期**：4–6 天。**验收**：新机器 `git clone` 后按 README 在 10 分钟内起前后端与三件套，`/api/v1/health` 返回 OK。

---

#### S0-1 创建仓库骨架与 .gitignore

- **目标**：monorepo 目录一次成型，`samples/` 里的真实票据永不入 git
- **产出**：目录骨架 + `.gitkeep`、`.gitignore`、`README.md`
- **提示词（复制）**：
```
在 E:\BiSheXZY 建 monorepo 骨架，只建目录与 .gitkeep，不写业务代码：backend/app/{core,api/v1,models,schemas,services,ocr,crypto,tasks,utils}、backend/app/ocr/templates/、backend/{alembic,tests,eval/{datasets,configs,results}}、web/src/{api,components,views,stores,router,utils,types}、docs/ deploy/ scripts/ samples/
1) .gitignore 覆盖 .venv/、__pycache__/、*.env、node_modules/、dist/、.idea/、.vscode/；samples/ 下只提交 .gitkeep，票据影像一律忽略（含个人信息，进了 git 撤不回）
2) README.md 只写一句话简介（基于 OCR 的高校报销票据识别与加密系统）、目录说明、启动步骤占位；不要 git init、不装依赖、不生成 requirements.txt（属 S0-5）
先给执行计划，等我确认后再写代码。
```
- **验收**：`git status` 干净；`git check-ignore samples/a.jpg` 有输出；目录树与契约完全一致
- **坑**：别让 AI 顺手初始化 FastAPI/Vue（那是 S0-5/S0-6），会破坏一步一验证；`samples/` 一旦 commit，后续清洗要重写 git 历史

---

#### S0-2 写项目宪法 AGENTS.md

- **目标**：把技术栈、目录约定、禁止事项固化，作为后续所有任务卡的常驻上下文
- **产出**：`AGENTS.md`
- **提示词（复制）**：
```
生成 AGENTS.md 作为常驻约束，四节：技术栈 / 目录与命名 / 禁止事项 / 验收习惯。
技术栈写死：Python 3.11 + FastAPI + SQLAlchemy 2.0 + Alembic + Pydantic v2；PostgreSQL 16 + Redis 7 + MinIO；ARQ + APScheduler；PaddleOCR 2.7.3（锁死，禁止 3.x）、OpenCV 4.x；国密库用 gmssl（PyPI: gmssl，纯 Python，pip 即装）；cryptography + OpenSSL 3.x 命令行（生成 SM2 密钥对、自建 CA）；Vue 3 + Vite + TS + Element Plus + Pinia。
禁止：新增未列出依赖、编造第三方 API（不确定写 TODO 并问我）、私钥/口令进库或进日志、擅自改契约文档的表名与字段名。验收：每任务给可复制的验收命令、跑 pytest、列改动文件清单。
先给执行计划，等我确认后再写代码。
```
- **验收**：通读一遍，能对着它回答"这个项目用什么、不用什么、不许做什么"
- **坑**：这份文件决定 59 个任务的代码风格一致性；PaddleOCR 版本、gmssl 的能力边界（无 SM2 keygen、SM4 无 GCM、无证书 API）必须写进去，并明确"gmssl-python 是可选升级路线，不进主线"，否则 AI 会编 API

---

#### S0-3 ★关键 写契约文档 SCHEMA.md + API.md

- **目标**：16 张表与 8 个前缀的接口清单落盘，这是整个项目最重要的一次性投入
- **产出**：`docs/SCHEMA.md`、`docs/API.md`
- **提示词（复制）**：
```
只写 Markdown 表格，不写 SQL、不写建表代码。docs/SCHEMA.md 按模块分组，覆盖 16 张表且一张不多一张不少：users、certificates、invoice_files、invoices、invoice_fields、ticket_templates、field_rules、ocr_jobs、ocr_lines、report_forms、report_items、signatures、envelopes、audit_logs、review_logs、key_records；每表列字段名/类型/可空/默认值/说明/索引，JSONB 字段必须写出结构。
枚举单独一节定死：角色 submitter|reviewer|admin；ocr_jobs.status=pending|running|succeeded|failed（low_confidence 是字段级标记，不是任务状态）；单据状态 draft|pending_sign|signed|encrypted|submitted|approved|rejected|archived。docs/API.md 按 /api/v1/auth、/invoices、/templates、/reports、/finance、/certs、/admin、/health 分组，列方法/路径/入参/出参概要/所需角色，标注异步与幂等接口；不确定处写 "TODO 待定"，不要造字段。
先给执行计划，等我确认后再写代码。
```
- **验收**：全文表名/字段名只出现一套写法；枚举值全文一致；命名统一 snake_case 并定死
- **坑**：AI 会发明第 17 张表和 `invoice.amount_total` 这类近义字段；这里定错，S2 迁移与 S5 全链路返工。后续任务只能引用、不能新增

---

#### S0-4 起基础服务（PostgreSQL 16 + Redis 7 + MinIO）

- **目标**：`docker compose up -d` 一键起三件套，MinIO bucket 自动就绪
- **产出**：`deploy/docker-compose.dev.yml`、`deploy/.env.example`、`scripts/check_services.py`
- **提示词（复制）**：
```
创建 deploy/docker-compose.dev.yml：postgres:16（5432，命名卷 pgdata）、redis:7-alpine（6379，开 AOF）、minio（9000/9001，命名卷 miniodata），全部写 healthcheck。口令一律从环境变量读，另给 deploy/.env.example。加一个一次性 init 容器用 mc 幂等创建 bucket（bucket 名从 .env 读）。
写 scripts/check_services.py：连 PostgreSQL 执行 select 1、Redis ping、MinIO 建/删测试对象，逐项打印 [OK]/[FAIL]+原因，退出码非 0 表示失败。再写 deploy/init/01_extensions.sql（建 pgcrypto，为后续预留）。
先给执行计划，等我确认后再写代码。
```
- **验收**：`docker compose -f deploy/docker-compose.dev.yml up -d && python scripts/check_services.py` 三项 OK；重启容器 bucket 仍在
- **坑**：Windows 上别用 `./data:/var/lib/postgresql/data` 相对路径绑定挂载（权限/换行必炸），用命名卷；MinIO init 要幂等，重启不能报错

---

#### S0-5 后端最小可运行：config + 日志/trace_id + 统一异常

- **目标**：FastAPI 能起、`/api/v1/health` 返回、配置校验失败启动即报错、日志带 trace_id
- **产出**：`backend/app/{main.py,core/config.py,core/logging.py,core/exceptions.py}`、`requirements.txt`
- **提示词（复制）**：
```
创建 FastAPI 最小骨架（不含业务）。main.py 用 create_app()，注册 GET /api/v1/health 返回 {status,version,ts}，CORS 允许来源从配置读。core/config.py 用 pydantic-settings 读 .env（DATABASE_URL、REDIS_URL、MINIO_ENDPOINT/ACCESS_KEY/SECRET_KEY/BUCKET、JWT_SECRET、LOG_LEVEL），缺必填项启动即抛错。core/logging.py 输出 JSON 结构化日志，trace_id 用 contextvar + 中间件注入（优先取 X-Trace-Id，缺省 uuid4），每条日志必须带 trace_id。core/exceptions.py 定义 AppError 基类 + 全局 handler，统一返回 {code,message,data:null,trace_id}，未捕获异常同格式且不泄露堆栈。requirements.txt 全部固定版本。
先给执行计划，等我确认后再写代码。
```
- **验收**：`uvicorn app.main:app --reload`；`curl -s localhost:8000/api/v1/health`；访问不存在路由，响应含 code/message/trace_id 且日志能按 trace_id 串起
- **坑**：trace_id 中间件与日志必须打通，后面 8 个阶段排查全靠它；别同时引入 loguru 与 structlog 两套日志

---

#### S0-6 前端最小可运行 + axios 封装 + 路由守卫

- **目标**：Vue 3 工程能起，请求封装统一处理 token 与业务错误码
- **产出**：`web/src/{main.ts,api/http.ts,router/index.ts,stores/user.ts,views/Login.vue,views/Debug.vue}`
- **提示词（复制）**：
```
用 Vue 3 + Vite + TS 搭骨架，全局注册 Element Plus、Pinia、Vue Router、axios。src/api/http.ts：baseURL 取 import.meta.env.VITE_API_BASE，请求拦截注入 Authorization: Bearer <token>，响应拦截统一解包 {code,message,data}，401 清 token 跳 /login、403 提示无权限、其余 ElMessage.error(message) 并 reject 原始错误。
router 建 /login、/、/debug 三条路由 + 全局前置守卫（无 token 去 /login，预留 meta.roles）。/debug 页调用 /api/v1/health 显示结果与 trace_id。补 .env.development/.env.production/.env.example。
先给执行计划，等我确认后再写代码。
```
- **验收**：`npm run dev` 打开 `/debug` 看到后端 health 结果；浏览器 Network 无 CORS 报错
- **坑**：CORS 与 Vite proxy 二选一，两个都配会出现难以理解的 404；token 存 localStorage 的决定要写进 `docs/DECISIONS.md`（S4 讨论私钥存储会引用）

---

#### S0-7 冒烟脚本与 README 完整化

- **目标**：一条命令回答"环境到底行不行"
- **产出**：`scripts/smoke.py`、完整 `README.md`、`docs/DECISIONS.md`（首条 ADR）
- **提示词（复制）**：
```
写 scripts/smoke.py：依次检查 PostgreSQL 可连、Redis ping、MinIO bucket 存在、后端 /api/v1/health、alembic_version 表是否存在（无表则提示"请先执行 alembic upgrade head"），逐项 [OK]/[FAIL]+失败原因，末尾汇总，退出码非 0 表示失败；所有参数从 .env 读，不硬编码；服务全没起时要给人话提示而不是 traceback。
补全 README.md：环境要求（Python 3.11 / node 18+ / Docker）、启动顺序、逐步命令、Windows 特有问题（路径、换行、端口占用）。在 docs/DECISIONS.md 记第一条 ADR：为什么选 PostgreSQL 16 + Redis 7 + MinIO + ARQ。
先给执行计划，等我确认后再写代码。
```
- **验收**：新目录 clone 后照 README 走一遍，`python scripts/smoke.py` 全绿
- **坑**：冒烟脚本要能区分"服务没起"与"配置错"；打完本卡打 tag `m0-env-ready`

---

## 第 4 章 阶段 1：OCR 技术验证（S1，6 个任务，最重要）

> 本阶段代码全部落在 `backend/app/ocr/` 与 `scripts/`，**只用命令行脚本跑，不接 Web、不建表**。
> **周期**：3–4 周。**验收**：`python scripts/ocr_baseline.py --dir samples/ --config baseline` 输出每张票据的字段 JSON，并在自建测试集上得到第一组 检测 Hmean / 字符准确率 / 字段级准确率 / 整单正确率。

---

#### S1-1 ★关键 票据样本集、标注规范与合成票据脚本

- **目标**：有可复现的评测集，且不碰真实发票个人信息
- **产出**：`scripts/make_samples.py`、`backend/eval/configs/synth.yaml`、`backend/eval/datasets/annotations.jsonl`、`scripts/check_annotations.py`
- **提示词（复制）**：
```
建 OCR 评测数据集。scripts/make_samples.py 用 Pillow + 系统中文字体生成四类合成票据（增值税普通发票、火车票、出租车票、定额发票），含中文文本、金额、日期、税号、印章色块，并支持旋转/透视/光照/噪声参数（读 backend/eval/configs/synth.yaml）。禁止抓取真实票据图片。
backend/eval/datasets/annotations.jsonl 每行 {"image","ticket_type","boxes":[{"label","text","bbox":[x1,y1,x2,y2]}],"fields":{"invoice_code","invoice_no","invoice_date","buyer_name","buyer_tax_no","seller_name","seller_tax_no","amount","tax_amount","total_amount","check_code","passenger_name"}}。写 scripts/check_annotations.py 校验格式、bbox 在图内、字段值能在 boxes 文本里找到。
先给执行计划，等我确认后再写代码。
```
- **验收**：`python scripts/make_samples.py --n 40` 出 40 张；`python scripts/check_annotations.py` 全通过
- **坑**：别用真实发票做数据集（发票号、税号是敏感信息）；合成样本必须故意含倾斜、阴影、印章遮挡，否则指标虚高，S6 消融结论会被推翻

---

#### S1-2 ★关键 图像预处理与透视校正

- **目标**：任意手机照片进来都不躺倒、不扭曲，失败也能降级不阻断
- **产出**：`backend/app/ocr/preprocess.py`、`scripts/preprocess_demo.py`、`backend/tests/test_preprocess.py`
- **提示词（复制）**：
```
实现 backend/app/ocr/preprocess.py，签名固定：preprocess(path, *, denoise=True, rectify=True, illum=True, detect_seal=True) -> Result{image, quality_flag, corners, seal_boxes, warnings}。
1) 先 Pillow ImageOps.exif_transpose 修方向，再转 RGB/灰度；灰度可选 fastNlMeansDenoising。
2) 透视校正：Canny → findContours → approxPolyDP 取最大凸四边形做四点变换；找不到则 minAreaRect 旋转校正兜底；再失败原图直送并 quality_flag="low"，绝不抛异常。
3) 照度校正用背景估计（大核形态学闭运算或 divide 归一化）；detect_seal=True 时用 HSV 红/蓝双区间掩膜定位印章，只记录 seal_boxes，不做 inpaint。
4) scripts/preprocess_demo.py --dir 批量出前后对照图到 backend/eval/results/preprocess/。单测覆盖：无四边形、纯色图、EXIF 带旋转的 jpg、超小图。
先给执行计划，等我确认后再写代码。
```
- **验收**：对 40 张合成样本 + 真实拍照样本跑 demo，肉眼确认不躺倒不扭曲；统计 quality_flag 分布并记入 `docs/PROGRESS.md`
- **坑**：① 必须先 EXIF 方向修正，否则整条链路全错；② 二值化/去印章会掉检测精度（DBNet 在自然彩色图训练），只能做开关并消融；③ 印章只标记不 inpaint，修补会吃掉笔画；④ 透视失败要降级并打标，不能抛异常

---

#### S1-3 ★关键 PaddleOCR 检测识别最小闭环

- **目标**：给定图片输出带坐标与置信度的文本行，作为后续所有模块的输入契约
- **产出**：`backend/app/ocr/{detector.py,recognizer.py}`、`scripts/ocr_demo.py`
- **提示词（复制）**：
```
封装 PaddleOCR 2.7.3（PP-OCRv4，CPU）。detector.py：PaddleOCR(use_angle_cls=True, lang='ch', show_log=False)，det_db_unclip_ratio 默认 1.6、det_db_box_thresh=0.3、det_limit_side_len=1600（票据密集小字，起步 1600；压测可降 1280 换速度），全部从配置读；调用固定 ocr.ocr(img, cls=True)，解析返回 [[[[四点坐标], (text, score)], ...]] 成 OcrLine{text,score,box,line_no}，按 y 再 x 排序，空结果返回 [] 不抛异常。recognizer.py 暴露 recognize_line(crop_img) 供 S3-3 复用，模型目录与 device 走配置。
scripts/ocr_demo.py --image 打印行文本/置信度/坐标，--dir 批量统计平均置信度与单张耗时；OcrLine 用 Pydantic 定义。requirements.txt 锁 paddleocr==2.7.3、paddlepaddle==2.6.x。
先给执行计划，等我确认后再写代码。
```
- **验收**：`python scripts/ocr_demo.py --image samples/synth_001.png` 输出 ≥10 行且中文无乱码；同图两次结果一致
- **坑**：① 2.7.3 是 `ocr.ocr(img, cls=True)`，3.x 改 `predict()` 返回 rec_texts/rec_scores/dt_polys，**不兼容，必须锁版本**；② 票据文本密集，`det_db_unclip_ratio` 别超 1.7，否则相邻行框粘连导致串行；③ 首次运行会下载模型，离线机器要预置 `~/.paddleocr`

---

#### S1-4 票种识别与版面区域划分

- **目标**：先判票种，再按模板把文本行分到 header/body/footer
- **产出**：`backend/app/ocr/layout.py`、`backend/app/ocr/templates/*.yaml`、可视化 debug 输出
- **提示词（复制）**：
```
实现 backend/app/ocr/layout.py。classify_ticket(image, lines) -> (ticket_type, score)：四类票各一个 templates/{vat_general,train_ticket,flight_itinerary,taxi_ticket}.yaml，存关键字集合、相对位置权重与长宽比先验；按命中关键字加权 + 长宽比打分，低于阈值返回 ("unknown", score)。
划分区域：模板里定义 header/body/footer 的归一化矩形，按 bbox 中心点把 OcrLine 归区；未知票种全部归 body 并打 warning，下游 extractor 走通用规则。提供 debug 可视化把区域框与行标签画到图上，存 backend/eval/results/layout/。
先给执行计划，等我确认后再写代码。
```
- **验收**：40 张合成样本票种识别准确率 ≥0.95；未知票种不抛异常且字段仍能抽出
- **坑**：别上深度学习分类器（40 张必过拟合，答辩也难解释）；区域坐标必须归一化，写死像素值换分辨率就废

---

#### S1-5 ★关键 关键字锚定 + 规则字段抽取 + 校验器

- **目标**：从 OcrLine 抽出 7 个字段，并独立校验其合法性
- **产出**：`backend/app/ocr/{extractor.py,validators.py}`、`backend/app/ocr/templates/field_rules.yaml`、`backend/tests/test_validators.py`
- **提示词（复制）**：
```
实现 extractor.py：规则（先放 templates/field_rules.yaml，S2 再入 field_rules 表）含 anchor 同义词表（如 ["价税合计","合计金额","小写"]）、方向 right|below、regex、max_distance、required、priority；抽 invoice_code、invoice_no、invoice_date、amount、tax_amount、total_amount、check_code、buyer_tax_no（其余字段按票种模板选配），每字段返回 {value, raw_text, source_line_no, confidence, rule_id}。
实现 validators.py（全部用 Decimal）：total == amount + tax，容差 Decimal("0.01")；大写金额与 total 一致（处理整/正/角/分）；日期可解析且不晚于今天；发票代码 10 或 12 位、号码 8 位纯数字；统一社会信用代码 18 位，字符集排除 I/O/S/V/Z，权重 [1,3,9,27,19,26,16,17,20,29,25,13,8,24,10,30,28]，校验码 = 31 − (Σ 加权和 mod 31)，等于 31 时取 0。校验失败写 errors[] 并把该字段 confidence 折半，不抛异常。单测覆盖每个校验器的通过与失败样例。
先给执行计划，等我确认后再写代码。
```
- **验收**：`pytest backend/tests/test_validators.py -q` 全绿；对 40 张样本跑抽取并打字段级准确率表；故意改一位税号必须报错
- **坑**：① 金额一律 Decimal，float 在 0.01 容差处会误判；② 税号权重表抄错一位就全错，先拿 10 个真实格式税号验证；③ 别让 LLM 兜字段（成本高且不可复现，S6 要的是规则基线）；④ 规则里禁止写"取最后一行的金额"这类模糊描述

---

#### S1-6 ★关键 端到端基线脚本与指标计算

- **目标**：拿到论文第一章真实数字，并建立可复跑的基线
- **产出**：`scripts/ocr_baseline.py`、`backend/eval/metrics.py`、`backend/eval/results/baseline_report.md`、`backend/tests/test_metrics.py`
- **提示词（复制）**：
```
写 scripts/ocr_baseline.py + backend/eval/metrics.py。CLI：--dir --config backend/eval/configs/baseline.yaml（preprocess 开关、unclip_ratio、规则路径）--out backend/eval/results/baseline_{ts}.json。
指标（每个一个纯函数 + 单测）：检测 Hmean（预测框与标注框 IoU≥0.5 匹配后算 P/R/Hmean）；字符准确率（1 − 编辑距离/标注长度，按行拼接）；字段级准确率（逐字段精确匹配）；整单正确率（全部必填字段都对）。同时输出 Markdown 表格到 baseline_report.md，可直接贴论文；结果文件按时间戳命名不覆盖。
先给执行计划，等我确认后再写代码。
```
- **验收**：`python scripts/ocr_baseline.py --dir samples/ --config baseline` 出真实数字；`pytest backend/tests/test_metrics.py -q` 用手算小样本断言数值；整单正确率 <0.5 先回 S1-2/S1-5 排查
- **坑**：① 指标必须独立于产框代码，同一份实现既产框又判分等于自证；② 本卡必须顺带跑一次预处理开关对比，否则 S6 没有 baseline；③ 别用平均分配套，逐字段统计才能定位问题

---

## 第 5 章 阶段 2：基础框架（S2，12 个任务）

> 把 S1 的命令行能力搬进 API：16 张表落库、三角色鉴权、文件上传、异步任务框架、前端三页。代码落在 `backend/app/{models,schemas,api/v1,services,tasks}`、`web/src`。
> **周期**：3–4 周。**验收**：三个角色登录后只看到该看的票据；上传 20 张票据全部入库并生成 ocr_jobs；`docs/API.md` 与实际接口逐条一致。

---

#### S2-1 模型与首批迁移（16 张表）

- **目标**：SCHEMA.md 变成可往返的 Alembic 迁移
- **产出**：`backend/app/models/*.py`（一表一文件）、`backend/alembic/versions/0001_init.py`
- **提示词（复制）**：
```
按 docs/SCHEMA.md 建 SQLAlchemy 2.0 模型（Mapped/mapped_column）与首个 Alembic 迁移，16 张表全覆盖：users、certificates、invoice_files、invoices、invoice_fields、ticket_templates、field_rules、ocr_jobs、ocr_lines、report_forms、report_items、signatures、envelopes、audit_logs、review_logs、key_records。
统一约定：主键 BigInteger identity、时间 timestamptz + server_default=now()、软删用 deleted_at、JSONB 用 postgresql.JSONB 且 server_default=text("'{}'::jsonb")。迁移里建关键约束：invoice_files.sha256 唯一、invoices 的 (invoice_code, invoice_no) 部分唯一索引（WHERE status <> 'void'）、ocr_lines.invoice_id 外键+索引、audit_logs(created_at) 倒序索引。
先给执行计划，等我确认后再写代码。
```
- **验收**：`alembic upgrade head` 成功，`alembic downgrade base && alembic upgrade head` 可往返；`\dt` 恰好 16 张业务表
- **坑**：别顺手加 created_by、tenant_id 之类契约外列；JSONB 默认值写在 Python 侧时，原生 SQL 插入会炸，必须 server_default

---

#### S2-2 ★关键 JWT 认证 + RBAC 三角色

- **目标**：登录、刷新、登出可用，submitter/reviewer/admin 权限边界清晰
- **产出**：`backend/app/{api/v1/auth.py,core/security.py,schemas/auth.py}`、`backend/tests/test_auth.py`
- **提示词（复制）**：
```
实现 /api/v1/auth：login、refresh、me、logout（register 仅初始化脚本调用）。密码用 Argon2，失败统一返回"用户名或密码错误"，不区分账号是否存在。JWT：access 30min + refresh 7d，payload 含 sub/role/jti，HS256 密钥从配置读；refresh 存 Redis 白名单，logout 删白名单。
依赖注入 get_current_user 与 require_roles("submitter"|"reviewer"|"admin")，权限矩阵同步写进 docs/API.md。测试用三角色 fixture，覆盖 401（无/过期 token）、403（越权）、200（正常）三类分支。
先给执行计划，等我确认后再写代码。
```
- **验收**：`pytest backend/tests/test_auth.py -q` 全绿；submitter token 调财务接口返回 403
- **坑**：① 角色判断散在各接口最致命，S5 加"复核人"必返工，统一走依赖；② 别用 OAuth2PasswordBearer 却把 token 塞 query；③ Argon2 的 memory_cost 记进 DECISIONS.md，压测时它是隐藏瓶颈

---

#### S2-3 行级数据隔离

- **目标**：submitter 看不到别人的票据，且越权返回 404 而非 403
- **产出**：`backend/app/services/repository.py`（统一过滤）、`backend/tests/test_isolation.py`
- **提示词（复制）**：
```
实现行级隔离：submitter 只能访问 owner_id == 自己的 invoices/invoice_files/reports；reviewer 可读全部待审单据并做审核决策（不可改模板与证书）；admin 负责模板/规则/证书/审计，**不得代替 reviewer 做审核决策**。过滤逻辑集中在一个 query 基类或 filter_visible(query, user) 函数里，列表和详情必须共用，禁止各接口手写 where。
越权返回 404（不暴露资源存在性），并写 pytest：A 建单，B GET /invoices/{id} → 404；B 列表里不出现 A 的数据；reviewer 能读不能改。在 docs/DECISIONS.md 记一条：为什么用应用层过滤而不是 PostgreSQL RLS（连接池 + SET LOCAL 的坑）。
先给执行计划，等我确认后再写代码。
```
- **验收**：`pytest backend/tests/test_isolation.py -q` 全绿；手工换 id 访问他人单据得 404
- **坑**：AI 常只改详情漏改列表；导出 CSV、批量接口、审计查询同样要过滤，抽一个函数是最省事的做法

---

#### S2-4 分页、筛选与排序

- **目标**：所有列表接口共用一套协议，且 5 万条数据下不慢
- **产出**：`backend/app/schemas/common.py`（PageParams/PageResult）、`backend/app/services/pagination.py`、`scripts/bench_list.py`
- **提示词（复制）**：
```
统一列表协议：入参 page/page_size（默认 20，上限 100）、sort_by/sort_order（字段白名单）、filter（status、ticket_type、date_from/date_to、q 匹配发票号/单号）；出参 {items,total,page,page_size}。count 与数据必须同一 where 条件，禁止两次拼条件。
用 SQLAlchemy 2.0 select + offset/limit；列表所需关联用 selectinload/joinedload 一次取，禁止循环查库。sort_by 只接受白名单映射，不接受用户传的字段名。写 scripts/bench_list.py 造 5 万条数据打印 p50/p95。
先给执行计划，等我确认后再写代码。
```
- **验收**：5 万条下 p95 < 300ms；`EXPLAIN ANALYZE` 显示走索引；`pytest` 里有分页边界用例（page=0、page_size=1000）
- **坑**：N+1 是这里最常见的翻车点（列表页每条记录单独查用户/模板）；排序字段白名单缺失等于开放 SQL 注入

---

#### S2-5 票据表与 CRUD（invoices / invoice_files）

- **目标**：票据的建、查、改、软删可用，状态机集中管理
- **产出**：`backend/app/{api/v1/invoices.py,services/invoice_service.py}`、`backend/tests/test_invoices.py`
- **提示词（复制）**：
```
实现 /api/v1/invoices：POST 建草稿（关联 invoice_files，owner=当前用户）、GET 列表、GET /{id} 详情（含 fields 与最新 ocr_job 状态）、PATCH /{id} 仅草稿态可改、DELETE 软删。
状态机写成枚举 + 单一 transition(invoice, target, actor) 函数：draft → ocr_pending → ocr_done → confirmed → signed → submitted → approved|rejected → archived；非法迁移返回 409 并说明当前状态。PUT /invoices/{id}/fields 批量改字段（S3 复用），值校验复用 S1-5 validators。
单测：正常建单、非法迁移 409、软删后列表不可见、软删后同发票号重建的冲突行为。
先给执行计划，等我确认后再写代码。
```
- **验收**：`pytest backend/tests/test_invoices.py -q`；建单→改字段→查详情 curl 链路通
- **坑**：状态机别用 if-else 散落各处；软删与唯一约束冲突要想清楚（同发票号重传会被唯一索引挡，需要明确策略）

---

#### S2-6 ★关键 文件上传 + MinIO + sha256 去重

- **目标**：上传校验、去重、存储、投递异步任务一条链
- **产出**：`backend/app/{api/v1/files.py,services/storage.py}`、`backend/tests/test_upload.py`
- **提示词（复制）**：
```
实现 POST /api/v1/invoices/{id}/files（multipart，流式读块算 sha256，不整文件进内存）。校验：MIME 白名单 image/jpeg|png|tiff、application/pdf，单文件 ≤10MB，用 Pillow 打开验证真图（防改扩展名），PDF 用 pypdf 校验且页数 ≤5。
先查 invoice_files.sha256 是否已存在：存在则 409 + 已有 invoice_id，不重复写 MinIO。存储 key = yyyy/mm/{sha256[:2]}/{sha256}.{ext}。上传成功写 invoice_files(sha256,size,mime,storage_key,owner_id,file_id) 后投递 ARQ ocr_task，返回 {file_id,job_id}。MinIO 成功但入库失败要补偿删除对象；文件名不得直接用作 storage_key。
先给执行计划，等我确认后再写代码。
```
- **验收**：同文件二次上传 409 且 MinIO 只有一个对象；`sha256sum 本地文件` 与库内值一致；改扩展名的假图片被拒
- **坑**：① 中文/重名/路径穿越都来自直接用原文件名；② 上传与入库非同一事务，必须有补偿；③ 去重要在写 MinIO 前判断，否则对象会重复堆积

---

#### S2-7 ARQ 异步任务框架（状态机 + 重试 + 幂等）

- **目标**：任务可投递、可重试、不重复执行
- **产出**：`backend/app/tasks/{worker.py,ocr_task.py}`、`backend/app/services/job_service.py`
- **提示词（复制）**：
```
搭 ARQ worker：tasks/worker.py 定义 WorkerSettings 注册 ocr_task 与 anchor_audit_task（S4 实现，先占位），redis_settings 从配置读。ocr_jobs 状态机 pending → running → succeeded | failed | low_confidence，每次变更写 status/attempts/error/started_at/finished_at。
重试策略 max_tries=3、退避 5s/30s/120s，只对可重试异常（网络、临时 IO、内存不足）重试，参数类错误直接 failed 并写原因。幂等：以 job_id 为键用 Redis SET NX EX 600 加锁，重复投递返回已有 job。worker 与 api 共用同一份 config，不允许抄两份 DATABASE_URL。
先给执行计划，等我确认后再写代码。
```
- **验收**：同一 job 投递两次只执行一次；kill 掉 worker 重启后 running 任务能恢复或标 failed，不残留僵尸态
- **坑**：ARQ 没有内置幂等，重复投递会导致同图识别两遍、ocr_lines 重复；退避重试要记录 attempts，否则论文里说不清失败率

---

#### S2-8 票种模板与规则管理

- **目标**：模板与抽取规则可由财务在后台维护，热更新不重启
- **产出**：`backend/app/api/v1/templates.py`、`backend/tests/test_templates.py`
- **提示词（复制）**：
```
实现 /api/v1/templates 与 field_rules CRUD。ticket_templates：name、ticket_type、anchors(JSONB)、region_layout(JSONB)、enabled、version；field_rules：template_id、field_name、anchor_synonyms、direction、regex、max_distance、required、priority。
POST /templates/{id}/test 传一张图返回该模板命中的字段与置信度（内部调 S1-5 extractor）；extractor 从 DB 读 enabled 规则并缓存 60s，改规则不重启生效。写操作仅 admin，reviewer 只读，全部写 audit_logs。首次启动用 S1 的 JSON 文件做 seed，之后以 DB 为准。
先给执行计划，等我确认后再写代码。
```
- **验收**：新建模板 + 2 条规则后 `/test` 能抽到字段；改规则 60s 内生效；reviewer 写操作 403
- **坑**：别让 JSON 文件与 DB 变成两套逻辑（只能一条数据通路，否则线上改了不生效）；正则来自后台就属于不受信输入，必须编译校验 + 长度上限

---

#### S2-9 前端登录与主框架

- **目标**：三角色登录后看到不同菜单，token 过期自动续
- **产出**：`web/src/{views/Login.vue,stores/user.ts,layout/MainLayout.vue,router/index.ts}`
- **提示词（复制）**：
```
实现 Login.vue（Element Plus 表单 + 前端校验，调 /api/v1/auth/login，token/refresh_token 存 Pinia + localStorage）与 stores/user.ts（user、role、permissions、hasRole()）。
MainLayout 侧边栏按角色动态渲染：submitter 见 我的票据/上传；reviewer 见 待核对/待审；admin 见 模板/规则/证书/审计。路由守卫按 meta.roles 拦截，无权限跳 /403。http.ts 补 401 自动 refresh 一次，失败再跳登录；并发请求的刷新必须去重（同一 Promise 复用）。
先给执行计划，等我确认后再写代码。
```
- **验收**：三角色登录后菜单不同；token 过期时只刷新一次，不出现连续弹窗
- **坑**：前端隐藏菜单不等于授权，后端必须独立校验；refresh 并发去重不做会出现"刷新风暴"把白名单刷失效

---

#### S2-10 前端上传页

- **目标**：能拖 20 张票据，逐个反馈状态，去重有明确提示
- **产出**：`web/src/views/Upload.vue`、`web/src/api/files.ts`
- **提示词（复制）**：
```
实现 Upload.vue：el-upload 支持拖拽与多选，限制 pdf/jpg/png、单文件 10MB，上传前本地校验并给缩略图；用 axios onUploadProgress 显示总进度与逐文件进度条。
逐文件状态列表：等待/上传中/成功/失败+原因。409 去重响应单独处理：显示"该票据已上传（单号 RPT/INV-xxx）"并给跳转链接，不弹红色错误。上传完成后跳 /invoices/{id}，页面轮询该单 ocr_job 状态直到结束（3s 起退避，上限 120s，超时给"识别时间较长"提示）。
先给执行计划，等我确认后再写代码。
```
- **验收**：拖 20 张上传，成功/失败计数正确；重复文件给出跳转提示；轮询在超时后停止
- **坑**：轮询不设上限会在 OCR 卡死时无限请求；组件内直连 axios 会绕过统一错误处理，一律走 http.ts

---

#### S2-11 前端票据列表与详情

- **目标**：列表可筛可翻，详情可看文件与字段（框选联动留给 S3-5）
- **产出**：`web/src/{views/InvoiceList.vue,views/InvoiceDetail.vue,types/invoice.ts}`
- **提示词（复制）**：
```
实现 InvoiceList.vue：分页 + 状态/票种/日期筛选 + 关键字搜索，列含单号、票种、金额、状态（el-tag 配色）、创建时间、操作；筛选状态同步到 URL query，刷新不丢。
InvoiceDetail.vue：左侧文件预览（图片 img 自适应，PDF 给下载链接），右侧字段表格（字段名/值/置信度/来源行），草稿态支持行内编辑并调 PUT /invoices/{id}/fields。types/invoice.ts 定义与后端 schema 一致的 TS 类型，字段名统一 snake_case；日期按 Asia/Shanghai 显示。
先给执行计划，等我确认后再写代码。
```
- **验收**：列表筛选结果与后端 total 一致；详情改字段能落库并刷新可见
- **坑**：手写 TS 类型容易与后端漂移，至少维护一份字段对照表；详情页别一次拉全量 ocr_lines（几百行），只取字段引用的行

---

#### S2-12 定时任务骨架与阶段收口

- **目标**：APScheduler 就位（S4 接审计链锚定），文档与代码同步
- **产出**：`backend/app/tasks/scheduler.py`、更新后的 `docs/{PROGRESS.md,API.md,SCHEMA.md}`
- **提示词（复制）**：
```
app/tasks/scheduler.py：APScheduler AsyncIOScheduler 随 API 启动，注册 anchor_audit_task（cron 每日 02:00，S4 实现逻辑，先打日志），任务执行时抢 Redis 分布式锁，保证多副本只跑一次。配置与常量集中到 core/config.py，禁止散落硬编码。
收口：更新 docs/PROGRESS.md（完成项/遗留项/下一步）、docs/API.md（补齐实际实现的所有接口）、docs/SCHEMA.md（有微调必须同步）。跑全量 pytest 并列出失败项与原因，不要修与我要求无关的模块。
先给执行计划，等我确认后再写代码。
```
- **验收**：`pytest -q` 全绿；起两个 API 进程时定时任务只有一个在跑；文档里的接口与实际路由逐条对得上
- **坑**：多副本下 APScheduler 会重复执行，答辩必问；文档不同步会让下一次会话的 AI 上下文失真

---

## 第 6 章 阶段 3：识别流水线上线（S3，8 个任务）

> 把 S1 的脚本链路搬进 ARQ worker，产出可被人工确认的字段数据；前端补图像与字段联动。代码落在 `backend/app/{tasks,services,ocr}`、`web/src/views`。
> **周期**：2–3 周。**验收**：上传票据 → 自动识别 → 低置信字段进确认队列 → 人工确认后转 confirmed，全程不用手工跑脚本。

---

#### S3-1 ★关键 OCR Worker 串起全链路

- **目标**：一次任务完成 下载→预处理→检测识别→票种→抽取→校验
- **产出**：`backend/app/tasks/ocr_task.py`、`backend/app/services/ocr_pipeline.py`、`backend/tests/test_ocr_task.py`
- **提示词（复制）**：
```
实现 ocr_task：取 job_id → invoice_files.storage_key → MinIO 下载到 tempfile.TemporaryDirectory（用后必删）→ PDF 用 pypdfium2 转图（限前 3 页）→ preprocess → detector/recognizer → layout.classify_ticket → extractor.extract → validators.validate。
输出统一 OcrResult{lines,fields,ticket_type,quality_flag,timings}，timings 写入 ocr_jobs.metrics(JSONB)；任何一步异常写 ocr_jobs.error 并置 failed，不吞异常。单文件硬超时 180s，超时置 failed 并记录原因。PaddleOCR 实例用 worker 级单例，不要每任务新建。
先给执行计划，等我确认后再写代码。
```
- **验收**：上传 20 张成功 ≥18，失败项 error 能定位原因；单张 P95 < 30s（CPU）；临时目录无残留
- **坑**：① 临时文件不清理会吃满磁盘；② 每次新建 PaddleOCR 要 2s 加载；③ 模型非线程安全，worker concurrency 配 1–2

---

#### S3-2 ★关键 字段级置信度聚合与低置信度确认队列

- **目标**：把"要不要人工看"变成可解释的规则，并形成 reviewer 队列
- **产出**：`backend/app/services/confidence.py`、`GET /invoices?status=needs_review`、阈值配置
- **提示词（复制）**：
```
实现 confidence.py：字段置信度 = 加权组合(锚点行 OCR score, 值行 OCR score, 规则匹配强度)，权重与阈值走配置，默认 ≥0.9 自动确认、0.6–0.9 标 needs_review、<0.6 必须人工；validators 校验失败的字段直接 <0.5。整单存在 needs_review 时 ocr_jobs 置 low_confidence、invoices 置待确认。
写回 invoice_fields(confidence, need_review, source_line_no)。提供 reviewer 队列接口与前端过滤。阈值与权重在 docs/DECISIONS.md 记录取定依据（S6 用真实分布校准后回填）。
先给执行计划，等我确认后再写代码。
```
- **验收**：跑完 40 张，确认队列里恰好是低置信字段；上下调阈值队列长度单调变化
- **坑**：别只用 OCR score（规则匹配强度同样重要）；不要用整单平均分决定单字段是否需要核对，会漏掉个别错的字段

---

#### S3-3 局部二次识别（crop 放大重识别）

- **目标**：人工点一下就能把某个字段重新识别一遍，不重跑整图
- **产出**：`backend/app/ocr/refine.py`、`POST /invoices/{id}/fields/{field}/reread`、`POST /invoices/{id}/reread-region`
- **提示词（复制）**：
```
实现 refine.py：按字段 source_line_no 取 ocr_lines.box，四周各扩 10% 并 clamp 到图像边界，INTER_CUBIC 放大 2–3 倍后调 recognizer.recognize_line。返回候选列表 [{old, new, score}] 由人工选择，禁止自动覆盖已人工确认值；整块区域重识别接受 bbox。
记录 audit_logs(action=field_reread, before, after)，并更新 invoice_fields.raw_text 与 confidence。边界：越界 bbox、空裁剪、非文本区域都要返回 422 而不是 500。
先给执行计划，等我确认后再写代码。
```
- **验收**：对一张低置信金额重识别后置信度明显上升；候选列表正确；越界 bbox 返回 422
- **坑**：裁剪越界会让 PaddleOCR 内部断言失败；重识别结果静默覆盖人工值等于丢数据

---

#### S3-4 识别结果 JSON Schema 与落库

- **目标**：识别产物有单一契约，前端与评测脚本共用
- **产出**：`backend/app/schemas/ocr.py`、`ocr_lines`/`invoice_fields` 写入逻辑、新 alembic revision
- **提示词（复制）**：
```
用 Pydantic v2 定义 OcrResult/OcrLine/ExtractedField/FieldError，导出 JSON Schema 附到 docs/API.md，供前端 TS 类型与评测脚本共用。落库：ocr_lines(invoice_id, job_id, line_no, text, score, box_json JSONB, page_no) 批量 insert，不要逐条 commit；invoice_fields(invoice_id, field_name, field_value, raw_text, confidence, need_review, source_line_no, rule_id, bbox_json, source, updated_by)。
bbox 格式在文档里写死为 [x1,y1,x2,y2]（原图坐标系），前端画框只认这一个格式。新增字段走新 alembic revision，不改已应用的迁移。
先给执行计划，等我确认后再写代码。
```
- **验收**：ocr_lines 行数与检测结果一致；导出 schema 能被前端对照；`alembic upgrade head` 在旧库上成功
- **坑**：字段名必须与 SCHEMA.md 逐字一致；坐标系（原图 vs 预处理后图）不确定会导致 S3-5 全偏，必须在契约里写清

---

#### S3-5 ★关键 票据详情页：图像与字段并排 + 框选高亮

- **目标**：点字段高亮图上的字，点字定位字段
- **产出**：`web/src/components/{ImageCanvas.vue,FieldTable.vue}`、升级 `InvoiceDetail.vue`
- **提示词（复制）**：
```
升级 InvoiceDetail：左图右表，图片按容器等比缩放，canvas 叠层绘制 bbox（不改原图的 img）。字段行 hover → 图上对应 bbox 高亮；点击图上框 → 右侧定位并闪烁该字段行；低置信字段整行黄底，校验失败红底并显示错误文案。
坐标是原图坐标系，必须按 scaleX/scaleY 换算后绘制；支持滚轮缩放与拖拽平移，缩放后框仍对齐。只渲染当前页/当前视口附近的框，避免一次画 300 个。
先给执行计划，等我确认后再写代码。
```
- **验收**：随机抽 20 个框核对位置，缩放前后都对齐；拖动和 hover 不卡顿（<16ms/帧）
- **坑**：坐标不换算直接画是全偏的头号 bug；用 DOM 绝对定位画上百个框会卡，必须 canvas

---

#### S3-6 字段修正与确认 API

- **目标**：人工改字段、整单确认，并防并发覆盖
- **产出**：`backend/app/api/v1/invoices.py`（fields/confirm）、`backend/tests/test_confirm.py`
- **提示词（复制）**：
```
实现 PATCH /invoices/{id}/fields（批量改值，仅 draft/needs_review 态）与 POST /invoices/{id}/confirm。confirm 必须服务端重跑 validators：必填齐全且全部通过才置 confirmed，否则 422 + 逐字段 {field,error}。
并发：invoices.version 乐观锁，PATCH 带 version，冲突返回 409。权限：submitter 只能改自己的草稿；reviewer 可改值但不能改金额（金额修改需 reviewer（财务审核人）），规则写进 docs/DECISIONS.md。每次修改写 audit_logs(before/after)。
先给执行计划，等我确认后再写代码。
```
- **验收**：两窗口同时改同一字段，后者收到 409；缺必填字段 confirm 返回 422 并列出字段名
- **坑**：无乐观锁时后写覆盖前写，评审必追问；confirm 不能信前端传来的"已校验"标记

---

#### S3-7 重复报销检测

- **目标**：同一张票不能报两次，弱重复要提示但不阻断
- **产出**：`backend/app/services/duplicate.py`、`GET /invoices/{id}/duplicate-check`
- **提示词（复制）**：
```
实现重复检测三维度：① 强重复 invoices 的 (invoice_code, invoice_no) 部分唯一索引（WHERE status <> 'void'）兜底，插入冲突转 409 并返回已有 invoice_id；② 影像重复 invoice_files.sha256 相同即同一张票（上传时已拦，提交报销前再查一次）；③ 弱重复 同金额+同日期+同销方税号但不同发票号 → 返回 warning 列表供 reviewer 判断，不阻断。
逻辑集中 duplicate.py，接口返回 {level: strong|weak|none, matches:[...]}。并发插入场景要有测试（两个请求同时建同号单，只成功一个）。
先给执行计划，等我确认后再写代码。
```
- **验收**：同发票号第二张单 409；同图换发票号上传被拦；弱重复给出 warning 列表
- **坑**：唯一索引是最后一道闸，应用层判断不能省（并发窗口）；弱重复做成硬阻断会误伤连号票，业务上不可接受

---

#### S3-8 批量上传与进度

- **目标**：一次传 30 张能看进度、失败能单条重试
- **产出**：`backend/app/api/v1/invoices.py`（batch 接口）、`web/src/views/Upload.vue`（批量模式）、限流
- **提示词（复制）**：
```
实现 POST /invoices/batch（多文件）逐个建 invoice + invoice_file 并投递 ARQ，返回 batch_id 与逐项状态；GET /invoices/batch/{batch_id} 返回汇总（总数/成功/失败/处理中）与逐项 job 状态。
前端 Upload.vue 批量模式：总进度 + 逐项状态 + 失败项单独重试（重试不得重复建单，用原 file_id 重投）。限流：单用户并发识别任务 ≤3（Redis 计数器），超出排队并返回明确提示文案。
先给执行计划，等我确认后再写代码。
```
- **验收**：一次传 30 文件进度到 100%；失败项重试不产生重复单据
- **坑**：批量接口里同步识别会超 Nginx 60s 默认超时，必须全异步；并发限制不做会把 CPU 打死导致全部超时

---

## 第 7 章 阶段 4：密码模块（S4，9 个任务，本课题重点）

> 本阶段只写 `backend/app/crypto/` 与 `backend/tests/test_crypto/`，**不碰业务接口**，全部能力用 pytest + 命令行脚本验证；算法参数、密钥格式、载荷结构在这里定死，S5 只调用不修改。
> **周期**：3–4 周。**验收**：`pytest backend/tests/test_crypto -q` 全绿且含 SM2/SM3/SM4 国标测试向量；签名→信封→解封→验签链路在脚本里跑通。

---

#### S4-1 ★关键 SM3 摘要与 canonical JSON 规范化

- **目标**：跨语言、跨进程对同一份业务数据算出同一个摘要
- **产出**：`backend/app/crypto/{canonical.py,sm/sm3.py}`、`backend/tests/test_crypto/test_canonical.py`
- **提示词（复制）**：
```
实现 crypto/canonical.py：canonical_json(obj) -> bytes，规则 = 键按 Unicode 码点升序、分隔符 (",",":") 无空格、ensure_ascii=False 后 UTF-8 编码、数字统一（金额先格式化成字符串，禁止 float 进 JSON）、None 保留 null、拒绝 NaN/Infinity。另提供 canonical_sha256 供对照。
实现 crypto/sm/sm3.py：sm3_digest(bytes) -> bytes(32)（gmssl.sm3.sm3_hash 返回 hex，内部统一转 bytes），sm3_hex 仅用于日志。单测：键序不同的两个 dict 摘要相同；含中文/负数/嵌套 list 用例；100 与 100.00 的差异用例；浮点被拒绝的用例。
先给执行计划，等我确认后再写代码。
```
- **验收**：`pytest backend/tests/test_crypto/test_canonical.py -q` 全绿；同一 dict 在 Python 与一段 Node 脚本下摘要一致
- **坑**：**签名前不做规范化，前后端各算一次摘要必然不一致**；float 进 JSON 会变 100.0 与 100 两种字节，必须先格式化

---

#### S4-2 ★关键 SM2 签名与验签（防双重哈希）

- **目标**：签名/验签链路只允许一种摘要策略，并把陷阱写成测试
- **产出**：`backend/app/crypto/{sm/sm2.py,signature.py}`、`backend/tests/test_crypto/test_sm2.py`
- **提示词（复制）**：
```
实现 crypto/sm/sm2.py（用 gmssl.sm2.CryptSM2，纯 Python 包）：CryptSM2(private_key=hex, public_key=hex, mode=1)（C1C3C2）。对外只暴露 sign_bytes(digest32: bytes) -> hex 与 verify_bytes(digest32, sig_hex) -> bool，函数内不再做任何哈希；签名策略二选一并在文件头注释写死（gmssl 的 sign() 不自带杂凑，sign_with_sm3() 内部做 SM3，同一链路只能选一种）。
signature.py 定义签名记录 {algo:"SM2-SM3", digest_algo:"SM3", signature, cert_id, signed_at, payload_version, canonical_payload_base64}。
**随机源必须替换**：gmssl 的 sign/sign_with_sm3 内部用 func.random_hex（基于 Python random 模块，非密码学安全），封装层必须显式传入 secrets.token_hex(32) 生成的 K；禁止依赖库默认随机源。
单测：正常通过；改明文 1 bit 失败；sign_with_sm3 + 已摘要数据混用必须失败（把陷阱固化成用例）；同一明文连续签两次，签名结果必须不同（证明随机源生效）；用 fixed K 两次签名结果相同（证明 K 参数确实被采纳）。
先给执行计划，等我确认后再写代码。
```
- **验收**：`pytest backend/tests/test_crypto/test_sm2.py -q`；用 `openssl dgst -sm3` 对同一 payload 复算摘要，结果与代码一致
- **坑**：**双重哈希陷阱**——"先做摘要再签名"与"直接 sign_with_sm3"只能选一种，验签方必须与签名方一致，否则恒失败且极难排查；签名对象永远是规范化字节，不是 dict；**随机源陷阱**——`gmssl.func.random_hex` 用的是 `random` 模块，nonce 可预测，SM2 私钥可被恢复，必须换成 `secrets`（这条要写进论文"实现"章节）

---

#### S4-3 ★关键 SM4-CBC + HMAC-SM3 的 Encrypt-then-MAC 对称层

- **目标**：对称加密可用且抗篡改
- **产出**：`backend/app/crypto/sm/sm4.py`、`backend/tests/test_crypto/test_sm4.py`
- **提示词（复制）**：
```
实现 sm4.py：encrypt(key16, plaintext) -> {iv(hex16), ciphertext(hex), mac(hex)}。PKCS7 填充、随机 IV = os.urandom(16)、CBC 加密，mac = HMAC-SM3(mac_key, iv || ciphertext)，且 mac_key 与加密 key 必须不同（用 mac_key = SM3(key||"mac") 派生，写进 docs/SECURITY.md）。
decrypt：**先验 MAC 再解密**，MAC 不符抛 IntegrityError 且不泄露任何明文信息；去填充失败同样抛 IntegrityError。单测：往返正确；改密文 1 bit 失败；改 IV 失败；截断密文明确异常；同明文两次密文不同；密钥/IV 长度不合法抛 ValueError。
先给执行计划，等我确认后再写代码。
```
- **验收**：`pytest backend/tests/test_crypto/test_sm4.py -q` 全绿；用国标 SM4 向量（ECB 单块）验证算法本身正确
- **坑**：**gmssl 的 SM4 只有 ECB/CBC，没有 GCM**，别写 mode="GCM"；必须 Encrypt-then-MAC 且 MAC 覆盖 IV||密文（MAC-then-Encrypt 与只覆盖密文都可被构造攻击）；IV 绝不允许固定或复用

---

#### S4-4 ★关键 数字信封（SM2 包裹会话密钥）

- **目标**：2MB 影像能在 1 秒内完成加密与解密
- **产出**：`backend/app/crypto/envelope.py`、`backend/tests/test_crypto/test_envelope.py`、`scripts/envelope_demo.py`
- **提示词（复制）**：
```
实现 envelope.py：seal(plaintext, recipient_cert, meta) -> Envelope{cipher_mode:"SM4-CBC", iv_b64, ciphertext_b64, mac_b64, wrapped_key_b64, key_algo:"SM2", recipient_cert_fp, payload_sha256, created_at}。流程：secrets.token_bytes(16) 生成 SM4 会话密钥（禁止 random/os.urandom 之外的来源）→ S4-3 加密载荷 → 仅用 SM2 公钥加密这 16 字节（C1C3C2，hex 是否带 04 前缀在代码里约定并注释）→ 组装并计算 payload_sha256。
unseal(envelope, private_key)：先用 SM2 私钥解出会话密钥，**但我们不依赖 SM2 自带的 C3 做完整性判断**（实测 gmssl 的 decrypt 内部算了 C3 的 u 值却未比对，密文被改也可能返回垃圾明文），完整性一律由 S4-3 的 HMAC-SM3 与 payload_sha256 保证；再走 S4-3 解密（先验 MAC），最后校验 payload_sha256。会话密钥每次信封重新生成，禁止复用；对入参长度断言，明文超过 32 字节直接 ValueError。
先给执行计划，等我确认后再写代码。
```
- **验收**：2MB 影像 seal→unseal 往返一致且 <1s（CPU）；篡改 wrapped_key_b64 / ciphertext_b64 都抛明确异常
- **坑**：**SM2/RSA 只加密 16–32 字节会话密钥，不要拿去加密图像**（RSA-2048+OAEP-SHA256 单次上限约 190 字节，2MB 要上万次运算）；C1C3C2 与 04 前缀处理不一致会导致跨实现解不开（gmssl 输出的 C1 不带 0x04 前缀）；不要相信 SM2 解密能发现密文篡改，它不会报错

---

#### S4-5 ★关键 密钥生成与私钥口令保护

- **目标**：私钥永不落库、不进日志，且能按口令解锁
- **产出**：`backend/app/crypto/keystore.py`、`backend/tests/test_crypto/test_keystore.py`、`scripts/keygen.py`
- **提示词（复制）**：
```
实现 keystore.py。国密库用 `gmssl`（PyPI: gmssl，纯 Python 实现，pip 即装、Windows 免编译、离线可跑）；`gmssl-python`（ctypes 封装，需先编译 libgmssl）只作可选升级路线，本阶段不引入。SM2 密钥对生成：gmssl **不提供密钥生成接口**，用 OpenSSL 3.x 命令行 `openssl genpkey -algorithm SM2 -out key.pem` 生成后解析成 hex；或用 gmssl 内部 `_kg` 应急，但必须注释"原型可用、生产不可用"。不要编造 API。
私钥保护：口令 + PBKDF2-HMAC-SHA256（iters ≥ 200000，盐 16B 随机）派生 32B KEK → AES-256-GCM 包裹 PKCS#8 私钥（cryptography 库）→ 输出 {kdf, iterations, salt, nonce, ciphertext, key_fp}，只落 key_records（user_id, key_fp, algo, created_at, status）。解锁失败统一提示"口令错误"。单测覆盖错误口令、被篡改密文、空口令。
先给执行计划，等我确认后再写代码。
```
- **验收**：生成→加锁→解锁→用解锁私钥签名 的脚本跑通；`grep -rn "PRIVATE KEY" backend/ docs/` 除测试向量外无命中；日志里搜不到口令
- **坑**：① 私钥不入库、不进日志、不进异常堆栈；② PBKDF2 iterations 太低等于没保护，这是论文安全分析里的一节；③ 别把 `_kg` 当生产实现，答辩会问随机源

---

#### S4-6 X.509 自建 CA 与角色证书

- **目标**：每个用户一张证书，验签时能判吊销与过期（链校验刻意简化）
- **产出**：`scripts/ca_init.sh`（OpenSSL 命令行签发）、`backend/app/crypto/certs.py`（指纹/有效期/公钥一致性校验）、`backend/tests/test_crypto/test_certs.py`
- **提示词（复制）**：
```
证书一律用 OpenSSL 3.x 命令行生成，Python 侧不实现 CA 签发：scripts/ca_init.sh 用 `openssl req -x509 -newkey sm2 -sm3` 建 CA，用户证书用 `openssl x509 -req -sm3 -CA ... -extfile` 签发，扩展显式写 KeyUsage=digitalSignature,keyEncipherment,nonRepudiation、extendedKeyUsage=clientAuth、subject 含 CN=用户名/O=学校/OU=角色。
certs.py 只做三件事：算 fingerprint_sha256、校验有效期、校验"证书公钥 == 库内登记的 SM2 公钥 hex"（防证书与私钥不匹配）；**不做完整 X.509 证书链验签**——这是刻意的简化：单 CA 拓扑，链校验在 OpenSSL 部署期完成一次，运行期只做指纹+有效期+吊销字段判断。落 certificates(cert_pem, serial, not_before, not_after, fingerprint_sha256, role, public_key_hex, revoked_at)。
在 docs/DECISIONS.md 记录该简化及其边界（不适用于多级 CA/跨机构互信）。
先给执行计划，等我确认后再写代码。
```
- **验收**：`openssl x509 -in user_cert.pem -text -noout` 能看到 KeyUsage/EKU 与 sm2 公钥；证书配错私钥时公钥一致性校验失败；吊销后返回 cert_revoked
- **坑**：别引入 `gmssl-python` 的证书 API（需先编译 libgmssl，Windows 上装不上，且偏离主线）；`-sm3` 与钥匙类型必须显式给，否则指纹与签名算法对不上；证书与私钥的配对校验不能省，配错会拖到 S5 验签时才炸

---

#### S4-7 ★关键 审计日志哈希链

- **目标**：改任何一条审计日志都能被检出
- **产出**：`backend/app/services/audit.py`、`GET /api/v1/admin/audit/verify`、`scripts/verify_chain.py`
- **提示词（复制）**：
```
实现 audit.py：字段 prev_hash、entry_hash、ts、actor_id、action、object_type、object_id、payload_sha256、trace_id。entry_hash = HMAC_SM3(prev_hash || ts || actor || action || payload_sha256)，HMAC key 从环境变量读，拼接口径用 canonical 编码固定；创世条目 prev_hash 为 32 字节 0。
写入必须串行：用 pg_advisory_xact_lock 锁住链尾，保证不分叉。verify_chain() 顺序复算返回首个断链 index 与原因，暴露到 /api/v1/admin/audit/verify 与 scripts/verify_chain.py。单测：正常链通过；改中间一条 payload 报断链；并发 50 条写入后链仍为单链。
先给执行计划，等我确认后再写代码。
```
- **验收**：篡改中间一条的 payload 后 verify 报出正确 index；50 并发写入后 verify 通过
- **坑**：不加锁并发写会让两条记录共用同一 prev_hash（链分叉且无法检出）；ts 必须存 UTC 且参与哈希的格式固定，别用本地时间字符串

---

#### S4-8 ★关键 密码模块单测与国标测试向量验证

- **目标**：用标准向量证明实现正确，而不是"自己签自己验"
- **产出**：`backend/tests/test_crypto/`（test_vectors.py、test_pipeline.py）、`docs/PROGRESS.md` 覆盖率记录
- **提示词（复制）**：
```
补齐密码模块测试：① SM3 用 GB/T 32905 附录向量（如 "abc" 的 66-bit 摘要，去官方文档核对，不要凭记忆）；② SM4 用 GB/T 32907 示例密钥/明文单块加密向量验证；③ SM2 至少覆盖 确定性摘要+随机 k+验签成功/失败/篡改，并与 OpenSSL 命令行交叉验证一次；④ 集成链路 canonical_json → SM3 → SM2 签名 → SM4 加密 → 信封 → 解封 → 验签，含 1 bit 篡改用例。
输出 `pytest --cov=app/crypto --cov-report=term-missing`，把覆盖率与向量来源写进 docs/PROGRESS.md。
先给执行计划，等我确认后再写代码。
```
- **验收**：`pytest backend/tests/test_crypto -q --cov=app/crypto` 全绿且行覆盖 ≥85%
- **坑**：只做"自己签自己验"是循环自证，两边同时错也能通过；测试向量必须注明出处（标准号+附录），论文要引用

---

#### S4-9 RSA-OAEP+PSS 与 AES-GCM 对照实现

- **目标**：为 S6 性能实验提供同接口的第二套算法实现
- **产出**：`backend/app/crypto/{rsa_layer.py,aesgcm_layer.py,provider.py}`、参数化测试
- **提示词（复制）**：
```
实现对照算法层，接口与国密层同名，便于 S6 公平对比。rsa_layer.py：RSA-2048/3072 密钥生成、OAEP-SHA256 加密会话密钥、PSS(MGF1+SHA256) 签名，暴露 encrypt_key/decrypt_key/sign/verify。aesgcm_layer.py：AES-256-GCM 的 encrypt/decrypt，签名与 S4-3 一致（iv/nonce 12B，tag 16B）。
用 AlgoProvider 协议抽象（keygen/sign/verify/encrypt_key/decrypt_key/wrap_symmetric），SM 与 RSA/AES 各实现一份，S5 通过配置选择，默认国密。测试参数化：两套实现跑同一组往返与篡改用例。
先给执行计划，等我确认后再写代码。
```
- **验收**：`pytest backend/tests/test_crypto -q -k "rsa or gcm"` 全绿；对拍脚本能输出两套实现的可比耗时
- **坑**：接口签名不一致 S6 就无法做公平对比（要做的是同接口换实现）；RSA-OAEP 的 hash 与 PSS 的 MGF1 必须显式指定 SHA256，不同库版本默认值不同

---

## 第 8 章 阶段 5：报销业务闭环（S5，7 个任务）

> 本阶段打通"签名 → 加密 → 提交 → 财务验签解密 → 审核归档"，代码落在 `backend/app/{api/v1,services}` 与 `web/src/views`；**密码层只调用 S4 接口，不允许改算法参数**。
> **周期**：2–3 周。**验收**：一单从提交到归档全流程跑通，401/403/409 三类异常都有明确响应，库里查不到明文影像与明文会话密钥。

---

#### S5-1 报销单创建与明细

- **目标**：单据与明细可建、金额自洽、只能引用已确认票据
- **产出**：`backend/app/api/v1/reports.py`、`backend/app/services/report_service.py`、`backend/tests/test_reports.py`
- **提示词（复制）**：
```
实现 /api/v1/reports：POST 建单（report_forms：report_no、applicant_id、department、reason、total_amount Decimal、status draft）；POST /reports/{id}/items 加明细（invoice_id、amount、remark），重新计算并校验合计 == total_amount（容差 Decimal("0.01")，不符 422）。
明细只能引用当前用户 status=confirmed 的票据；同一 invoice 不能被两张有效单据引用（唯一约束）。单据状态机 draft → pending_sign → signed → encrypted → submitted → approved|rejected → archived，集中在 transition()。单号 RPT-YYYYMM-序号 用 PostgreSQL 序列生成。全部金额用 NUMERIC(12,2)。
先给执行计划，等我确认后再写代码。
```
- **验收**：建单 + 3 条明细成功；金额不符 422；引用未确认票据 409；并发建单无重号
- **坑**：float 累加会出现 0.01 误差导致提交被拒，一律 Decimal；单号别用 count()+1（并发重号）

---

#### S5-2 ★关键 签名 → 加密提交链路

- **目标**：一次事务里完成规范化、签名、打包加密、状态推进与审计
- **产出**：`backend/app/services/submit_service.py`、`POST /api/v1/reports/{id}/submit`、`backend/tests/test_submit.py`
- **提示词（复制）**：
```
实现 POST /reports/{id}/submit：① 组装签名载荷 {report_no, owner, items[{invoice_code,invoice_no,amount,file_sha256}], total_amount, submitted_at} 并用 S4-1 canonical_json 序列化；② SM3 摘要 + 申请人私钥签名（私钥经 S4-5 解锁，口令只在内存停留、绝不进日志），写 signatures(digest_hex, digest_algo, signature_b64, algo, cert_id)；③ 票据影像 + 字段 JSON 打包 → S4-4 seal 到财务公钥 → 写 envelopes（ciphertext_b64, wrapped_key_b64, iv_b64, mac_b64, payload_sha256, recipient_cert_id），MinIO 只存密文对象；④ 状态 encrypted → submitted 并写 audit_logs。
整个过程要么全成要么全回滚；重复提交返回 409。返回 {report_id, envelope_id, signature_id}。
先给执行计划，等我确认后再写代码。
```
- **验收**：提交后 MinIO 对象 `file` 显示 data（非图片）；envelopes 表无明文字段；重复提交 409；签名失败时无半成品记录
- **坑**：签名与加密对象都必须是规范化字节；口令不进日志/异常；签名成功而信封失败会留下半成品，必须同一事务或补偿

---

#### S5-3 财务端待审列表

- **目标**：财务能看到所有已提交单，且列表接口不拖慢页面
- **产出**：`backend/app/api/v1/finance.py`、`web/src/views/FinanceList.vue`
- **提示词（复制）**：
```
实现 /api/v1/finance：GET /finance/pending 返回已提交单（单号、申请人、金额、提交时间、票据数、verify_status 未验/通过/失败），GET /finance/{id} 返回详情 + 明细 + 附件元数据（不含明文，明文走 S5-4）。
verify_status 在提交时算一次落库，不要在列表里实时计算；列表绝不返回 ciphertext 全文。筛选分页复用 S2-4 协议；导出 CSV 用流式响应并加 UTF-8 BOM（Excel 中文不乱码）。仅 reviewer 可见，submitter 403。
先给执行计划，等我确认后再写代码。
```
- **验收**：提交 5 单看到 5 条；CSV 用 Excel 打开中文正常；submitter 访问 403
- **坑**：列表返回几 MB 的 ciphertext 会直接卡死页面；实时算验签状态等于给自己加 N+1 和几十秒延迟

---

#### S5-4 ★关键 验签与解密

- **目标**：财务在审核前证明"这单没被改过、确实是本人签的"
- **产出**：`backend/app/services/verify_service.py`、`POST /api/v1/finance/{id}/verify`、`backend/tests/test_verify.py`
- **提示词（复制）**：
```
实现 POST /finance/{id}/verify：① 按 S4-6 的口径校验申请人证书（指纹、有效期、吊销、公钥与库内登记一致；链已在签发期由 OpenSSL 验过）；② 从库内原始数据重算 canonical payload 的 SM3 摘要，用证书公钥 verify_bytes；③ 财务私钥解锁（口令来自请求体）→ S4-4 unseal → 校验 payload_sha256 与库内一致（防替换）；④ 通过才把明文影像/字段写入带 TTL（30min）的临时预览对象，DB 不落明文；⑤ 写 audit_logs(action=finance_verify, result, reason)。
验签或解密失败返回 409 且不返回任何明文；错误信息不要细到可被利用（不区分证书无效/签名不符的具体位置）。单测覆盖：篡改 1 bit、错误私钥、吊销证书、payload_sha256 不匹配。
先给执行计划，等我确认后再写代码。
```
- **验收**：正常单验签通过可预览影像；篡改 payload 后失败且无明文泄露；错误口令返回明确错误码
- **坑**：用"提交时存下的摘要"直接比对等于没验签，必须从库内原始数据重算；临时明文对象必须有 TTL 清理任务

---

#### S5-5 审核决策与驳回

- **目标**：审批一次成功、不能重放、必须发生在验签之后
- **产出**：`POST /api/v1/finance/{id}/decision`、`review_logs` 写入、`backend/tests/test_decision.py`
- **提示词（复制）**：
```
实现 POST /finance/{id}/decision：{decision: approve|reject, comment, version}。approve 必须先有有效的 verify 凭证（S5-4 通过后写 Redis 一次性 token，30min 有效，用后即删）；reject 必填 comment，状态回 rejected 并让申请人可修改重提。
写 review_logs(reviewer_id, decision, comment, created_at) 与 audit_logs；同一单只能决策一次（唯一约束 + 乐观锁），重复决策 409。权限：仅 reviewer 可 approve/reject 并附意见；admin 不得代替审批（职责分离）。
先给执行计划，等我确认后再写代码。
```
- **验收**：approve 后状态 approved；未验签直接 approve 409；同一 verify_token 用第二次 409；重复决策 409
- **坑**：verify_token 不一次性就能"验一次批十单"；驳回后重提要生成新版本，别覆盖原签名记录（审计要求保留证据）

---

#### S5-6 归档与检索

- **目标**：审批通过的单据不可改、可检索、可导出
- **产出**：`POST /admin/reports/{id}/archive`、`GET /reports?status=archived`、导出 ZIP
- **提示词（复制）**：
```
实现归档：POST /admin/reports/{id}/archive（需 confirm=true）把 approved 单据置 archived，生成归档号 AR-YYYY-NNNN（序列）+ archive_at，写 audit_logs 并落 entry_hash。归档后单据只读，任何修改接口返回 409。
检索：GET /reports?status=archived 支持单号、申请人、日期区间、金额区间；导出 ZIP 含 签名文件、信封元数据、归档清单 CSV，ZIP 用口令加密；ZIP 内不得包含明文影像。
先给执行计划，等我确认后再写代码。
```
- **验收**：归档后详情可看、PATCH 被 409 拒；ZIP 可用口令解开且内容完整；检索条件组合结果正确
- **坑**：归档不可逆，接口必须二次确认参数；ZIP 里放明文影像等于把加密白做了

---

#### S5-7 端到端联调与异常场景

- **目标**：全链路自动化测试 + 可演示脚本，错误码与文档一致
- **产出**：`backend/tests/test_e2e_flow.py`、`scripts/e2e_demo.py`、更新 `docs/API.md`
- **提示词（复制）**：
```
写 test_e2e_flow.py 覆盖：① 正常链路 登录(submitter) → 上传 → 确认字段 → 建单 → 签名加密提交 → 登录(reviewer) → 验签解密 → approve → archive；② 异常 401 无 token、403 submitter 调财务接口、409 重复提交/重复发票号/验签失败/非法状态迁移、422 错口令；③ 并发两请求同时 approve，断言 1 成功 1 冲突；④ 安全不变量 断言库中无明文影像列、envelopes 可 unseal 且 payload_sha256 匹配。
同步更新 docs/API.md 的错误码表；scripts/e2e_demo.py 串起同一流程打印每步 trace_id，便于演示。
先给执行计划，等我确认后再写代码。
```
- **验收**：`pytest backend/tests/test_e2e_flow.py -q` 全绿；`python scripts/e2e_demo.py` 一键跑出完整流程日志
- **坑**：这类测试常被写成只走 happy path；错误码必须与 API.md 完全一致，否则前端分支会错乱

---

## 第 9 章 阶段 6：实验与评测（S6，5 个任务）

> 本阶段产出论文第 5、6 章的全部数据：识别对比、抽取对比、密码性能、安全测试、报告导出。代码落在 `backend/eval/` 与 `scripts/`，指标函数复用 `backend/eval/metrics.py`，不另写一套。
> **周期**：2–3 周。**验收**：`python scripts/run_experiments.py --all` 一条命令生成 `backend/eval/results/report.md`，每个数字都能追溯到原始结果 JSON。

---

#### S6-1 识别对比实验（CRNN vs SVTR + 预处理消融）

- **目标**：给出一张可写进论文的识别能力对比表
- **产出**：`backend/eval/experiments/recog_compare.py`、`backend/eval/results/ablation.csv`
- **提示词（复制）**：
```
写 recog_compare.py：① 主实验 固定同一份检测结果（detector 输出存盘复用），只换识别模型：PaddleOCR 2.7.3 默认 SVTR_LCNet vs CRNN 基线（用 rec_model_dir 切换或独立实现），报字符准确率、行准确率、单张耗时；② 消融 把 preprocess 的 denoise/rectify/illum/detect_seal 组合成 ≥5 组配置，各跑一遍检测 Hmean + 字段级准确率，输出 ablation.csv；③ 按票种分组给指标与失败样本清单。
指标只调用 backend/eval/metrics.py 里的函数（需要扩展就改那一个文件），不许另写一套。
先给执行计划，等我确认后再写代码。
```
- **验收**：ablation.csv ≥5 行且指标差异明显；失败样本可点开定位；报告能说明每组改了什么
- **坑**：① 换识别模型必须固定检测结果，否则增益来源说不清；② 二值化/去印章很可能拉低精度，**要做好负结果的准备并如实写**；③ 两套指标实现等于数据不可信

---

#### S6-2 字段抽取对比（纯正则 vs 正则+语义匹配）

- **目标**：证明规则+语义比纯正则在难例上更好
- **产出**：`backend/eval/experiments/extract_compare.py`、`backend/eval/results/extract_compare.csv`
- **提示词（复制）**：
```
写 extract_compare.py 三组对比：A 纯正则（只用 anchor 邻近正则，无同义词、无版面先验、无校验器修正）；B S1-5 完整规则（同义词 + 区域约束 + 校验器）；C 规则 + 语义兜底（jieba 分词 + TF-IDF cosine + 字符 n-gram Jaccard + 编辑距离选 anchor；**不引入 sentence-transformers 之类向量模型**，零下载、可解释）。
报逐字段 P/R/F1、整单正确率、平均耗时；测试集必须含中文同义表述难例（价税合计/合计金额/小写金额），单独统计难例子集指标。输出 CSV + 逐样本明细（哪个字段错、错成什么）。
先给执行计划，等我确认后再写代码。
```
- **验收**：三组同表可比；难例子集上 B/C 明显优于 A；明细能定位错例
- **坑**：A 组必须真退化，偷偷用同义词表等于造假（论文最容易被审的地方）；C 组只用 jieba 与标准库，别再引向量模型（与《完整方案》6.3 的口径一致）

---

#### S6-3 ★关键 密码性能实验

- **目标**：用数据回答"为什么用国密 + 数字信封"
- **产出**：`backend/eval/experiments/crypto_bench.py`、`backend/eval/results/crypto_bench.csv`
- **提示词（复制）**：
```
写 crypto_bench.py，接口与 S4-9 的 AlgoProvider 对齐：① 密钥生成 SM2 vs RSA-2048 vs RSA-3072，各 100 次，报均值/中位数/P95；② 签名与验签 SM2 的 sign+SM3 vs RSA-PSS-SHA256，载荷 1KB 与 10KB；③ 加密 方案甲 数字信封（SM4-CBC+HMAC-SM3 加密 2MB，SM2 只包 16B 会话密钥）；方案乙 直接非对称加密（按 190B/次分块，报调用次数与总耗时，跑 1–3 次即可）；④ 解密与验签同口径。
每组先 warmup 再计时，结果写 CSV 并记录环境（CPU 型号、Python 版本、OpenSSL 版本）。
先给执行计划，等我确认后再写代码。
```
- **验收**：CSV 中"直接 RSA 加密 2MB"耗时应到秒级以上，信封方案 <1s，构成论文核心对比；换机器数字不可复现，故环境列必填
- **坑**：① 不 warmup 会混入 import/缓存开销；② 2MB 直接非对称加密别真跑 100 次（要几小时）；③ 别只报最快的一次

---

#### S6-4 安全测试用例

- **目标**：把"系统抗攻击"变成可复跑的用例表
- **产出**：`backend/tests/test_security.py`、`backend/eval/security/cases.md`
- **提示词（复制）**：
```
写 test_security.py 覆盖六类：① 篡改 密文改 1 bit（MAC 失败）、envelope payload_sha256 改动、signature 改动；② 密钥错误 用 B 私钥解 A 的信封、吊销证书、过期证书；③ 越权 他人单据 404、submitter 调 approve 403、无 token/过期 token 401；④ 重放 重复提交、复用 verify_token、同一 envelope 二次解封；⑤ 审计链 改一条 audit_logs 的 payload 后 verify_chain 报断链并给出 index；⑥ 私钥泄露面 断言日志与 API 响应中无口令/私钥片段。
每个用例在 cases.md 里写"攻击原理 / 预期表现 / 实际结果"三列，可直接贴论文。
先给执行计划，等我确认后再写代码。
```
- **验收**：`pytest backend/tests/test_security.py -q` 全绿；cases.md 三列齐全
- **坑**：只断言"返回 4xx"不够，还要断言无副作用（DB 未写、MinIO 未新增对象）；重放用例需要明确的 nonce/幂等键设计，否则测了个寂寞

---

#### S6-5 评测报告导出

- **目标**：一条命令出报告与图表，数字全部可追溯
- **产出**：`scripts/run_experiments.py`、`backend/eval/results/report.md`、`backend/eval/results/figs/*.png`
- **提示词（复制）**：
```
写 run_experiments.py：--all 依次跑 S1-6 基线、S6-1/2/3，各自结果写 backend/eval/results/{exp}_{ts}.json，再汇总成 report.md（Markdown 表格 + 每组配置与环境说明）。报告章节固定：数据集说明 / 识别指标 / 抽取指标 / 密码性能 / 安全用例结果 / 结论与局限（局限必须如实写）。
每个数字后标注来源文件与时间戳；用 matplotlib 画 3 张图（检测 Hmean 对比、密码操作耗时对比、预处理消融）存 figs/，中文字体显式指定（SimHei 或 Noto Sans CJK）并嵌入。
先给执行计划，等我确认后再写代码。
```
- **验收**：`python scripts/run_experiments.py --all` 生成 report.md 与 3 张图，中文正常显示；report.md 每个数字能回溯到 JSON
- **坑**：服务器无中文字体会导出方块图（提前装字体或指定字体文件绝对路径）；报告数字禁止手改，只能来自 JSON

---

## 第 10 章 阶段 7：部署、调优与论文支撑（S7，5 个任务）

> 收尾阶段：让系统能在别人的电脑上跑起来，并提供答辩可用的演示与图表数据。代码落在 `deploy/`、`scripts/`，业务逻辑冻结，只允许做性能相关的局部改动。
> **周期**：2 周。**验收**：一台没装过本项目依赖的机器上 `docker compose up -d` 后能走完一次完整报销；论文图表可从结果目录一键再生。

---

#### S7-1 性能压测与 CPU 推理优化

- **目标**：知道瓶颈在哪，并在精度可接受范围内变快
- **产出**：`scripts/bench_api.py`、`backend/eval/results/bench_{ts}.json`、优化记录
- **提示词（复制）**：
```
压测：对 /api/v1/invoices 列表（读）与上传（写）各压 5 分钟，报 QPS/p95/p99/错误率，写 bench_{ts}.json。用 locust 或自写脚本，独立环境，别把识别队列打爆。
优化（每项都要报精度变化）：① det_limit_side_len 1600→1280 对比；② 按票种模板只识别关键区域（crop 后跑检测识别）；③ 同图 sha256 结果缓存（Redis TTL 1h，key 必须含预处理参数与模板版本）。记录 CPU 核数 vs worker 并发（PaddleOCR 非线程安全，concurrency 1–2）。
优化前后各跑一次完整评测（S1-6/S6-1），差异写进 docs/DECISIONS.md。
先给执行计划，等我确认后再写代码。
```
- **验收**：有优化前后对比表；单张识别耗时下降 ≥30% 且字段准确率下降 ≤2%
- **坑**：① 只报"变快了"不报精度，答辩必追问；② 缓存 key 不含配置版本，改了参数还命中旧结果；③ 压测与识别抢 CPU，数据会失真

---

#### S7-2 Docker 一键部署 + 备份

- **目标**：一条命令起全栈，且数据能备份与恢复
- **产出**：`deploy/docker-compose.yml`、`backend/Dockerfile`、`deploy/nginx.conf`、`scripts/{backup.sh,restore.sh}`
- **提示词（复制）**：
```
写生产部署：docker-compose 含 nginx + api + worker + scheduler + postgres + redis + minio，全部 healthcheck + depends_on condition，显式资源限制。Dockerfile 多阶段构建，paddleocr/paddlepaddle 层单独缓存，模型文件（~/.paddleocr）打进镜像或挂载卷，避免容器内反复下载；api 与 worker 用同一 Dockerfile target。
nginx 反代 /api 与前端静态文件：client_max_body_size 20m、proxy_read_timeout 300s、gzip on。scripts/backup.sh：pg_dump + mc mirror + openssl 加密打包，保留 7 天；restore.sh 对应恢复，文档写清 RTO。
先给执行计划，等我确认后再写代码。
```
- **验收**：`docker compose up -d` 后浏览器走完一次报销；备份包能恢复到干净环境
- **坑**：① 不预置模型容器会反复下载；② 只备份 DB 不备 MinIO 等于没备；③ api 与 worker 镜像不同源会导致依赖版本漂移，签名结果都对不上

---

#### S7-3 冷启动检查与临时明文零残留扫描

- **目标**：验证"换台电脑也能跑"，产出是 README 而不是代码
- **产出**：`scripts/cold_start.sh`、README 的"常见错误 → 解决"表
- **提示词（复制）**：
```
做冷启动验收：在干净目录 git clone，撇开本地任何缓存与配置，从零按 README 执行并记录每步耗时与报错。重点检查隐式依赖：.env.example 是否完整、是否误依赖 samples/ 里未提交的文件、模型与字体是否需要仓库外提供、alembic 能否从 base 升到 head。
写 scripts/cold_start.sh 半自动：复制 .env.example → 起服务 → alembic upgrade head → smoke.py → 打开首页，任一步失败即停并打印修复建议。把发现的问题修掉并补进 README 的"常见错误→解决"表。
先给执行计划，等我确认后再写代码。

另加一项"临时明文零残留扫描"：用 20 张票据跑完整链路后，扫描 /tmp、上传目录、日志文件与 MinIO 明文桶，检索发票号码、税号、姓名的原文特征，命中即为缺陷并给出清理方案（对应《完整方案》7.4 的验收要求）。```
- **验收**：另一台机器（或删掉本地 .env 与卷）按 README 在 30 分钟内跑完全流程
- **坑**：80% 的"在我电脑上没问题"来自未提交文件、硬编码绝对路径、未跟踪的样本；这条最容易走过场

---

#### S7-4 论文图表数据导出

- **目标**：论文里的表和图能一键再生，杜绝手抄数字
- **产出**：`scripts/export_paper_assets.py`、`docs/{DATASET.md,PERF.md}`、`backend/eval/results/figs/`（PDF+PNG）
- **提示词（复制）**：
```
写 export_paper_assets.py：从 backend/eval/results/ 汇总生成 ① 识别指标表 ② 抽取对比表 ③ 密码性能表（CSV+Markdown），④ 图（检测 Hmean 对比、密码操作耗时对比、预处理消融柱状图）输出 PDF+PNG 300dpi，中文字体显式指定并嵌入。
同时生成 docs/DATASET.md（数据集规模、票种分布、标注规范摘要、合成参数）与 docs/PERF.md（环境、配置、结论）。
脚本只读 JSON，不许出现写死的数字；每个表标注"数据来源：{file}#{key}"。
先给执行计划，等我确认后再写代码。
```
- **验收**：删掉 figs 目录后一条命令全部再生；图表数字与 eval JSON 完全一致
- **坑**：手抄数字是最常见的低级错误，改一次实验要全文重改；PDF 未嵌入字体在 Word 里会变宋体或方块

---

#### S7-5 演示环境与录屏脚本

- **目标**：6 分钟讲完，断网也能演，含两个安全亮点
- **产出**：`deploy/demo/seed.py`、`scripts/demo_flow.md`、预置演示数据
- **提示词（复制）**：
```
准备答辩演示：① deploy/demo/seed.py 幂等灌入 8 张合成票据 + submitter/reviewer 各一个 + 已签发证书 + 一张已归档单据（重复执行不产生重复数据）；② scripts/demo_flow.md 写 6 幕录屏脚本（登录 → 上传识别 → 字段确认 → 签名加密提交 → 财务验签解密 → 审核归档），每幕写清点击路径、预期画面、旁白要点、时长，总计 ≤6 分钟。
必须演示两个亮点：低置信字段进确认队列、篡改 payload 后验签失败；另备一份被篡改的数据供现场切换。全流程离线可跑，提前验证。
先给执行计划，等我确认后再写代码。
```
- **验收**：断网状态下按 demo_flow.md 走完 6 幕无卡顿；重复 seed 数据不翻倍
- **坑**：现场最怕外网依赖与首次模型下载（提前 warmup 并保留容器）；演示数据禁止用真实票据

---

## 第 11 章 提示词模板库（直接复制用）

### T0 会话开场（每次必用）
```
请先读 AGENTS.md、docs/PROGRESS.md，以及与本次任务相关的 API.md / SCHEMA.md 片段。
然后用 5 句话总结：项目做什么、当前进度、本次任务是什么、有哪些硬约束、你打算怎么做。
不要写代码。
```

### T1 开发新功能（最常用）
```
【任务】<任务编号与名称>
【目标】<一句话说明用户可见的效果>
【产出文件】<明确列出允许新增/修改的文件>
【约束】
- 只改上述文件，不要动其他已有代码
- 不引入新依赖（如需引入，先说明理由并等我同意）
- 遵循 AGENTS.md 的命名与分层规范
- 必须处理边界情况：空输入、空结果、超时、异常
【验收方式】<pytest 命令 / curl 命令 / 界面点击路径 / 肉眼核对哪张图>
【流程】先给执行计划（改哪些文件、每个文件做什么、怎么验证），等我确认后再写代码。
```

### T2 修 Bug（信息给足）
```
【现象】<实际发生了什么>
【期望】<应该是什么样>
【复现步骤】1... 2... 3...
【完整报错】<贴完整堆栈，不要截断>
【相关代码】<贴关键片段或让 AI 自己 read>
【要求】
1) 先给出根因分析（为什么会这样），不要直接改代码
2) 给出最小改动方案，说明为什么这是最小改动
3) 修完后写一个能复现该 bug 的测试用例
```

### T3 写测试
```
请为 <文件/函数> 编写 pytest 单元测试，要求：
1) 覆盖正常路径 + 至少 3 个边界情况（空值、超长、异常、坏填充）
2) 断言必须有意义（不要 assert result is not None）
3) 涉及数据库的用事务回滚或测试库隔离
4) 密码相关必须包含"篡改后必须失败"的用例
5) 需要真实模型推理的测试单独标记 @pytest.mark.slow，默认不跑
```

### T4 代码审查
```
请以严格代码审查者身份审查 <文件>，逐条列出问题，按严重程度分级：
[严重] 逻辑错误 / 安全问题 / 数据一致性风险 / 密码误用
[中等] 性能问题 / N+1 查询 / 缺少错误处理 / 命名不清
[轻微] 风格 / 注释 / 可读性
对每个问题给出：位置、为什么是问题、建议改法。
不要直接改代码，先出报告。
```

### T5 生成数据库迁移
```
表结构以 docs/SCHEMA.md 为唯一依据。请：
1) 写 SQLAlchemy 模型（models/<module>.py）
2) 生成 Alembic 迁移（autogenerate 后人工检查）
3) 迁移里必须包含索引与约束（唯一、外键、CHECK、部分唯一索引）
4) 特别确认：invoices 的 (invoice_code, invoice_no) 部分唯一索引、report_forms 的 idempotency_key 唯一
5) 验证 alembic upgrade head 与 downgrade -1 都能成功
6) 不要修改已存在的迁移文件
```

### T6 卡住时的求助（防止 AI 瞎改）
```
现在卡在 <具体问题>。请不要立刻改代码，先：
1) 列出 3 种可能的技术方案，各自的实现复杂度、优缺点、风险
2) 指出你推荐哪一种，为什么
3) 说明每种方案会改到哪些文件
等我选定方案再动手。
```

### T7 前端页面
```
请实现页面 <页面名>，遵循：
1) Vue3 <script setup> + TypeScript + Element Plus，不用 Options API
2) 接口调用统一走 src/api/ 封装（不要组件里直接 axios）
3) 必须处理：加载中、空数据、请求失败三种状态（el-skeleton / el-empty / el-alert）
4) 表单要有校验；提交按钮要防重复点击
5) 识别核对页要求：字段 hover 时图上对应框高亮；低置信字段标红；冲突字段给出两个候选让用户选，不要自动替换
先给组件拆分方案与数据流说明，确认后再写代码。
```

### T8 OCR 调参 / 规则迭代（要能进论文的对照设计）
```
现状：<贴当前配置与测试集上的指标>
问题：<具体现象，如"合计区的金额框经常与税额行粘连""税号识别错位多">
请给出 3 组可调参数或规则改动的方案，说明每组改动的预期影响，
并告诉我在同一个测试集上如何做对照（要能写进论文的表格：配置 / 指标 / 结论）。
不要直接改代码，先给方案。
```

### T9 上下文交接（换工具或隔天继续）
```
请生成一份《上下文交接摘要》，包含：
1) 项目当前状态（已完成到哪个任务）
2) 最近 3 次任务的改动与遗留问题
3) 下一步任务是什么、前置条件是否满足
4) 开发者需要知道的关键约束与坑（尤其是密码与 OCR 的踩坑记录）
控制在 400 字以内，供下一次会话加载。
```

### T10 密码实现专项审查（本课题必用，每个密码任务都过一遍）
```
请审查 <文件> 的密码实现，逐条回答：
1) 随机数来源是什么？是否用了 Python 的 random 模块（禁止）？
2) SM2 的 mode 参数是否显式设置为 1（C1C3C2）？
3) 签名用的是 sign_with_sm3 还是 sign？是否与验签侧完全一致？有没有双重哈希？
4) 对称加密是否 Encrypt-then-MAC？MAC 覆盖了 IV、密文、算法标识、收方证书指纹吗？
5) 解密前是否先验 MAC？失败时是否立即中止且不进入解密？
6) 私钥是否可能出现在日志、异常、接口响应、数据库里？
7) 是否有"篡改后必须失败"的测试用例？是否跑过国家标准测试向量？
对每一条给出结论与证据（代码位置），不要只说"没问题"。
```

---

## 第 12 章 让 AI 干活时最容易翻车的 12 个地方

| # | 翻车点 | 表现 | 预防 |
|---|---|---|---|
| 1 | **幻觉密码 API** | 说"用 `gmssl` 生成密钥对"（实际没有）、说"SM4-GCM"（实际只支持 ECB/CBC）、编造 `CryptSM2.encrypt_bytes()` | 每个密码调用都必须有测试向量跑通；不确定时让它读源码 |
| 2 | **OCR 库版本漂移** | 网上教程是 3.x 的 `predict()`，项目装的是 2.7.3 的 `ocr()`，返回结构对不上 | 锁 `paddleocr==2.7.3`；写 `tests/test_ocr_contract.py` 断言返回结构 |
| 3 | **随机数用错** | 加密/签名从 `random` 取随机数 | 全项目只允许 `secrets`；代码审查第 1 问 |
| 4 | **双重哈希** | 先 `SM3` 再 `sign_with_sm3`，或签名用 A 方案验签用 B 方案 | 统一封装成 `sign_canonical` / `verify_canonical`，业务层不许直接调库 |
| 5 | **SM2 模式与格式不一致** | `mode=0`（C1C2C3）与 `mode=1`（C1C3C2）混用、C1 少 `04` 前缀 | 常量集中定义；跨实现时先做一次互通测试 |
| 6 | **先解密后验 MAC** | "解出来再看 MAC 对不对" | 代码审查第 5 问；单测断言"MAC 错时 `crypt_cbc` 未被调用" |
| 7 | **规范化不一致** | 签名时 `json.dumps` 带空格，验签时不带 → 永远验不过 | 落库 `canonical_json` 原文；两侧共用同一个函数；单测固定一组输入产出固定字节 |
| 8 | **置信度阈值没校准** | 所有字段都标红（阈太高）或一个都不标红（阈太低） | 用测试集画"阈值-准确率"曲线，阈值写进配置而非硬编码 |
| 9 | **预处理过度** | 强行二值化、盲目 inpaint 去印章，检测 Hmean 反而下降 | 预处理全部做成开关；用消融实验决定留哪一步 |
| 10 | **明文残留** | `tmp/` 里有识别用的解密图，日志里有发票号码原文 | 内存处理 + `finally` 清理 + 日志脱敏 + S7 扫描任务 |
| 11 | **大文件用 base64 塞 JSON** | 2MB 图片 base64 后 2.7MB，接口超时、内存翻倍 | 走 multipart + 对象存储；只在密码服务内部处理 bytes |
| 12 | **AI 越改越乱** | 每次让它改 A，都顺手重构了 B | 每次明确"只改这几个文件"，并在 commit 前 `git diff --stat` 看一眼 |

**通用兜底**：每个里程碑结束做一次"冷启动检查"——`git clone` 到新目录，按 README 跑一遍。跑不起来说明有隐式依赖（本地路径、未提交文件、手工改过的配置、没锁的版本）。

---

## 第 13 章 什么必须你自己做（AI 不能替）

| 事项 | 为什么必须你来 |
|---|---|
| **技术选型与架构决策** | 答辩会问"为什么用 DBNet 不用 EAST""为什么不用 GCM"，AI 给不出你的理由 |
| **数据库表结构与字段语义** | 这是系统的骨架，AI 编字段会导致后期全面返工 |
| **票种模板与字段校验规则** | 规则对不对，只能对着真实票据一张张看；这是系统质量的直接决定因素 |
| **数据集的真实性** | 哪些是合成、哪些是脱敏真实票据、标注怎么做——只有你知道，且必须在论文里说清 |
| **密码协议的设计取舍** | 签名域包含什么、MAC 覆盖什么、证书简化到什么程度、失败怎么处理——这些设计决定才值钱 |
| **实验数字** | AI 可以写脚本，但表格里的数字必须你自己跑出来填；让 AI 填 = 学术不端 |
| **抽查结果的分析** | AI 会替你美化数据 |
| **论文正文撰写** | 观点、逻辑、自我批评只能是你的 |
| **合规与授权** | 票据样本的采集授权、脱敏方式，必须你本人走流程并留档 |
| **每周进度判断与砍需求决策** | 什么时候启动 Plan B，只有你能决定 |
| **划定"现成 vs 自写"的边界** | AI 天然倾向"我帮你写一个"；哪个该复用、哪个必须自研（论文工作量），判断权在你 |

> **一个实用平衡**：让 AI 干"有明确对错、能自动验证"的活（CRUD、接口封装、测试、样式、迁移）；自己干"没有标准答案、需要判断"的活（预处理策略、抽取规则、密码协议、标注、实验、写作）。这条线划对了，AI 是 5 倍效率；划错了，是在给未来的自己挖坑。

---

## 第 14 章 全部任务总表（可当进度看板）

> 复制到 `docs/PROGRESS.md`，做完一项把 `[ ]` 改成 `[x]`。

```
## 阶段 0：项目启动（M0 前半）
[ ] S0-1 创建仓库骨架与 .gitignore
[ ] S0-2 写项目宪法 AGENTS.md
[ ] S0-3 写契约文档 SCHEMA.md + API.md ★关键
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

**打星号（★）的 21 个任务是项目的关键路径**：其中阶段 1 占 5 个（识别链路）、阶段 4 占 7 个（密码链路），正好对应论文的两条主线；阶段 0 的契约文档、阶段 5 的签名提交与验签解密也属关键。这些任务**不要赶时间、必须亲自验收、必须自己读懂代码**；其余任务可以并行、可以借助 AI 快速推进。

---

## 附录 A AGENTS.md 完整模板（复制即用）

```markdown
# AGENTS.md — 项目开发约束（AI 助手必读）

## 1. 项目一句话
基于 OCR 的高校报销票据识别与加密系统：票据影像 → 结构化字段（含置信度）→ 人工确认 →
国密数字签名 + 数字信封 → 财务端验签解密 → 审核留痕。

## 2. 技术栈（不要擅自更换）
- 后端：Python 3.11 + FastAPI + SQLAlchemy 2.0 + Alembic + Pydantic v2
- 前端：Vue 3 + TypeScript + Vite + Pinia + Element Plus
- 数据：PostgreSQL 16、Redis 7、MinIO
- 异步：ARQ（队列）+ APScheduler（定时：审计锚定、临时文件清理）
- OCR：**paddleocr==2.7.3**（PP-OCRv4：DBNet 检测 + SVTR_LCNet 识别，CPU 推理）、
      opencv-python、numpy、pillow；**禁止升级到 3.x**（返回结构不兼容）
- 密码：**gmssl==3.2.2**（纯 Python 版，SM2/SM3/SM4）、cryptography（AES-GCM /
      RSA-OAEP/PSS / X.509 / PBKDF2）、OpenSSL 3.x 命令行（生成 SM2 密钥与证书）
- 测试：pytest；部署：Docker Compose + Nginx

## 2.1 依赖与复用原则
- 新需求先找成熟库；只有"8 块业务逻辑"（预处理兜底、版面分块、字段锚定、校验规则、
  置信度聚合、规范化签名域、信封格式、审计链+评测）自己实现
- 擅自引入新依赖前必须说明理由并等确认
- **所有依赖必须锁版本号**，改版本前先跑全量测试

## 3. 目录约定
backend/app/{api/v1,models,schemas,services,ocr,crypto,core,tasks,utils}
backend/app/ocr/{preprocess,detector,recognizer,layout,extractor,validators,templates}
backend/app/crypto/{canonical,sm/,signature,envelope,keystore,certs}
web/src/{api,components,views,stores,router,utils,types}
docs/{PROGRESS.md,API.md,SCHEMA.md,DECISIONS.md,EVAL.md}
backend/eval/{datasets,configs,results}   samples/   deploy/   scripts/

## 4. 编码规范
- 分层：路由只做参数校验与调用；业务在 services；OCR 只在 ocr/；密码只在 crypto/
- 命名：数据库与 API 用 snake_case；前端变量用 camelCase；常量全大写
- 类型：所有函数签名必须有类型标注；Pydantic 模型用于所有出入参
- 金额：一律 `Decimal`，禁止 float
- 图像与 bytes：只在内存流转，不落临时文件
- 错误：不吞异常，不用 `except: pass`；异常带错误码与 trace_id，不带敏感数据
- 注释：只解释"为什么"，不复述"做了什么"；密码相关必须写明协议意图

## 5. 密码实现约束（违反即打回）
- 随机数只允许 `secrets`，禁止 `random`（gmssl 的 `func.random_hex` 也不许直接用）
- SM2 必须显式 `mode=1`（C1C3C2）；`CryptSM2` 默认 `mode=0`，不许用默认值
- 签名验签统一用 `sign_with_sm3` / `verify_with_sm3`；禁止与 `sign(data, K)` 混用
- 对称层：SM4-CBC + PKCS7 + 随机 IV + HMAC-SM3，**Encrypt-then-MAC**；
  解密前必须先验 MAC，禁止"先解密再校验"
- 不依赖 SM2 自带的 C3 校验（该库实现未做比对），完整性一律由 HMAC 保证
- 私钥一律口令加密存文件，禁止入库、禁止进日志、禁止出现在接口响应
- 业务代码禁止直接调用密码库，只能调 `crypto/` 的封装函数
- 每个密码封装必须有：标准测试向量用例 + 往返用例 + 篡改失败用例

## 6. OCR 实现约束
- 坐标与置信度必须一路保留到字段层，禁止在中途丢弃
- 预处理每一步都要能单独开关（为消融实验）
- 预处理失败必须降级而非抛异常（轮廓法 → 旋转校正 → 原图直送 + quality_flag=low）
- 不做无收益的强预处理（二值化、inpaint 去印章）除非消融实验证明有正收益
- 检测框的 unclip_ratio 不得超过 1.8（票据文本密集，会粘连）
- OCR 结果结构变化必须立刻被 `tests/test_ocr_contract.py` 发现

## 7. 禁止事项
- 禁止修改已提交的 Alembic 迁移文件
- 禁止引入未经确认的新依赖；禁止改动锁定的版本号
- 禁止在任务范围外"顺手重构"
- 禁止删除或跳过测试来让测试通过
- 禁止生成假的数据、假的指标、假的抽查结果
- 禁止把真实票据图片、姓名、身份证号、银行账号写进代码、日志、示例数据、提交历史
- 禁止在日志中打印明文金额、税号、身份证号（一律走脱敏函数）

## 8. 验收习惯（每个任务必须做到）
1) 改完代码给出可执行的验证命令（pytest / curl / 界面路径 / 看哪张输出图）
2) 新增功能必须配测试；外部依赖（模型推理）可标记 slow，但密码测试必须默认跑
3) 更新 docs/PROGRESS.md；接口变化同步 API.md；表变化同步 SCHEMA.md
4) 新增环境变量同步 .env.example；新增依赖同步 requirements.txt
5) 生成符合 Conventional Commits 的提交信息

## 9. 与其他文档的关系
- docs/SCHEMA.md：数据结构的唯一依据，冲突时以它为准
- docs/API.md：接口契约的唯一依据
- docs/PROGRESS.md：当前进度与遗留问题的唯一依据
- docs/EVAL.md：评测口径与实验数字，**只读，AI 不得修改其中数字**
若发现文档与代码不一致，先报告，不要自行决定改哪边。
```

---

## 附录 B PROGRESS.md / API.md / DECISIONS.md 模板

### B1 docs/PROGRESS.md
```markdown
# 项目进度

## 当前状态
- 阶段：阶段 4（密码模块）
- 最近完成：S4-3 SM4-CBC + HMAC-SM3 信封对称层
- 下一步：S4-4 数字信封封装与拆封
- 已知阻塞：纯 Python SM4 处理 2MB 耗时 ~420ms，需评估是否影响端到端 P95

## 任务清单
（粘贴第 14 章的总表，完成后改 [x]）

## 遗留问题（TECH DEBT）
| # | 问题 | 影响 | 计划处理时间 |
|---|---|---|---|
| 1 | 印章遮挡样本的字段准确率明显偏低（0.61） | 整单正确率被拉低 | 视 S6 结果决定是否加去印章分支 |
| 2 | 识别 Worker 单并发，批量上传排队久 | 体验差 | S7-1 |
| 3 | 证书只做指纹与有效期校验，无链验证 | 安全性分析需说明 | 论文"不足"章节 |

## 变更日志
### 2027-02-20
- S4-3 完成：新增 crypto/envelope.py、crypto/sm/sm4.py、tests/test_envelope.py
- 接口无变化；新增配置项 ENVELOPE_CIPHER_ALGO（默认 SM4-CBC-HMAC-SM3）
```

### B2 docs/API.md（每条接口一段）
```markdown
### POST /api/v1/invoices/upload
- 权限：submitter（登录用户）
- 类型：multipart/form-data；文件 ≤ 20MB；返回后走异步识别
- 入参：
  | 字段 | 类型 | 必填 | 说明 |
  |---|---|---|---|
  | file | file | 是 | JPG/PNG/PDF |
  | template_hint | string | 否 | 票种提示，不传则自动判定 |
- 出参：{ invoice_id, job_id, dedup: {hit: bool, existing_id?: int} }
- 行为：先算 sha256 查重 → 内存中信封加密 → 写 MinIO → 落库 → 投递 ARQ 任务
- 错误码：
  - 40001 文件类型不支持
  - 40002 文件超过 20MB
  - 40901 影像已存在（返回已有 invoice_id）
  - 50010 OCR 引擎异常
- 备注：明文不落磁盘；`storage_key` 不对外暴露
```

### B3 docs/DECISIONS.md（论文选型章节的草稿）
```markdown
## D-001 后端框架：FastAPI 而非开题写的 Flask
- 日期：2026-12-05
- 背景：开题技术选型表写的是 Flask + MySQL；开发前重新评估
- 备选：① Flask ② FastAPI ③ Django
- 决策：FastAPI
- 理由：OCR 是秒级长任务，原生 async 更顺；Pydantic 同时承担接口校验与抽取结果校验；
        自动 OpenAPI 便于前端联调
- 代价：相对开题有调整，需在论文中说明并在答辩时解释
- 验证：M0 两个最小闭环脚本均以 FastAPI 结构组织，运行正常

## D-002 国密库：gmssl（纯 Python）而非 gmssl-python（官方 ctypes 版）
- 日期：2027-02-10
- 背景：需要用 SM2/SM3/SM4 完成签名与信封
- 备选：① gmssl（纯 Python，pip 即装） ② gmssl-python（需编译 libgmssl）
        ③ 调 OpenSSL 命令行 ④ 商用密码机
- 决策：①，密钥与证书生成辅以 ③
- 理由：Windows 开发环境零编译；① 覆盖 SM2/SM3/SM4 主要运算；
        ③ 只用于密钥对与证书这类一次性操作，SM2/SM3/SM4 的日常运算仍走 ①
- 已知缺陷（已在代码中补偿）：
  * `func.random_hex` 用 random 模块 → 全项目改用 secrets
  * `decrypt` 未比对 C3 → 完整性由 HMAC-SM3 保证
  * `mode` 默认 C1C2C3 → 全项目强制 mode=1
  * SM4 无 GCM → 采用 CBC + HMAC 的 Encrypt-then-MAC
- 验证：S4-8 用国家标准测试向量（SM3 "abc"、SM4 单块）全部通过

## D-003 证书：只做指纹绑定与有效期校验，不做证书链验证
- 日期：2027-02-14
- 背景：开题要求"公钥以数字证书形式发布"，但纯 Python 国密库不提供证书解析与链验证
- 备选：① 完整 PKI ② 指纹绑定 + 有效期 + 吊销 ③ 直接用裸公钥
- 决策：②（①列为展望，③安全性不足）
- 理由：签名与信封的安全性主要取决于私钥保密与公钥-身份绑定；
        链路验证需要 SM2 证书签名验证能力，纯 Python 库不具备，自研风险与工期均不允许
- 代价：无法抵御"CA 被攻破后伪造证书"的场景；论文"安全性分析"中明确列为局限
- 验证：S4-6 实现证书签发、指纹入库、有效期与吊销校验，并给出篡改用例
```

---

## 附录 C 每周节奏与打卡表

### 每周固定动作

| 时间 | 动作 | 产出 |
|---|---|---|
| 周一 | 从任务总表挑本周 3–5 张卡，排优先级 | 本周计划（写在 PROGRESS.md 顶部） |
| 每天 | 按第 1 章 7 步流程做 1–2 张卡 | 1–2 个 commit |
| 周三 | 检查"关键路径"任务（★）是否有风险 | 风险记录（落后就准备降级） |
| 周五 | 冷启动检查 + 打 tag + 写周报 | tag + 3 行周报 |
| 每两周 | 给导师发一页汇报（完成/计划/卡点/需协助）+ 2–3 张截图 | 汇报文档 |
| 每里程碑 | 全量回归（跑全部测试 + 手工走主流程）+ 更新 EVAL.md 数字 | 里程碑 tag + 自测结果 |

### 打卡表模板

| 周次 | 计划任务 | 实际完成 | 卡点 | 是否需启动降级 |
|---|---|---|---|---|
| W1 | S0-1~S0-3 | | | 否 |
| W2 | S0-4~S0-7 | | | 否 |
| W3 | S1-1~S1-3 | | | 否 |
| W4 | S1-4~S1-6 | | | 否 |
| … | | | | |

**降级判定规则**：连续两周实际完成 < 计划的 70%，就对照《完整方案》8.4 砍需求，**立刻砍，不要拖**。拖着不砍的结果通常是最后一周什么都做不完。

---

## 最后三条

1. **先验证，再动工**。阶段 1 的 6 个任务用脚本跑通"预处理 → 检测识别 → 字段抽取"，是这个项目最值钱的两周。跳过它直接做界面和密码模块，大概率在第 12 周才发现"字段根本抽不准"，返工成本翻三倍。
2. **AI 写代码，你写判断**。让 AI 干能自动验证的活（CRUD、封装、测试、样式）；预处理策略、抽取规则、密码协议、实验数字、论文自己来。分界线划清，效率是 5 倍；划不清，是给未来的自己挖坑。
3. **密码模块的标准不是"跑通"，是"讲得清 + 测得狠"**。签名域、MAC 覆盖范围、密钥分层、失败路径——每一处都要能对着代码讲，并且有"篡改必失败"的用例兜底。这是本课题和"调个 API 的 demo"之间唯一的分界线。

---

**落地建议**：把本文档与 `OCR票据识别与加密系统-毕业设计完整方案.md` 一起放进仓库的 `docs/` 目录，并在 `AGENTS.md` 里加一行引用：

```
项目方案见 docs/OCR票据识别与加密系统-毕业设计完整方案.md
实施步骤与任务卡见 docs/AI协作开发实施计划.md（任务编号 S0-1 ~ S7-5 均出自该文件）
```