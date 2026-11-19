# PROGRESS.md — 项目进度

> 唯一进度依据。每个任务完成后更新：勾选任务、记录实际产出文件、登记遗留问题。
> 任务编号出自 `docs/OCR票据识别与加密系统-AI协作开发实施计划.md`（S0-1 ~ S7-5，共 59 张卡）。

## 当前状态

- 阶段：**阶段 0（项目启动）**，M0 前段
- 最近完成：**S1-2 图像预处理与透视校正**（EXIF/定位/透视+降级链/照度/印章标记；65 个测试、几何校正成功率 85%、P95 245ms、OCR 前后无下降）
- 下一步：**S1-3 PaddleOCR 检测识别最小闭环**（第一次装 paddlepaddle，需先解决几百 MB 依赖下载）
- 已知阻塞：无
- 环境提醒：后端必须用 **Python 3.11**（`py -3.11 -m venv .venv`）；本机默认 `python` 是 3.13，`paddlepaddle` 无对应 wheel

## 任务清单

```
## 阶段 0：项目启动（M0 前半）
[x] S0-1 创建仓库骨架与 .gitignore
[x] S0-2 写项目宪法 AGENTS.md
[x] S0-3 写契约文档 SCHEMA.md + API.md ★关键
[x] S0-4 起基础服务（PostgreSQL 16 + Redis 7 + MinIO）
[x] S0-5 后端最小可运行：config + 日志/trace_id + 统一异常
[x] S0-6 前端最小可运行 + axios 封装 + 路由守卫
[x] S0-7 冒烟脚本与 README 完整化（tag m0-env-ready）

## 阶段 1：OCR 技术验证（M0 后半）
[x] S1-1 票据样本集、标注规范与合成票据脚本 ★关键
[x] S1-2 图像预处理与透视校正 ★关键
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
| S1-2 | `backend/app/ocr/preprocess.py`（`PreprocessOptions` 8 开关 + `to_original` 3×3）、`scripts/{preprocess_demo,preprocess_eval}.py`、`backend/tests/{test_preprocess_exif,test_preprocess_geometry}.py` | ① 65 个测试全过（含 EXIF 旋转、已知梯形还原、坐标回算、开关组合冒烟）；② 60 张数据集：几何校正成功率 **85%**（其中 15 张判为"正视角满幅"免重采样）、quality ok 52 / low 8、耗时 **P50 84ms / P95 245ms**（预算 300ms）；③ `preprocess_eval`：可比样本 OCR 命中率 88% → 88%（**无样本变差**）；④ demo 输出 60 张前后对照图 | ✅ 通过 |
| S1-1 | `scripts/{sample_fonts,sample_data,invoice_layout,invoice_templates,perturb,make_samples,check_annotations,verify_samples}.py`、`scripts/ocr_probe.ps1`、`backend/eval/datasets/{annotations_synth_v1.jsonl,README.md}`、`samples/MANIFEST.md`、`docs/{spec,plan}/s1-1-samples.md`、3 个测试文件 | ① `make_samples.py` 生成 **60 张**（dev 36 / test 24，**5 票种 × 6 版式**，含 2026 主流的数电票 `vat_digital`）；② `check_annotations.py --strict` 全过；③ `verify_samples.py` **5 项全过**（渲染一致性 4.1×、OCR 字段命中率 干净 0.89 / 困难 0.86、标注自洽、成像覆盖、划分完整）；④ `pytest` 50 passed（新增 26 条）；⑤ 系统 OCR 能读出合成票的字段值 | ✅ 通过 |
| S0-7 | `scripts/smoke.py`（配置/端口/三件套/后端/迁移/前端六类检查，`--strict` 与 `-v`）、完整 `README.md`（环境要求、8 步快速开始、端口表、常用命令、**9 条 Windows 坑**、故障排查表、文档索引、已知限制）、`docs/DECISIONS.md` D-022（基础设施 ADR） | ① `smoke.py` 在后端未起时给出"后端没起 + 启动命令"而不是 traceback；② 故意写错 PG 口令 → 报"认证失败 + 两处 .env 要对齐"（与"服务没起"区分开）；③ 停掉整个 docker 栈 → 每项都提示 `docker compose up -d`；④ 全起后 OK 7 / WARN 1（迁移，S2-1 前正常）/ FAIL 0，退出码 0；⑤ `--strict` 下 WARN 使退出码为 1 | ✅ 通过 |
| S0-6 | `web/`（Vue 3.5 + Vite 8 + TS 6 + Element Plus + Pinia + Vue Router：`src/{main.ts,App.vue,api/http.ts,api/system.ts,router/index.ts,stores/user.ts,views/*}`、`package.json` 精确版本、`.env.{development,production,example}`）、`scripts/ui_smoke.mjs` | ① `npm run typecheck` 无错、`npm run build` 成功；② 干净浏览器访问 `/` → 守卫重定向到 `/login?redirect=/` 且登录页渲染；③ `/debug` 显示后端 health（4 个 tag 全 ok）+ trace_id；④ 点「触发一个 404」→ 弹出后端 message「资源不存在」并把 trace_id 回填；⑤ 停掉 MinIO → 页面显示 degraded + 一个 fail，恢复后回到全 ok；⑥ 控制台无 CORS 报错 | ✅ 通过 |
| S0-5 | `backend/app/{main.py,core/{config,logging,exceptions,health}.py,schemas/common.py,api/v1/health.py}`、`backend/{requirements.txt,requirements.lock.txt,pyproject.toml,.env.example}`、`backend/tests/{conftest,test_config,test_health,test_errors_trace}.py` | ① `pytest -q` 24 passed；② 起 uvicorn 后 `/api/v1/health` 真实连三件套返回 200（db/redis/minio 全 ok）；③ 不存在路由返回 `40400` 且响应头 `X-Trace-Id` 与 body 一致；④ 同一 trace_id 能在日志里串起错误日志 + 访问日志；⑤ 缺配置启动即抛 `ConfigError` 并列出缺哪几项 | ✅ 通过 |
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

S1-1 新增的遗留（不阻塞 S1-2）：

| # | 问题 | 影响 | 计划处理 |
|---|---|---|---|
| 1 | 真实样本 R-001 的票种与字段未人工补录 | 只能做定性对照，不能进评测 | 你方便时告知票种，我补进 MANIFEST |
| 2 | 合成集只有 40 张，`test` 仅 16 张 | 指标置信区间宽，只能做配置间相对比较 | S6 前若需要可 `--n 80` 扩量（脚本已支持） |
| 3 | 版式与真实票仍有差距（字体、栏线、防伪底纹未模拟） | 真实票据上指标会低于合成集 | 论文如实写"合成集为主 + 真实样本定性对照" |
| 4 | 数电票版式靠"真实 PDF 的坐标量测"复刻，未逐像素对照 | 合成数电票可能比真票"整齐"，检测框分布偏乐观 | S1-2 预处理阶段把 R-002 的 PDF 转图后与合成图并排比一次（人工） |
| 5 | 抽取规则需要按票种分支（数电票没有代码/校验码） | 影响 S1-5/S2-8 的规则设计 | S1-5 实现时按 `ticket_type` 分派 |
| 6 | 远景小票（纸面 ~300px 高）预处理后 OCR 仍读不出 | 这类样本的准确率会被拖低 | 属信息量不足，交给人机协同（低置信 → 人工确认）；S6 分组报告 |
| 7 | `preprocess` 最慢一张 447ms（2800px 原图） | 极端大图会超预算 | S3 的 Worker 里按任务超时兜底；必要时把 warp_max_side 降到 2000 |

## 变更日志

### 2026-11-18

- **S1-2 完成**：图像预处理与透视校正。
  · `preprocess()` 全链路：EXIF 方向 → 缩放（检测用小图）→ 票据四角点（Otsu 亮区 + 边缘双路候选）→ 透视 /
    `minAreaRect` / 原图直送三档降级 → 小票放大 → 残差去斜 → 照度校正 → 印章标记（**不 inpaint**）；
  · 返回 `to_original`（3×3 单应矩阵，校正图 → 原图）：S1-3 在校正图检测，前端要在原图显示高亮框；
  · 三处实现修正（D-026，都是被"OCR 前后对比"逼出来的）：
    ① **原图重采样**（先缩放再裁切会把字缩小，某张票 OCR 命中率 56% → 0%）；
    ② **恒等快捷**（四角点≈图像四角时不做透视，避免插值把加粗数字抹花，100% → 67% → 恢复）；
    ③ **照度限幅**（目标取 p90、增益 0.75–1.6 且只施加 70%，否则深色背景压暗整图 / 浅灰小票被提亮成白纸）；
  · 依赖三角（D-025）：`opencv-python==4.6.0.66` 与 numpy 2 不兼容 → numpy 锁 **1.26.4**，同时满足 paddleocr 2.7.3 的 pin。
- **S1-1 完成（含一次范围修正）**：合成票据集与标注规范。
  · **修正**：真实样本 R-002 是**数电票**（20 位号码、无发票代码、无校验码、210×140mm），
    而首版模板做的是旧版增值税电子普通发票。按 D-023 新增 `vat_digital` 版式为主流票种，
    旧版降级为"历史形态"，合成集由 40 张扩为 **60 张**（6 版式 × 10 张，dev 36 / test 24）；
    数电票模板的版面几何取自真实 PDF 的**坐标量测**（只取相对位置，未读取字段值）。
  · 生成器 6 个模块：字体探测、字段值生成（金额大写 / 统一社会信用代码校验位 / 假身份证号）、
    版式原语（**绘制即记录坐标**）、5 种票面、扰动（背景桌面 + 几何 + 光度 + 印章/折痕）、CLI；
  · **几何扰动同步变换标注框**（单测钉死 90° 旋转的期望位置），这是"图歪框正"这类隐形 bug 的唯一防线；
  · 场景补了"贴到桌面"：纸面占画面 23%–100% 三档，才能真正考验 S1-2 的票据定位；
  · 真实样本 R-001 建档：像素统计定性为"手机拍摄、强红偏、纸面仅占 30% 的困难工况"，票种待人工确认，
    图片不入库、票面文字不进日志；
  · 验收做成 5 项程序化检查（不依赖人眼看图），并**在过程中修正了两处判据**：
    ① 渲染一致性用"局部自适应墨迹"而非全局暗像素（深色背景下随机框也"暗"，首版判据失效）；
    ② 可读性用"字段值命中率"而非整页字符相似度（键名表头不该进分母），并修掉归一化未处理全角标点的问题
       （OCR 把 `¥666.36` 读成 `¥ 666 ． 36`，导致金额字段被判为未读出）。
- **S0-7 完成**：环境冒烟与 README，**S0 阶段收口**（tag `m0-env-ready`）。
  · `scripts/smoke.py`：配置 → 端口 → PostgreSQL(版本/库/迁移) → Redis(PING/AOF) → MinIO(bucket) → 后端 /health → 前端(可跳过)；
    **区分"没起"与"配错"**（连接被拒 vs 认证失败给出完全不同的下一步）；任何失败都不吐 traceback；`--strict` 让 WARN 也失败；
    `-v` 打印实际地址且**对 DSN 口令打码**；
  · 迁移项刻意做成 WARN：S2-1 之前没有 `alembic_version` 表是正常的，S2-1 之后同一脚本自动升级为 FAIL（靠"有没有迁移文件"判断）；
  · README 补全：环境要求（Python **3.11** 的硬约束写在表里）、8 步快速开始（每步带验证命令与期望输出）、端口表、
    常用命令、目录结构、**Windows 特有问题 9 条**（paddle wheel、镜像拉取、命名卷、CRLF、端口占用、MSYS 缺 tee/nohup、
    Edge dump-dom、中文路径、控制台编码）、故障排查表（按冒烟输出对照）、已知限制；
  · DECISIONS D-022：基础设施组合 ADR（PG16 + Redis7 + MinIO + ARQ 的逐项理由与代价）。
- **S0-6 完成**：前端最小可运行。
  · 技术栈按 AGENTS 落地：Vue 3.5 / Vite 8 / TS 6 / Element Plus 2.14 / Pinia / Vue Router，`package.json` 全部精确版本；
  · `api/http.ts` 是唯一出口：注入 `Authorization`、统一解包 `{code,message,data,trace_id}`、401 清会话跳登录、
    403 提示、其余弹后端 message；错误归一成 `ApiError`，并**携带后端 payload**（否则 503 降级信息会被丢）；
  · 路由守卫：未登录跳 `/login?redirect=...`；`meta.roles` 先声明不生效（S2-3 实装，避免假的安全感）；
  · `/debug` 联调自检页：展示 health 三依赖、版本、trace_id（可复制）、原始响应壳，并能一键触发 404 验证错误路径；
  · 新增 `scripts/ui_smoke.mjs`：无头 Edge + CDP 读**渲染后**正文与控制台（Windows 版 Edge 的 `--dump-dom` 不输出到 stdout），
    支持 `--expect` / `--click` / `--eval` / `--wait`，供 S7 冷启动检查复用。
  · 过程中修掉两个问题：① 前端把 503 当纯错误丢掉 payload → 降级态显示成"未请求"，改为 `ApiError.payload` 透传；
    ② 冒烟脚本无 `--expect` 时 0.4s 就断言，早于异步请求返回 → 改为先固定宽限再轮询。
- **S0-5 完成**：后端最小可运行。
  · `create_app()` 工厂 + `Settings`（pydantic-settings，缺项启动即抛 `ConfigError` 并列出缺哪几项；prod 拒绝示例 JWT_SECRET）；
  · JSON 结构化日志（stdlib only，不引 loguru/structlog），`trace_id` 用 contextvar + 中间件注入，
    **同一条链路可 grep**：错误日志、访问日志、业务日志都带同一个 trace_id；
  · 统一错误壳 `{code,message,data,trace_id}`：404→40400、422→42200、未捕获→50000 且**堆栈只进日志**；
  · `/health` 与 `/api/v1/health` 同一实现，检查 DB/Redis/MinIO，任一挂了返回 **503 degraded**（探针可直接判活）；
  · 24 个单测覆盖：配置校验 7、健康检查 5、错误壳与 trace 12。
  · 修掉一个真 bug：未捕获异常时 `Exception` 处理器在最外层 ServerErrorMiddleware 里执行，
    此时 contextvar 已 reset → 日志和响应里的 trace_id 变成 `-`；改用 `request.state.trace_id` 传递。
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