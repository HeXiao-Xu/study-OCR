# 上手指南：在队友电脑上从零跑起 study-OCR（交给 DSH 执行）

> **给人看的 3 行**：把本文件全文贴给 DeepSeek Harness（下面简称 DSH），然后按它的提示点
> 「是」/重启电脑/登录 GitHub 即可。预计 40–70 分钟，时间基本花在下载上。
> 本文件同时放在仓库 `docs/ONBOARDING.md`；克隆后可以直接让 DSH 读它。

---

## 0. 给 DSH 的任务说明

你现在在一台干净的 **Windows 10/11** 电脑上。目标是把
`https://github.com/HeXiao-Xu/study-OCR.git` 这个项目**完整跑起来**，并配好
**两人通过 GitHub 共同开发**的工作方式。

项目形态（先建立正确预期，别把它当成一个纯前端或纯脚本项目）：

| 层 | 技术 | 本机端口 |
|---|---|---|
| 后端 API | Python 3.11 + FastAPI + SQLAlchemy + Alembic | **8000** |
| 异步 worker | ARQ（OCR 识别任务） | 无端口 |
| 前端 | Vue 3 + Vite + TypeScript | **5173** |
| PostgreSQL 16 | Docker 容器 | **5433** |
| Redis 7 | Docker 容器 | **6380** |
| MinIO（对象存储） | Docker 容器 | **9002 / 控制台 9003** |
| OpenSSL 3.5.9 | Docker 容器（**一次性**，生成国密密钥/证书用） | 无 |

**最终验收（第 13 节，必须全绿）**：`smoke.py` FAIL=0、后端 `/health` 三项 ok、
`pytest` 全绿、前端 `typecheck + build` 通过、`e2e_demo.py` 退出码 0、
浏览器能用 `test_sub` 登录并上传一张票完成识别。

---

## 1. 硬规则（违反会导致返工，请逐条遵守）

1. **一步一步来，每步先自证再进下一步**。每个阶段结束时把「实测命令 + 实测输出」贴出来；
   禁止写"应该没问题"、"理论上可以"。
2. **禁止编造结果**。命令失败就贴原始报错；做不到就停下来报告，不要假装完成。
3. **不要改锁定版本**：`backend/requirements.txt`、`web/package.json` 里的版本号一个字都不要动
   （paddle 2.7.3 / paddlepaddle 2.6.2 / numpy 1.26.4 / opencv 4.6.0.66 / SQLAlchemy 2.0.58 是整组锁死的，
   动一个就连锁崩）。
4. **禁止修改 `backend/alembic/versions/` 下已有的迁移文件**。
5. **禁止把下面这些提交进 git**：`.env`、`secrets/`（CA、私钥、证书）、`samples/` 里的图片、
   `node_modules/`、`.venv/`、`dist/`、`.idea/`、真实票据图片。它们都已在 `.gitignore` 里，
   你**不要**用 `git add -f` 绕过。
6. **不要 `git push --force` 到 `main`**；不要改写已推送的历史。
7. **不要自己猜口令、不要自己造账号密码**：本文档第 9、10 节与附录 A 指定了统一口令。
8. **需要人来做的节点**（做完就停下来等人）：
   - Windows 的 UAC 弹窗（安装软件时出现，点"是"）；
   - 安装 Docker Desktop / WSL2 后的**重启**；
   - 首次启动 Docker Desktop 时的协议接受与（可选）登录；
   - GitHub 的登录 / Personal Access Token / 把队友加成 collaborator；
   - 浏览器里人工点一遍登录与上传（第 13 节最后一项）。
9. **同一步卡住超过 15 分钟，停下来报告**，不要连续换 5 种偏方（尤其是 Docker 和 paddle）。
10. **工具使用约定**：`.sh` 脚本一律用 **Git Bash** 跑（`bash scripts/xxx.sh`），
    不要用 PowerShell 直接跑 `.sh`（会落到 WSL 的 bash，那里没有 Windows 的 docker 客户端）；
    其余命令用 PowerShell 跑。下载/安装/pip/pytest 这类长任务用后台任务跑，不要阻塞。
11. **不要用 WSL 里的 Python/Node**，一律用 Windows 原生安装的版本。
12. 汇报用中文，先给命令输出，再给结论。

**建议先用 `todo_write` 建任务清单**（12 个 todo，对应第 3–14 节），完成一个勾一个。

---

## 2. 克隆之后先读这 4 份文档（项目自己的规则）

| 文件 | 作用 |
|---|---|
| `AGENTS.md` | 项目"宪法"：技术栈、目录、编码规范、OCR/密码高压线、验收习惯 |
| `docs/HANDOFF.md` | 会话交接：当前进度、环境、常用命令、已知取舍 |
| `README.md` | 环境要求、快速开始、Windows 坑 |
| `笔记.txt`（仓库外，作者提供）/ `docs/UI_TEST_FLOW.md` | 人工演示流程与账号 |

读完再动手。文档与代码冲突时**先报告，不要自己决定改哪边**。

---

## 3. 阶段 A：盘点这台机器（5 分钟）

先记录现状，**不要先装东西**。在 PowerShell 里逐条执行（找不到命令就是没装，属正常）：

```powershell
git --version
py -3.11 -V
python -V
node -v
npm -v
docker --version
docker info --format "{{.ServerVersion}}"
winget --version
```

**期望与判定**：

