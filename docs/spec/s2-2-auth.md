# S2-2 JWT 认证 + RBAC 三角色 — 设计（spec）

> 任务卡：S2-2 ｜ 契约：`docs/API.md` §0 / §1 / §10 / §12 ｜ 上游：S2-1（`users` 表、`core/database.py`）｜ 下游：S2-3 行级隔离、S2-9 前端登录、S5 审核
> 状态：**已实现（2026-11-19）**；§8 两个冲突项的最终口径已写入，实测见 §10。

## 1. 目标与范围

交付**可用的登录会话与角色边界**：注册（见 §8 冲突 A）、登录、`me`、刷新、登出；`use` 依赖 `get_current_user` /
`require_roles(...)` 供后续所有接口复用。

**做**：
- `POST /auth/register`（若按契约开放）、`POST /auth/login`、`GET /auth/me`、`POST /auth/refresh`、`POST /auth/logout`；
- access 30min + refresh 7d；refresh **轮换式**、存 Redis 白名单；登出即吊销 refresh（D-016）；
- Argon2id 口令哈希；HS256 JWT；三角色依赖；
- 初始化脚本 `scripts/create_user.py`（建 `reviewer` / `admin`，或批量建账号）；
- 单元测试（令牌/口令，无外部依赖）+ 集成测试（临时库 + 假 Redis 白名单，真实 HTTP 链路）。

**不做**：行级数据隔离（S2-3）、前端登录页与守卫（S2-9）、审计落库（S4-7，见 §8 冲突 B）、
自动锁定/登录失败计数（`users.status='locked'` 只作为状态值被识别，不主动写）、限流、单端互踢、SSO。

## 2. 契约口径（逐条对照 API.md）

| 项 | 落地 |
|---|---|
| 统一壳 | `{code,message,data,trace_id}`，复用 `core.exceptions.ok()` 与全局异常处理器 |
| 令牌 | `Authorization: Bearer <jwt>`；HS256；载荷 `{sub, role, jti, iat, exp}` + **`typ`**（见 §4） |
| 登录出参 | `{access_token, token_type:"bearer", expires_in:1800, refresh_token, refresh_expires_in:604800, user:UserOut}` |
| 刷新出参 | 同上去掉 `user`；**轮换**：成功即删旧 `jti`、写新 `jti` |
| 注册出参 | `201` + `UserOut`；服务端固定 `role=submitter`，请求体不接受角色 |
| 用户名 | trim + 转小写；3–64 字符、不含空白；不区分大小写 |
| 口令 | 8–128 字符；只存 Argon2id 哈希，任何响应/日志不出现 |
| 错误 | 登录失败统一 `401/40101`"用户名或密码错误"（不区分账号是否存在）；`disabled/locked` → `403/40301`；无/坏/过期令牌 → `401/40101`；`me` 用户不存在 → `404/40400`；重名注册 → `409/40900` |
| Redis | key `auth:refresh:{jti}`，value=`user_id`，TTL=refresh 剩余有效期（取 7 天） |
| 多端登录 | 允许，各自独立 `jti`；刷新一个不影响另一个 |

## 3. 分层与文件

```
app/core/security.py          口令哈希（Argon2id）+ JWT 编解码（不碰数据库/Redis）
app/core/redis.py             Redis 客户端单例（S2-7 队列复用；健康检查仍走自己的短连接）
app/core/config.py            + access/refresh TTL、Argon2 参数、refresh key 前缀
app/core/exceptions.py        + AccountDisabledError(40301/403)、RoleForbiddenError(40301/403)
app/core/database.py          + get_db() 请求级会话依赖（不提交，由 service 决定事务边界）
app/services/token_store.py   RefreshTokenStore 协议 + RedisRefreshTokenStore
app/services/auth_service.py  注册/认证/签发/轮换/吊销（唯一业务入口）
app/schemas/auth.py           RegisterRequest/LoginRequest/RefreshRequest/LogoutRequest/UserOut/AuthTokens/LoginData
app/api/deps.py               HTTPBearer、get_current_user、require_roles、get_token_store
app/api/v1/auth.py            5 条路由（每个 ≤30 行，只做校验→service→壳）
app/main.py                  挂载 auth router（前缀 settings.api_prefix）
scripts/create_user.py        初始化脚本（reviewer/admin 只能由它创建）
tests/{test_security,test_auth}.py + tests/db_helpers.py（从 test_migration 抽出的建库/迁移助手）
```

## 4. 令牌与会话设计

**载荷**：`sub`（用户 id 的字符串）、`role`（签发时快照，仅用于快速判断与日志）、`jti`（`secrets.token_hex(16)`）、
`iat`、`exp`，外加 **`typ` = `access` / `refresh`**。

- `typ` 不属于 API.md 列出的样板载荷，但**必须有**：否则 refresh token 可直接当 Bearer 使用，
  7 天的长令牌就变成 7 天的访问权限。这是安全必要项，不是冗余字段。
