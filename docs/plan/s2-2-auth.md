# S2-2 实现计划（plan）

> 对应 spec：`docs/spec/s2-2-auth.md` ｜ 契约：`docs/API.md` §0/§1/§10 ｜ 状态：**已实现（2026-11-19）**，计划与实现同步落盘
> 约定：命令在 `E:\BiSheXZY\study-OCR` 下执行，Python 一律 `.venv/Scripts/python.exe`。

## Step 0 — 依赖与配置

```bash
.venv/Scripts/python.exe -m pip install "passlib[argon2]==1.7.4" "PyJWT==2.15.1"
```

- `backend/requirements.txt` 新增：`passlib[argon2]==1.7.4`、`PyJWT==2.15.1`、`argon2-cffi==25.1.0`（显式锁）；
- 重新生成 `requirements.lock.txt` 并核对 diff（只允许新增认证相关行）；
- `core/config.py` 新增：`jwt_algorithm="HS256"`、`access_token_ttl_seconds=1800`、`refresh_token_ttl_seconds=604800`、
  `refresh_token_prefix="auth:refresh"`、`argon2_memory_cost=65536`、`argon2_time_cost=3`、`argon2_parallelism=4`；
- `.env.example` 同步说明（可不填，走默认）。

**验证**：`python -c "import jwt, passlib; print(jwt.__version__, passlib.__version__)"`；`pytest -q` 不回归。

## Step 1 — 异常与 Redis 客户端

- `core/exceptions.py` 加 `AccountDisabledError`（40301/403，账号停用/锁定）与 `RoleForbiddenError`（40301/403，角色级拒绝）；
- `core/redis.py`：`get_redis()`（`lru_cache`，`Redis.from_url(settings.redis_url, decode_responses=True, socket_timeout=...)`）。

**验证**：`python -c "from app.core.exceptions import RoleForbiddenError; print(RoleForbiddenError.http_status)"`；
`python -c "from app.core.redis import get_redis; print(type(get_redis()).__name__)"`（不真的连库）。

## Step 2 — `core/security.py`（纯函数，无 IO）

- `hash_password` / `verify_password`（passlib Argon2id，参数来自 Settings）；
- `encode_token(user_id, role, token_type, ttl_seconds) -> (jwt_str, jti)`；
- `decode_token(token, expected_type) -> TokenPayload`；`typ`/`exp`/`sub`/`jti` 必填；
- 用户不存在时的 dummy verify（防枚举侧信道）；
- `TokenType` 枚举（access/refresh）与 `TokenPayload` Pydantic 模型。

**验证**：`pytest -q tests/test_security.py`（先写测试后写实现）。

## Step 3 — `services/token_store.py`

`RefreshTokenStore` 协议（`save/get/delete`）+ `RedisRefreshTokenStore`（key `auth:refresh:{jti}`，value=user_id，
`setex` TTL）+ 依赖可注入（测试用内存实现）。

**验证**：单元测试用内存实现即可；Redis 实现由集成测试间接覆盖（S2-2 集成测试用内存实现，不依赖 Redis 服务）。

## Step 4 — `schemas/auth.py` + `services/auth_service.py`

- 请求模型：`RegisterRequest`（username 3–64、trim/lower、无空白；password 8–128；real_name 1–50；dept ≤50）、
  `LoginRequest`、`RefreshRequest`、`LogoutRequest(refresh_token 可选)`；
- 出参模型：`UserOut`（id/username/real_name/dept/role/status/created_at）、`AuthTokens`、`LoginData`；
- service：`create_user`（含唯一冲突→409）、`authenticate`（统一 401 / 禁用 403 / dummy verify）、
  `issue_session`（签 access+refresh，写白名单）、`rotate_refresh`（删旧写新）、`revoke_refresh`；
- 事务边界：`create_user` 内部 `commit`，异常 `rollback` 后抛 `AppError` 子类。

**验证**：`pytest -q tests/test_security.py`，再进 Step 5 的集成测试。

## Step 5 — `api/deps.py` + `api/v1/auth.py` + `main.py`

- `get_db()` 从 `core/database.py` 出（yield 会话，不提交）；
- `HTTPBearer(auto_error=False)`；`get_current_user` 按 `sub` 回表并校验 `typ=access` / `status=active`；
- `require_roles(*roles)`：不满足抛 `RoleForbiddenError`（403，不是 404）；
- 路由 5 条：register(201)、login、me、refresh、logout；统一 `ok()` 壳；
- `main.py` 挂载 `auth_router`，前缀 `settings.api_prefix`。

**验证**：起 uvicorn 后跑 spec §10 的 curl 链路（register→login→me→refresh→logout→refresh 401）。

## Step 6 — 初始化脚本

`scripts/create_user.py`：`--username --role {submitter,reviewer,admin} --real-name [--dept] [--password]`；
不给 `--password` 时 `getpass` 交互输入（不落 shell 历史）；重复用户名 → 明确报错退出非 0；
输出 `id/username/role`（不输出哈希）。复用 `services.auth_service.create_user`，避免两套逻辑。

**验证**：`create_user.py --username reviewer1 --role reviewer --real-name 审核员 --password 'Passw0rd!'`，
再 `GET /auth/me` 用该账号登录返回 `reviewer`。

## Step 7 — 测试

- 抽出 `backend/tests/db_helpers.py`（`temporary_database` / `alembic_config` / `_tables`），`test_migration.py` 改为引用；
- `tests/test_security.py`（单元，spec §9 第一组）；
- `tests/test_auth.py`（集成：临时库 + 内存白名单 + `dependency_overrides`，spec §9 第二组）；
- 固定断言"登录失败同码同文案""refresh 轮换后旧令牌 401""token 里的 role 不影响 `me`"。

**验证**：`pytest`（默认跑单元）与 `pytest -m integration -q`（集成）均全绿。

## Step 8 — 收口

```bash
cd /e/BiSheXZY/study-OCR/backend
../.venv/Scripts/python.exe -m pytest
../.venv/Scripts/python.exe -m pytest -m integration -q
cd .. && .venv/Scripts/python.exe scripts/smoke.py   # 后端起时 8 OK
```

- 文档：`PROGRESS.md`（勾 S2-2、实测 Argon2 耗时、遗留"审计动作枚举缺口"）、`DECISIONS.md`（D-038/D-039）、
  `AGENTS.md` §10.8 认证锚点、`HANDOFF.md` 下一步 S2-3、`.env.example`；
- 提交：`feat(s2-2): JWT 认证 + RBAC 三角色（refresh 轮换 / Redis 白名单 / Argon2id）`；**不 push**。

## 回滚与降级

| 情况 | 处理 |
|---|---|
| passlib 与 argon2-cffi 版本不兼容 | 固定 `passlib==1.7.4` + `argon2-cffi==25.1.0`；不行则降 23.1.0 并记 DECISIONS |
| Redis 未起 | 登录/刷新返回 503/50300（"依赖不可用"），不返回半套 token；`/health` 已能提示 |
| 集成测试污染开发库 | 临时库前缀 `study_ocr_auth_`，`finally DROP DATABASE ... WITH (FORCE)` |
| 现有测试回归 | 认证改动只新增依赖/路由/异常子类；健康检查与 S1 模块不触碰 |