| 项 | 合格条件 | 不合格怎么办 |
|---|---|---|
| Git | 输出 `git version 2.x` | 第 4.1 节安装 |
| Python 3.11 | `py -3.11 -V` 输出 `Python 3.11.x` | 第 4.2 节安装。**`python -V` 是 3.13/3.14 没关系，本项目只用 3.11** |
| Node.js | `node -v` ≥ v20（v22/v24 都行） | 第 4.3 节安装 |
| npm | `npm -v` 输出 10/11.x | 跟随 Node 安装 |
| Docker | `docker info` 能打印 ServerVersion | 第 4.4 节安装 + 重启 |
| winget | 有版本号 | 没有就用官方安装包手动装（第 4 节各小节附链接） |

把结果记成一张表，最后写进汇报（第 17 节）。

---

## 4. 阶段 B：安装缺失的工具（15–40 分钟，取决于网速）

> 原则：**缺什么装什么**，已经合格的不要重装。
> winget 命令后面统一加 `--accept-source-agreements --accept-package-agreements`，否则会卡在交互确认。
> 安装过程中出现 UAC 弹窗要**停下来让人点"是"**。
> 安装完成后**必须新开一个终端**（或刷新 PATH）再验证，否则会误判"没装上"。

### 4.1 Git for Windows（**必需，不是可选**）

为什么必需：除了 `git clone`，项目的国密密钥/证书脚本（`scripts/ca_init.sh`、`scripts/openssl.sh`）
是 bash 脚本，依赖 Git for Windows 自带的 Git Bash。

```powershell
winget install -e --id Git.Git --accept-source-agreements --accept-package-agreements
```

验证（新开终端）：`git --version`，并且能找到 Git Bash：
`Test-Path "C:\Program Files\Git\bin\bash.exe"`。

### 4.2 Python 3.11（**必须是 3.11**）

为什么：`paddlepaddle` 没有 3.13/3.14 的 wheel，装了也白装。

```powershell
winget install -e --id Python.Python.3.11 --accept-source-agreements --accept-package-agreements
```

验证：`py -3.11 -V` → `Python 3.11.x`。
如果 `py` 命令不存在（没装启动器），用完整路径：
`& "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe" -V`。
**记下这个 3.11 的真实路径**，后面建虚拟环境要用它（本文档统一写成 `py -3.11`，
若你的机器没有 `py`，就把 `py -3.11` 换成上面那个完整路径）。

手动兜底：<https://www.python.org/downloads/release/python-3119/>（勾选 "Add python.exe to PATH"）。

### 4.3 Node.js LTS（≥ 20）

```powershell
winget install -e --id OpenJS.NodeJS.LTS --accept-source-agreements --accept-package-agreements
```

验证（新开终端）：`node -v` ≥ v20，`npm -v` 有输出。
手动兜底：<https://nodejs.org/en/download>（选 LTS 的 Windows Installer）。

### 4.4 Docker Desktop（必需，本项目用它跑基础服务容器）

**先确认这台机器能不能装**：需要 Windows 10/11 64 位、开启虚拟化（BIOS 里 VT-x/AMD-V）、
有管理员权限。如果这是公司/学校统一管控的电脑且禁用虚拟化，**停下来报告**，不要硬试。

```powershell
# 1) 确认 WSL2 状态
wsl --status

# 2) 若提示未启用，执行（完成后必须重启电脑）
wsl --install --no-distribution

# 3) 安装 Docker Desktop
winget install -e --id Docker.DockerDesktop --accept-source-agreements --accept-package-agreements
```

重启之后（**这一步必须人工重启，等用户确认回来再继续**）：

1. 从开始菜单启动 **Docker Desktop**，首次会要求接受协议（点 Accept）、可能要求登录（**可以 Skip**）。
2. 等右下角鲸鱼图标变绿（首次启动可能要 1–5 分钟）。
3. 在 PowerShell 验证：

```powershell
docker info --format "{{.ServerVersion}}"
docker compose version
```

两条都有输出才算成功。**不要**在这个阶段去配 Docker 镜像加速（第 7 节按需处理）。

手动兜底：<https://www.docker.com/products/docker-desktop/>

---

## 5. 阶段 C：克隆代码（5 分钟）

**路径要求（很重要）**：纯 ASCII、无空格、无中文、不放 OneDrive/桌面同步目录。
推荐 `D:\dev\study-OCR` 或 `C:\dev\study-OCR`。

```powershell
New-Item -ItemType Directory -Force -Path D:\dev | Out-Null
Set-Location D:\dev
git clone https://github.com/HeXiao-Xu/study-OCR.git
Set-Location D:\dev\study-OCR
git log -1 --oneline
git status --short
```

期望：`git log` 打印最新提交；`git status --short` **没有任何输出**（工作区干净）。

可能遇到：

- **弹出 GitHub 登录框** → 让人登录自己的 GitHub 账号（或贴一个 Personal Access Token，
  classic、勾 `repo` 权限）。**不要把 token 写进任何文件或提交。**
- **`remote: Repository not found` / 403** → 说明这个账号没有仓库权限。
  停下来让人处理：仓库所有者要把你加成 **Collaborator**（GitHub → 仓库 → Settings →
  Collaborators → Add people），或者你走第 15 节的 **Fork 流程**。
- **`git config user.name` 未设置** → 让人确认用哪个名字/邮箱（会写进提交记录），然后：
  ```powershell
  git config user.name  "你的名字"
  git config user.email "你的邮箱"
  ```