- 解码时 `algorithms=["HS256"]`（显式白名单，防 `alg=none` 与算法混淆），`require=["exp","iat","sub","jti","typ"]`。
- **授权只信数据库**：`get_current_user` 按 `sub` 回表，角色、状态都以库为准；token 里的 `role` 只用于日志。
- **登出只吊销 refresh**：access token 在 30 分钟内仍可用（无状态令牌的固有代价）。不做 access 黑名单，
  否则每次请求都要查 Redis，等于把认证变成有状态；把这条边界写进论文"不足"（D-039）。
- 刷新轮换：`decode → 查白名单 → 删旧 jti → 回表校验用户状态 → 签新对 → 写新 jti`。
  旧令牌被重放时白名单已无该 `jti`，直接 401。
- 登出：删除请求体里 refresh token 的 `jti`；**重复登出返回 200**；body 可选（前端只带 access 也能登出，只是不吊销 refresh）。

## 5. 口令哈希（Argon2id）

- `passlib.CryptContext(schemes=["argon2"], argon2__type="ID", argon2__memory_cost=65536,
  argon2__time_cost=3, argon2__parallelism=4)`；
- 参数全部进 `Settings`，可调；默认 **m=64 MiB / t=3 / p=4**（高于 OWASP 最低线 m=19MiB、t=2、p=1）；
- `memory_cost` 是隐藏的执行时间与内存瓶颈：每次登录约几十毫秒、并发登录时线程池内存会上去，
  实测数据进 `DECISIONS.md`（任务卡明确要求）；
- 用户不存在时**也跑一次** `verify`（固定 dummy hash），避免"响应快 → 账号不存在"的枚举侧信道；
- `password_hash` 为空（SSO 占位）→ 统一 401，不区分。

## 6. 鉴权依赖与角色边界

```python
get_current_user(creds=Depends(HTTPBearer(auto_error=False)), db=Depends(get_db)) -> User
require_roles("reviewer", ...) -> Callable[[User], User]
```

- 缺 Bearer / 解析失败 / 类型不是 access / 过期 → `AuthError(40101/401)`；
- 用户不存在 → `NotFoundError(40400/404)`（`me` 按契约）；
- 用户被禁用/锁定 → **401**（API.md §1 对 `me` 的契约；登录接口才用 `AccountDisabledError` 的 403）；
- `require_roles` 不满足 → `RoleForbiddenError(40301/403)`（**角色级拒绝**，API.md §0 明确 403；
  资源级越权在 S2-3 用 `PermissionDeniedError` → 统一 404，两者不要混）。
- 现有 `PermissionDeniedError` 被全局处理器转成 404，**不能**拿它表达角色级 403，故新增两个异常子类。

## 7. 安全细节（论文可引）

1. 登录失败统一文案与状态码，账号禁用才 403（API.md 的取舍：管理员需要知道账号被禁用）；
2. 失败日志只记 `username_sha256`（前 16 位）与 `trace_id`，不记口令、不记完整用户名之外的可逆信息；
3. `refresh` 轮换 + Redis 白名单：令牌被窃取后只能"抢用一次"，且登出真的能吊销；
4. JWT 只做"身份声明"，不做权限缓存；角色变更下一次请求即生效；
5. 不把 token 放 URL/query；不接受 `token` 查询参数；
6. 注册/登录都做 `trim + lower`，避免 `Alice` 与 `alice` 双账号（唯一索引是大小写敏感的）。

## 8. 与任务卡 / 契约的冲突（已拍板）

### A. `POST /auth/register`：API.md 有公开接口，任务卡说"仅初始化脚本调用" → 按 API.md 开放

- 用户拍板：**按 API.md 实现公开注册**（固定 `submitter`）；`reviewer` / `admin` 仍只能由
  `scripts/create_user.py` 创建。
- 落地：`POST /auth/register` 201 + `UserOut`；重名 409；用户名 trim + 小写；请求体不接受角色字段。

### B. 审计动作枚举缺口 → 先改 SCHEMA + 0002 迁移，S2-2 仍不写审计行

- 用户拍板：**先加枚举值 + `0002` 迁移**（`login_failed` / `logout` / `forbidden`），为 S4-7 铺路；
- 落地：`SCHEMA.md` §9.19b 增至 12 值；`models/enums.py` 同步；迁移只放开 CHECK；
- **S2-2 不写 `audit_logs`**：哈希链在 S4-7 才实现，写裸行会破坏链式不变式（D-040）；
  登录失败/登出只写带 `trace_id` 与 `username_sha256` 的应用日志；
- 仍缺的 `template_create` / `report_create` / `issue_cert` 登记在 `PROGRESS.md` 遗留 20，
  在对应任务前按同一流程补。

## 9. 测试设计