**验证目录干净**：`git config --get core.autocrlf` 不用改；仓库有 `.gitattributes`（统一 LF），
不要手动改行尾。

---

## 6. 阶段 D：创建两份 `.env`（10 分钟，**逐项核对，这一步错后面全错**）

两个 `.env` 都不入库，**每台机器各自生成**。

```powershell
Set-Location D:\dev\study-OCR
Copy-Item deploy\.env.example  deploy\.env
Copy-Item backend\.env.example backend\.env

# 生成随机口令（跑 5 次，取 5 个不同的值：前 3 个是服务口令，后 2 个是密钥）
py -3.11 -c "import secrets; print(secrets.token_hex(16))"   # → PG 口令
py -3.11 -c "import secrets; print(secrets.token_hex(16))"   # → Redis 口令
py -3.11 -c "import secrets; print(secrets.token_hex(16))"   # → MinIO 口令
py -3.11 -c "import secrets; print(secrets.token_hex(32))"   # → JWT_SECRET（≥32 字符）
py -3.11 -c "import secrets; print(secrets.token_hex(32))"   # → AUDIT_HMAC_KEY（≥32 字符）
```

### 6.1 `deploy/.env`（容器侧）要改的项

| 键 | 改成 | 备注 |
|---|---|---|
| `POSTGRES_PASSWORD` | 上面第 1 个随机值 | **记下来**，下一份 `.env` 要用 |
| `REDIS_PASSWORD` | 第 2 个 | **记下来** |
| `MINIO_ROOT_USER` | `studyocr`（保持） | 至少 3 字符 |
| `MINIO_ROOT_PASSWORD` | 第 3 个 | 至少 8 字符，**记下来** |
| 端口 `5433/6380/9002/9003` | **不要改** | 已刻意错开默认端口，避开冲突 |

### 6.2 `backend/.env`（应用侧）要改的项

| 键 | 值 | 说明 |
|---|---|---|
| `DATABASE_URL` | `postgresql://studyocr:<PG口令>@127.0.0.1:5433/studyocr` | 口令必须与 `deploy/.env` 的 `POSTGRES_PASSWORD` **完全一致** |
| `REDIS_URL` | `redis://:<Redis口令>@127.0.0.1:6380/0` | 与 `deploy/.env` 的 `REDIS_PASSWORD` 一致 |
| `MINIO_ACCESS_KEY` | `studyocr` | 等于 `MINIO_ROOT_USER` |
| `MINIO_SECRET_KEY` | `<MinIO口令>` | 等于 `MINIO_ROOT_PASSWORD` |
| `JWT_SECRET` | 第 4 个随机值 | ≥32 字符；不能留示例值 |
| `AUDIT_HMAC_KEY` | 第 5 个随机值 | ≥32 字符；**一旦库里写了审计行就不要换**（换了历史链全验不过） |
| `FINANCE_KEY_PASSPHRASE` | `demo-pass-1234` | **必须和第 10 节财务私钥口令一致** |
| `ENVIRONMENT` | `dev` | 保持 |
| `STORAGE_ALLOW_PLAINTEXT` | `true` | 本地开发要看原图，必须是 true |
| `ALGO_PROVIDER` | `sm` | 保持（国密） |
| 其余 | 保持示例值 | 不要动 OCR/置信度/分页等参数 |

### 6.3 验证两份配置一致（必须通过）

```powershell
Set-Location D:\dev\study-OCR
py -3.11 -c "import re,pathlib;d=dict(re.findall(r'^([A-Z_][A-Z0-9_]*)=(.*)$',pathlib.Path('deploy/.env').read_text(encoding='utf-8'),re.M));b=dict(re.findall(r'^([A-Z_][A-Z0-9_]*)=(.*)$',pathlib.Path('backend/.env').read_text(encoding='utf-8'),re.M));print('PG一致 ',d['POSTGRES_PASSWORD'] in b['DATABASE_URL']);print('Redis一致',d['REDIS_PASSWORD'] in b['REDIS_URL']);print('MinIO一致',b['MINIO_ACCESS_KEY']==d['MINIO_ROOT_USER'] and b['MINIO_SECRET_KEY']==d['MINIO_ROOT_PASSWORD']);print('JWT长度',len(b['JWT_SECRET']),'AUDIT长度',len(b['AUDIT_HMAC_KEY']));print('财务口令',b['FINANCE_KEY_PASSPHRASE'])"
```

期望：前三项都是 `True`，`JWT长度 64 AUDIT长度 64`，财务口令 `demo-pass-1234`。
（长度不一定是 64，只要 ≥32 即可。）

**确认这两个文件不会被提交**：

```powershell
git status --short
```

期望：**没有任何输出**（`.env` 已被 `.gitignore` 忽略）。若看到 `deploy/.env` 或 `backend/.env`
出现在列表里，立刻停止并报告——说明 `.gitignore` 被改坏了，不要继续。

---

## 7. 阶段 E：起 Docker 三件套（10–20 分钟，主要是拉镜像）

### 7.1 先拉镜像

```powershell
docker pull postgres:16
docker pull redis:7-alpine
docker pull minio/minio:latest
docker pull minio/mc:latest
docker pull alpine/openssl:3.5.9
```

- 前 3 个是业务服务，`minio/mc` 是一次性初始化容器，`alpine/openssl` 用来生成国密密钥/证书
  （**宿主机不需要装 OpenSSL**，全部在容器里跑）。
- 总下载量约 1.2–1.5 GB。网慢就放后台跑。

**如果 `docker pull` 卡住然后报 `registry-1.docker.io` 超时**（国内很常见）：用仓库自带的镜像源脚本，
在 **Git Bash** 里执行：

```bash
cd /d/dev/study-OCR
bash scripts/pull_image.sh postgres:16
bash scripts/pull_image.sh redis:7-alpine
bash scripts/pull_image.sh minio/minio:latest
bash scripts/pull_image.sh minio/mc:latest
bash scripts/pull_image.sh alpine/openssl:3.5.9
```

（脚本会依次试 daocloud / xuanyuan / 1ms / 华为源，拉完后自动 `docker tag` 成标准名。）

### 7.2 起服务

```powershell
Set-Location D:\dev\study-OCR
docker compose -f deploy/docker-compose.dev.yml up -d
docker compose -f deploy/docker-compose.dev.yml ps
docker compose -f deploy/docker-compose.dev.yml logs minio-init
```

期望：

- `ps` 里 `study-ocr-postgres` / `study-ocr-redis` / `study-ocr-minio` 三个都是 **healthy**；
- `minio-init` 日志出现 `[minio-init] bucket ready: study-ocr-invoices`（这个容器跑完即退出，正常）。

### 7.3 服务级自检（不依赖虚拟环境）

```powershell
Set-Location D:\dev\study-OCR
py -3.11 scripts\check_services.py
```

期望：全部 OK，退出码 0。它会真的连库、跑 `select 1`、验证 pgcrypto 扩展、Redis AUTH + AOF、
MinIO 真实写读删一个对象。

**常见失败**：

| 现象 | 处理 |
|---|---|
| `port is already allocated` | 本机有别的服务占了端口。`netstat -ano \| findstr :5433` 找进程；或改 `deploy/.env` 里的端口（同时改 `backend/.env` 的 URL） |
| `initdb: cannot be run as root` | 不要改 compose 的卷挂载成 `./data:...`，必须用命名卷（默认就是）；若之前手动改过，`docker compose down -v` 后重来 |
| MinIO bucket 不存在 | `docker compose -f deploy/docker-compose.dev.yml up -d minio-init` 再 `logs minio-init` |

> 注意：`down -v` 会**清空开发库**，只在明确知道要重置时用。

---

## 8. 阶段 F：Python 虚拟环境与依赖（10–25 分钟）

虚拟环境放在**仓库根目录** `.venv`（不要建在 `backend/` 下，脚本路径都按根目录写的）。

```powershell
Set-Location D:\dev\study-OCR
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -V
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r backend\requirements.txt
```

- 用清华源加速；如果某个包在镜像上缺版本，去掉 `-i ...` 用官方源重试一次即可。
- `paddlepaddle` + `paddleocr` 体积大（几百 MB），慢是正常的，放后台跑。
- **如果报 `No matching distribution found for paddlepaddle`**：说明你在用非 3.11 的解释器建 venv，
  删掉 `.venv`，用 `py -3.11` 重来。

### 验证依赖装对了（版本必须**完全一致**）

```powershell
.\.venv\Scripts\python.exe -c "import numpy,cv2,paddle,paddleocr;print('numpy',numpy.__version__);print('opencv',cv2.__version__);print('paddle',paddle.__version__);print('paddleocr',paddleocr.__version__)"
```

期望：`numpy 1.26.4` / `opencv 4.6.0` / `paddle 2.6.2` / `paddleocr 2.7.3`。
**任何一个版本不对就停下来报告**，不要自己升级。

> 首次真正跑 OCR 时会自动下载 PP-OCRv4 模型（约 18 MB）到 `%USERPROFILE%\.paddleocr`，
> 需要联网。离线机器把模型目录拷过来，并在 `backend/.env` 设 `OCR_MODEL_ROOT=<目录>`。

---

## 9. 阶段 G：建表 + 种子模板 + 账号（5 分钟）

所有命令都在 `backend/` 目录里跑（`.env` 是从当前工作目录读的）。

```powershell
Set-Location D:\dev\study-OCR\backend

# 1) 建表（Alembic 迁移，head 应为 0009）
..\.venv\Scripts\python.exe -m alembic upgrade head
..\.venv\Scripts\python.exe -m alembic check

# 2) 票种模板与字段规则（5 个票种 / 37 条规则）
..\.venv\Scripts\python.exe ..\scripts\seed_templates.py

# 3) 三个演示账号（口令见附录 A；reviewer/admin 只能脚本建，不能自助注册）
..\.venv\Scripts\python.exe ..\scripts\create_user.py --username test_sub   --role submitter --real-name 测试提交人 --password "Test_sub!2026"
..\.venv\Scripts\python.exe ..\scripts\create_user.py --username test_rev   --role reviewer  --real-name 测试审核人 --password "Test_rev!2026"
..\.venv\Scripts\python.exe ..\scripts\create_user.py --username test_admin --role admin     --real-name 测试管理员 --password "Test_admin!2026"
```

期望：

- `alembic upgrade head` 打印从 0001 到 0009 的迁移记录；
- `alembic check` 输出 `No new upgrade operations detected`（模型与库零差异）；
- `seed_templates.py` 打印 seeded 5 个模板；
- 三个 `create_user.py` 都以退出码 0 结束。若提示"用户名已存在"说明之前建过，属正常，跳过即可。