**单元 `tests/test_security.py`（无 DB/Redis，默认 `pytest` 跑）**
- 口令：哈希以 `$argon2id$` 开头、正确口令通过、错误口令拒绝、空哈希拒绝、同一口令两次哈希不同（盐）；
- JWT：载荷字段齐全、`typ` 正确、过期令牌 401、签名被改 1 字节 401、`alg=none` 401、
  access 当 refresh 用 401（反之亦然）、`jti` 两次签发不同。

**集成 `tests/test_auth.py`（临时库 + 内存白名单，`-m integration`）**
- 注册：201 + 用户名转小写 + 无 `password_hash` 字段；重名 409；短口令 422；空白用户名 422；
- 登录：正确凭据 200 且 `expires_in=1800` / `refresh_expires_in=604800`；未知账号与错口令 **同码同文案**；
  `disabled` / `locked` → 403；两次登录拿到不同 `jti`，两个 refresh 都可用（多端）；
- `me`：正常 200；无/坏/过期 token 401；用户被禁用后 401；token 里伪造 `role=admin` 仍返回库里的 `submitter`；
- 刷新：轮换成功、旧 refresh 再用 401；拿 access 调 refresh 401；刷新后旧 access 仍可用（30min 窗口，断言现状）；
- 登出：200 + `logged_out:true`；重复登出 200；登出后 refresh 401；未登录登出 401；
- RBAC：测试路由 `require_roles("reviewer")`，submitter 403、reviewer 200、无 token 401
  （对应任务卡验收"submitter token 调财务接口返回 403"）。

## 10. 验收命令

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m pytest                       # 单元全绿（含既有 264）
../.venv/Scripts/python.exe -m pytest -m integration -q     # 集成全绿（需 Docker PG）
# 手工链路（后端起在 8000）：
curl -s -X POST localhost:8000/api/v1/auth/register -H 'Content-Type: application/json' -d '{"username":"alice","password":"Passw0rd!","real_name":"张三"}'
curl -s -X POST localhost:8000/api/v1/auth/login    -H 'Content-Type: application/json' -d '{"username":"alice","password":"Passw0rd!"}'
curl -s localhost:8000/api/v1/auth/me -H "Authorization: Bearer <access>"
curl -s -X POST localhost:8000/api/v1/auth/refresh  -H 'Content-Type: application/json' -d '{"refresh_token":"<refresh>"}'
curl -s -X POST localhost:8000/api/v1/auth/logout   -H "Authorization: Bearer <access>" -H 'Content-Type: application/json' -d '{"refresh_token":"<refresh>"}'
../.venv/Scripts/python.exe ../scripts/create_user.py --username reviewer1 --role reviewer --real-name 审核员
../.venv/Scripts/python.exe -m pytest -m integration -q tests/test_auth.py
```

通过标准：两套测试全绿；`me` 返回的角色来自数据库；登出后 refresh 必然 401；日志里 grep 不到口令。

**实测（2026-11-19）**：

- `pytest` → **288 passed**（新增 24 条认证单元用例）；`pytest -m integration` → **51 passed**（认证 28 + 迁移 23）；
- 真实 uvicorn + Redis：register 201 → login 200（Redis 白名单 1 key）→ me 200 → refresh 轮换（旧 refresh 401）
  → logout 200 → refresh 401；冒烟用户与 Redis key 已清理；
- `scripts/create_user.py` 创建 reviewer → 登录 → `me.role=reviewer`；
- Argon2id 实测 hash **59.4ms** / verify **45.7ms**（m=65536,t=3,p=4）；
- `scripts/smoke.py` **8 OK / 0 FAIL**（后端起时）。

## 11. 收口清单

- `PROGRESS.md` 勾 S2-2 + 实测数字；
- `DECISIONS.md`：D-038（PyJWT + passlib Argon2id 参数与实测耗时）、D-039（登出只吊销 refresh 的边界）；
- `AGENTS.md` §10.8 回填"认证与权限依赖"锚点；
- `.env.example` 同步新增配置项；
- `HANDOFF.md` 更新现状与下一步（S2-3）；
- 提交 `feat(s2-2): JWT 认证 + RBAC 三角色（refresh 轮换 / Redis 白名单 / Argon2id）`；**push 由用户执行**。

## 12. 风险与回滚

| 风险 | 处理 |
|---|---|
| Argon2 内存参数过高导致并发登录变慢 | 参数进配置；实测登录耗时写 D-038；必要时降到 m=32MiB |
| 轮换时"删旧成功、写新失败"导致用户被登出 | 先删后写；失败返回 401，用户重新登录即可（不产生安全问题） |
| Redis 不可用时登录/刷新失败 | 登录签 access 也依赖白名单写 refresh → 失败返回 503/50000 或 50300；**不允许**"白名单写失败但仍返回 token" |
| 测试污染开发库 | 集成测试用临时库 `study_ocr_auth_<hex>`，`finally` 删除；不碰 `studyocr` |
| 口令/令牌进日志 | 日志字段白名单：只记 `event/trace_id/username_sha256/user_id`；测试里断言日志无口令 |