### 验证数据真的进库了

```powershell
docker compose -f D:\dev\study-OCR\deploy\docker-compose.dev.yml exec -T postgres psql -U studyocr -d studyocr -c "\dt" -c "select username,role from users order by id" -c "select count(*) as templates from ticket_templates" -c "select count(*) as rules from field_rules" -c "select version_num from alembic_version"
```

期望：约 17 张表（16 业务表 + `alembic_version`）、3 个用户、`templates=5`、`rules=37`、
`version_num=0009`。

---

## 10. 阶段 H：国密密钥、证书、登记（10 分钟，**必须用 Git Bash**）

为什么必须 Git Bash：`scripts/ca_init.sh` 是 bash 脚本，内部用 Docker 里的 OpenSSL 3.5.9 生成
SM2 密钥与证书（宿主机的 OpenSSL 版本太老，不支持 SM2）。在 PowerShell 里跑 `bash xxx.sh`
可能落到 WSL，那里没有 Docker 客户端，会失败。

打开 **Git Bash**（开始菜单搜 "Git Bash"），执行：

```bash
cd /d/dev/study-OCR

# 统一口令（只在本 terminal 生效，不会写进文件）
export STUDYOCR_KEY_PASSPHRASE='demo-pass-1234'

# 1) 建 CA（SM2-SM3，10 年）
bash scripts/ca_init.sh init

# 2) 提交人身份：私钥（口令加密的 PKCS#8）+ 公钥
.venv/Scripts/python.exe scripts/keygen.py --name test_sub --role submitter
# 3) 用 CA 给 test_sub 签发证书（私钥只走 stdin，不落盘）
bash scripts/ca_init.sh issue test_sub submitter

# 4) 财务服务器的信封身份（验签/解密要用）
.venv/Scripts/python.exe scripts/keygen.py --name finance --role finance
bash scripts/ca_init.sh issue finance server

# 5) 把证书与密钥路径登记进数据库（私钥仍然只在 secrets/，不入库）
.venv/Scripts/python.exe scripts/register_identity.py --user test_sub
.venv/Scripts/python.exe scripts/register_identity.py --server finance
```

期望输出（关键行）：

- `ca_init.sh init` → `CA 已生成：secrets/ca/ca.crt.pem` + subject/serial/fingerprint；
- `keygen.py` → 打印 `sign.p8.enc` / `sign.pub.pem` 路径，并自检"签名/验签通过"；
- `ca_init.sh issue` → `证书已签发`、`公钥一致性自检：通过`；
- `register_identity.py` → 打印已登记/已复用（重复执行是幂等的）。

### 验证

```bash
ls secrets/ca secrets/certs secrets/keys/test_sub secrets/keys/finance
bash scripts/ca_init.sh verify secrets/certs/test_sub.crt.pem   # → OK
.venv/Scripts/python.exe scripts/verify_chain.py               # → {"ok": true, ...}
```

```powershell
docker compose -f D:\dev\study-OCR\deploy\docker-compose.dev.yml exec -T postgres psql -U studyocr -d studyocr -c "select id,owner_type,subject_dn,revoked from certificates order by id" -c "select id,user_id,key_type,algo,cert_id from key_records order by id"
```

期望：`certificates` 2 行（test_sub、finance），`key_records` 2 行。
**注意**：`secrets/` 整目录被 gitignore，换机器要重新生成，不同机器的证书指纹不同
（所以两台机器**不要共用同一个数据库**，详见第 15.5 节）。

---

## 11. 阶段 I：生成合成票据样本（5 分钟）

仓库里**不包含**票据图片（含个人信息风险，只入库了 `MANIFEST.md`），要本地生成。
样本用于上传演示、OCR 基线与评测。

```powershell
Set-Location D:\dev\study-OCR
.\.venv\Scripts\python.exe scripts\make_samples.py
.\.venv\Scripts\python.exe scripts\verify_samples.py --ocr-count 10
```

期望：

- 第一条生成 **60 张**图到 `samples/synth_v1/`，并写标注 `backend/eval/datasets/annotations_synth_v1.jsonl`；
- 第二条五项验收全过，退出码 0（它会用 OCR 反查合成图，第一次会触发模型下载）。

**然后检查 git 干净**：

```powershell
git status --short
```

期望：**没有输出**。如果 `annotations_synth_v1.jsonl` 显示被修改，说明 Pillow 版本与作者机器不同
导致重绘有细微差异——**不要提交它**，在汇报里写一条即可（图片不入库，不影响运行）。

---

## 12. 阶段 J：起三个进程（5 分钟）

用**三个后台任务**分别启动，不要用一个窗口串起来。

**① 后端 API**

```powershell
Set-Location D:\dev\study-OCR\backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --port 8000 --host 127.0.0.1
```

**② ARQ worker（OCR 任务执行者，不启动就永远卡在"识别中"）**

```powershell
Set-Location D:\dev\study-OCR\backend
..\.venv\Scripts\python.exe -m arq app.tasks.worker.WorkerSettings
```

**③ 前端**

```powershell
Set-Location D:\dev\study-OCR\web
npm ci          # 若报 lock 不同步，改用 npm install
npm run dev     # http://127.0.0.1:5173
```

### 验证

```powershell
curl.exe -s http://127.0.0.1:8000/api/v1/health
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8000/docs
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:5173/login
```

期望：`/health` 返回 `"code":0`，`checks` 里 `db` / `redis` / `minio` 三项都 `ok:true`；
后两个 HTTP 码都是 `200`。

**前端端口冲突**：5173 被占用时 Vite 会直接报错（`strictPort`），不会自动换端口。
处理方式：关掉占用进程，或改 `web/vite.config.ts` 的端口 **并且**在 `backend/.env` 的
`CORS_ORIGINS` 里加上新端口——但改端口属于代码改动，**先报告再改**。

---

## 13. 阶段 K：全量验收（必须全绿，逐条贴输出）

按顺序跑，全部通过才算"跑起来了"。

```powershell
Set-Location D:\dev\study-OCR

# 1) 环境冒烟（配置 + 三件套 + 后端 + 迁移）
py -3.11 scripts\smoke.py --strict
#    期望：FAIL 0，WARN 0，退出码 0

# 2) 后端单元测试（默认套件，不碰真实服务）
Set-Location backend
..\.venv\Scripts\python.exe -m pytest -q
#    期望：全部 passed，failed 0（数量级参考：759 passed，以实际输出为准）
#    注意：默认套件不连真实服务（mock/桩），不需要 Docker 在跑

# 3) 集成测试（需要 Docker 三件套在跑；会连开发库，用例自带清理，属正常）
..\.venv\Scripts\python.exe -m pytest -m integration -q
#    期望：全部 passed（数量级参考：359 passed）

# 4) 前端类型检查与生产构建
Set-Location ..\web
npm run typecheck
npm run build
#    期望：两条都退出码 0（会生成 web/dist，已被 gitignore）

# 5) 端到端演示（不依赖 8000 端口，自带 TestClient；用确定性 OCR 桩，密码/存储/DB 全真）
Set-Location ..
.\.venv\Scripts\python.exe scripts\e2e_demo.py
#    期望：上传→确认→建单→签名→提交→验签→解密→审核→归档 全链路跑完，退出码 0
```

### 最后一项：浏览器人工走查（必须人来做）

告诉用户打开 <http://127.0.0.1:5173/login>，用 **`test_sub` / `Test_sub!2026`** 登录，然后：

1. 侧栏「上传票据」→ 选一张 `samples/synth_v1/` 里的 jpg → 等 **识别完成**（这一步验证 OCR + ARQ worker 真的在工作，首次会下载模型，慢一点正常）；
2. 点「查看详情」→ 左侧票面、右侧字段，低置信字段有黄/红底；
3. 改错字段 → 勾选"确认金额" → 「确认整单」→ 状态变 **待复核**；
4. （可选）用 `test_rev` 登录看看待审核队列、用 `test_admin` 登录看看模板/证书/审计页。

完整的四角色流程（建报销单 → 签名 → 提交 → 财务验签解密 → 复核 → 归档）见**作者单独提供的
`笔记.txt`**（答辩演示脚本）或 `docs/UI_TEST_FLOW.md`（逐项人工走查清单）；
跑通上面第 1–3 步即可认为"环境完全可用"。

---

## 14. 阶段 L：PyCharm 配置（作者主要用 PyCharm）

**不要**把 PyCharm 的 `.idea/` 提交入库（已在 `.gitignore`）。

1. `File → Open` 选 **仓库根目录** `D:\dev\study-OCR`（不是 `backend`，不是 `web`）。
2. 解释器：`Settings → Project → Python Interpreter → Add Interpreter → Add Local Interpreter →
   Existing` → 选 `D:\dev\study-OCR\.venv\Scripts\python.exe`。
   选完后 PyCharm 能跳转 `app.*` 模块、pytest 能直接点绿色三角跑。
3. 建两个 Run/Debug Configuration（`Python` 类型）：
   - **backend**：Module name = `uvicorn`，Parameters = `app.main:app --reload --port 8000`，
     Working directory = `D:\dev\study-OCR\backend`；
   - **worker**：Module name = `arq`，Parameters = `app.tasks.worker.WorkerSettings`，
     Working directory = `D:\dev\study-OCR\backend`。
4. Terminal：`Settings → Tools → Terminal → Shell path` 选 **Git Bash**
   （`C:\Program Files\Git\bin\bash.exe`），这样第 10 节的命令可以直接在 PyCharm 里跑。
5. 可选：右键 `backend` 目录 → `Mark Directory as → Sources Root`，跳转更顺。
6. 前端不在 PyCharm 里管，用终端跑 `cd web && npm run dev`。

> 说明：项目自己读 `backend/.env`（pydantic-settings），**不需要**装 EnvFile 插件、
> 也不需要把变量配到 Run Configuration 里。

---

## 15. 阶段 M：两人用 GitHub 共同开发

### 15.1 先解决"能不能写"的问题

- **方式 A（推荐，简单）**：仓库所有者把队友加成 Collaborator
  （GitHub 仓库 → Settings → Collaborators → Add people，用对方的 GitHub 用户名/邮箱）。
- **方式 B（Fork）**：队友先 Fork 到自己账号，然后：

  ```powershell
  git clone https://github.com/<队友用户名>/study-OCR.git
  cd study-OCR
  git remote add upstream https://github.com/HeXiao-Xu/study-OCR.git
  git fetch upstream
  ```

  每次改完推到自己的 fork，然后在 GitHub 上向 `HeXiao-Xu/study-OCR:main` 发 Pull Request。

### 15.2 每天的标准动作

```powershell
# 开工前：同步
git fetch origin
git switch main
git pull --ff-only origin main

# 干活：永远在自己的分支上
git switch -c feat/s6-1-recog-compare

# ... 改代码、跑测试 ...

git add -A
git status --short          # 确认没有 .env / secrets/ / samples 图片
git commit -m "feat(s6-1): 识别模型对比实验脚本与消融配置"
git push -u origin HEAD     # 推自己的分支
```

然后在 GitHub 上开 Pull Request → 对方 review → 合并到 `main` → **两边都 `git pull`**。

> 走 **Fork** 方式时，把上面的 `origin` 理解为 `upstream`：
> 同步用 `git fetch upstream && git switch -c feat/xxx upstream/main`，
> 推送仍然推 **自己的 fork**（`git push -u origin HEAD`），再向作者的仓库发 PR。

### 15.3 提交信息与文档纪律（项目原有规矩，别破坏）

- 提交信息用 Conventional Commits，带任务号，例如
  `feat(s6-1): ...`、`fix(s5-4): ...`、`docs(onboarding): ...`；
- 功能级任务走 **spec → plan → 实现**：spec 落 `docs/spec/<任务号>-<主题>.md`，
  plan 落 `docs/plan/<任务号>-<主题>.md`（见 `AGENTS.md` §11）；
- 改完代码必须：**更新 `docs/PROGRESS.md`**；动接口补 `docs/API.md`；动表补 `docs/SCHEMA.md`；
  动配置补 `.env.example`；动依赖补 `requirements.txt` 并在 `docs/DECISIONS.md` 记一条；
- `docs/EVAL.md` 里的实验数字是**只读契约**，AI 不得编写/修改数字。

### 15.4 冲突怎么处理

```powershell
git switch main
git pull --ff-only origin main
git switch feat/xxx
git rebase main          # 或 git merge main
# 解冲突 → git add <文件> → git rebase --continue
git push --force-with-lease   # 只允许推自己的分支，绝不对 main 用 force
```

- `package-lock.json` 冲突：**不要手改**，以 `main` 为准，重新 `npm install` 生成后提交。
- **Alembic 迁移冲突**：两个人不要同时写迁移。若 `alembic heads` 出来两个 head，
  停下来报告，由写迁移的人调整 `down_revision` 顺序，**不要删改已应用的迁移**。
- 其它文件冲突按语义合并；拿不准就问，不要为了"看起来干净"而丢对方的改动。

### 15.5 每台机器各自独立的东西（不要共享、不要提交）

| 东西 | 说明 |
|---|---|
| `deploy/.env` / `backend/.env` | 每台机器自己生成；口令可以不同 |
| `secrets/`（CA、私钥、证书） | **每台机器自己生成，证书指纹不同** |
| `samples/synth_v1/` 图片 | 每台机器自己 `make_samples.py` 生成 |
| `.venv/`、`node_modules/`、`web/dist/` | 本机构建产物 |
| PostgreSQL / Redis / MinIO 数据卷 | 本机 Docker 卷 |

**推论**：两台机器**各自用各自的本机数据库**最省事。
如果以后要共用同一个数据库（比如部署到一台服务器），必须同时满足：
① `AUDIT_HMAC_KEY` 两边完全一致（否则审计链验不过）；
② 共用同一套 `secrets/`（离线拷贝，**绝不入 git**），否则财务端解不开对方封的信封；
③ 谁跑迁移谁负责，另一方只 `alembic upgrade head`。

---

## 16. 已知坑（Windows 专属，按疼的程度排序）

| # | 现象 | 原因与处理 |
|---|---|---|
| 1 | `pip install paddlepaddle` 报 "No matching distribution" | 解释器不是 3.11。删 `.venv`，用 `py -3.11 -m venv .venv` 重建 |
| 2 | `docker pull` 卡住后 `registry-1.docker.io` 超时 | 用 `bash scripts/pull_image.sh <镜像>`；或在 Docker Desktop → Settings → Docker Engine 配 `registry-mirrors` 后重启 Docker |
| 3 | PostgreSQL 容器起不来 / `initdb: cannot be run as root` | 别把数据目录改成绑定挂载，用命名卷（默认就是） |
| 4 | `port is already allocated` | 端口被占。查 `netstat -ano \| findstr :5433`，或改 `deploy/.env`（同时改 `backend/.env`） |
| 5 | 后端启动报配置缺失（`ConfigError`） | `backend/.env` 没建或缺 `JWT_SECRET` / `AUDIT_HMAC_KEY` / 三件套地址 |
| 6 | `/health` 返回 503 degraded | 看返回体 `checks` 哪项 `ok:false`；多半是容器没起或口令不一致 |
| 7 | 上传后一直"识别中" | **ARQ worker 没启动**（第 12 节 ②） |
| 8 | 控制台中文乱码 | PowerShell 先跑 `chcp 65001`；Git Bash 一般正常 |
| 9 | 中文路径导致工具异常 | 仓库必须放在纯 ASCII 路径（第 5 节） |
| 10 | 每次改动显示整文件 diff | 仓库 `.gitattributes` 统一 LF；不要关掉，也不要手动转 CRLF |
| 11 | `bash scripts/xxx.sh` 报没有 docker | PowerShell 把 bash 解析到 WSL 了；改用开始菜单的 **Git Bash** |
| 12 | 证书/验签报"证书无效/已过期" | **系统时间不对**。Windows 设置里开启自动同步时间后重试 |
| 13 | 杀毒软件拦截 Docker/pip | 临时把 `D:\dev\study-OCR`、`.venv`、Docker Desktop 加入白名单 |
| 14 | 前端 `npm ci` 报 lock 不同步 | 用 `npm install` 代替；并把 `package-lock.json` 的改动情况报告出来，**不要盲目提交** |

---

## 17. 收尾：给一份可复核的汇报

全部做完（或卡在某一步）后，按这个模板输出，方便作者核对：

```markdown
## 环境搭建结果

### 1. 机器与版本
- Windows 版本 / CPU / 内存 / 磁盘剩余：
- Git: / Python: （3.11 的具体路径）/ Node: / npm: / Docker: / Compose:

### 2. 路径与仓库
- 仓库路径：
- HEAD 提交：（git log -1 --oneline）
- git status：（应为 clean）

### 3. 服务状态
- docker compose ps：三个容器（healthy/其它）
- /health 返回：（贴 JSON）
- 前端 5173：（HTTP 码）

### 4. 验收命令实测
| 命令 | 结果 |
|---|---|
| scripts/smoke.py --strict | FAIL ? WARN ? 退出码 ? |
| pytest -q | ? passed / ? failed |
| pytest -m integration -q | ? passed / ? failed |
| npm run typecheck | 退出码 ? |
| npm run build | 退出码 ? |
| scripts/e2e_demo.py | 退出码 ? |

### 5. 密钥与数据
- certificates / key_records 行数：
- ticket_templates / field_rules 行数：
- samples 生成张数：
- alembic version_num：

### 6. 人工走查
- test_sub 登录：成功/失败
- 上传一张票 → 识别完成：成功/耗时
- 确认整单 → 待复核：成功/失败

### 7. 与文档不一致或卡住的地方（如实写，不要美化）
- ...
```

---

## 附录 A：演示账号与口令

| 用户名 | 口令 | 角色 | 登录后落地 |
|---|---|---|---|
| `test_sub` | `Test_sub!2026` | 提交人 submitter | 我的票据 `/invoices` |
| `test_rev` | `Test_rev!2026` | 审核人 reviewer | 报销审核 `/finance/pending` |
| `test_admin` | `Test_admin!2026` | 管理员 admin | 票种模板 `/templates` |

- **私钥口令**（`test_sub`、`finance` 的 SM2 私钥）：`demo-pass-1234`
- 这个口令同时写在 `backend/.env` 的 `FINANCE_KEY_PASSPHRASE`；
  签名/提交时页面会让你输入的就是它。
- **以上口令仅用于本地演示与毕设答辩，不是生产配置。**

## 附录 B：端口、容器、数据卷

| 服务 | 宿主机端口 | 容器名 | 数据卷 |
|---|---|---|---|
| PostgreSQL 16 | 5433 | `study-ocr-postgres` | `study-ocr-pgdata` |
| Redis 7 | 6380 | `study-ocr-redis` | `study-ocr-redisdata` |
| MinIO API | 9002 | `study-ocr-minio` | `study-ocr-miniodata` |
| MinIO 控制台 | 9003 | 同上 | — |
| 后端 API | 8000 | — | — |
| 前端 Vite | 5173 | — | — |

- `docker compose down` 保留数据；`down -v` **清空数据**。
- MinIO 控制台地址 <http://127.0.0.1:9003>，账号密码 = `deploy/.env` 里的 `MINIO_ROOT_USER` / `MINIO_ROOT_PASSWORD`。

## 附录 C：日常命令速查（每次开机后）

```powershell
# 1) 起容器（已起则无副作用）
docker compose -f D:\dev\study-OCR\deploy\docker-compose.dev.yml up -d

# 2) 后端（新窗口）
cd D:\dev\study-OCR\backend
..\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000

# 3) worker（新窗口）
cd D:\dev\study-OCR\backend
..\.venv\Scripts\python.exe -m arq app.tasks.worker.WorkerSettings

# 4) 前端（新窗口）
cd D:\dev\study-OCR\web
npm run dev

# 5) 出问题先跑冒烟
cd D:\dev\study-OCR
py -3.11 scripts\smoke.py --verbose
```

## 附录 D：明确**不要**做的事

1. 不要把 `.env`、`secrets/`、`samples/` 图片、真实票据提交进 Git（不要 `git add -f`）。
2. 不要升级 `paddleocr` / `paddlepaddle` / `numpy` / `opencv-python` / `SQLAlchemy`（整组锁死）。
3. 不要改已提交的 Alembic 迁移文件；不要跳过测试让 CI"变绿"。
4. 不要用 `random` 生成密码学随机数、不要自研密码算法（项目已封装在 `app/crypto/`）。
5. 不要直接 `import gmssl` / `cryptography` 到业务代码（只能调 `app/crypto/` 的封装）。
6. 不要对 `main` 做 `git push --force`；不要提交 `node_modules` / `.venv` / `dist` / `.idea`。
7. 不要把明文金额、税号、身份证号打印进日志（项目有 `utils/masking.py`）。
8. 不要在没有 spec/plan 的情况下直接写功能级代码（见 `AGENTS.md` §11）。