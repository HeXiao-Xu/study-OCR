# S2-12 定时任务骨架与阶段收口 — 设计（spec）

> 任务卡：S2-12 ｜ 契约：`API.md` / `SCHEMA.md` 对账 ｜ 上游：S2-7 作业、S4-7 审计链（未来）
> 状态：**已实现（2026-11-19）**；锁语义按双进程实测修正为"成功后保留到 TTL"，见 §4。

## 1. 目标

给 API 进程装上 APScheduler，注册每日 02:00 的审计链锚定任务（S4-7 才实现逻辑，现在只打日志）；
**多副本/多 worker 时同一次任务只执行一次**（Redis 分布式锁）；最后做 S2 阶段文档与接口对账。

**做**：
- 安装并钉 `APScheduler==3.11.1`（含 `tzlocal`），更新 `requirements.txt` / lock / `DECISIONS`；
- `core/config.py`：`scheduler_enabled`、时区、cron 时/分/秒、锁 key、锁 TTL 集中配置；
- `tasks/scheduler.py`：
  - `AsyncIOScheduler`（`ZoneInfo("Asia/Shanghai")`）+ `CronTrigger`，`max_instances=1`、`coalesce=True`；
  - `run_anchor_audit_once()`：Redis `SET NX EX` 抢锁 → 调 `anchor_audit_task` → Lua 比对 token 后释放；
  - `start_scheduler(settings)` / `shutdown_scheduler()`；`scheduler_enabled=false` 时完全不启动；
- `main.py`：用 lifespan 在 API 启动/停止时拉起/关闭调度器；
- 测试：`tests/test_scheduler.py`（锁跳过/释放/cron 配置/开关）、`tests/test_api_docs.py`（OpenAPI 路径逐条出现在 `API.md`）；
- 文档收口：`PROGRESS`（S2 完成、下一步 S3-1）、`API.md`（实际路由对账，缺的补上）、`SCHEMA.md`（`alembic check`）、
  `HANDOFF`、前端 Home 阶段文案（M1 完成）。

**不做**：审计链锚定的真实逻辑（S4-7）；定时清理临时文件（S2-12 只注册锚定任务，清理任务等后续卡）；
多副本的 leader 选举（用每次执行抢锁即可，简单且可解释）。

## 2. 为什么是"每进程都启动 + 每次执行抢锁"

- APScheduler 是**进程内**调度器：`uvicorn --workers N` 或 Docker 多副本时每个进程都会到点触发；
- 分布式锁把"是否真的执行"收敛为一次，比"只让 leader 启动调度器"简单，且 leader 挂了不需要重新选主；
- 锁必须在**任务开始时**抢（而不是进程启动时抢），否则长驻锁会阻止后续所有执行；
- **成功后锁保留到 TTL**，而不是立刻释放：APScheduler 在每个副本独立到点触发，占位任务瞬间完成，
  若立即释放，另一个副本几毫秒后就能拿到锁，同一 tick 会执行两次（实测必现）；
  TTL 必须**小于调度周期**（默认每日 02:00、TTL 1h），下一时段自然过期；
- 任务失败时释放锁，避免把后续手动重试/下一 tick 挡在锁外；释放用 Lua 比对 token，
  防止任务超过 TTL 后旧进程误删新进程的锁。

## 3. 配置（全部进 `core/config.py`，不散落）

| 配置 | 默认 | 说明 |
|---|---|---|
| `scheduler_enabled` | `true` | 测试/脚本显式关；生产保持开 |
| `scheduler_timezone` | `Asia/Shanghai` | cron 按业务时区 |
| `anchor_cron_hour` / `minute` / `second` | `"2"` / `"0"` / `"0"` | 每日 02:00；允许 `*` 便于双进程实测 |
| `anchor_lock_key` | `scheduler:lock:anchor_audit` | 锁 key |
| `anchor_lock_ttl_seconds` | `3600` | 防进程崩溃后死锁 |

## 4. 锁与调度

```
run_anchor_audit_once():
  token = secrets.token_hex(16)
  if not redis.set(key, token, nx=True, ex=ttl):  log "skipped (locked)"; return False
  try: await anchor_audit_task({}); log "executed"; return True   # 不释放，锁留到 TTL
  except: lua("if get(key)==token then del(key) end"); raise       # 失败才释放
```

- `SchedulerLock` 协议 + `RedisSchedulerLock`；测试注入假锁，**不连真实 Redis**。
- 任务当前是 `anchor_audit_task`（S2-7 占位，只日志、不写假审计）；S4-7 替换实现，调度骨架不动。

## 5. 文档对账

- 用 `app.openapi()` 列出实际路径，与 `docs/API.md` 的 `METHOD /path` 逐条比对；
  路径参数名统一归一（`{id}` / `{invoice_id}` → `{}`）后做集合包含检查；
- **新增 `tests/test_api_docs.py`** 把这条对账变成回归测试（以后加路由忘写文档会直接红）；
- `SCHEMA.md` 无新表/列，`alembic check` 零差异即可；`PROGRESS` 记录 S2 收口与 S3-1 下一步；
- 前端 Home 的 M1 标为完成（阶段 2 结束）。

## 6. 需要确认

- **依赖**：安装 `APScheduler==3.11.1`（会带 `tzlocal==5.4.5`；均纯 Python，Python 3.11 可用）。
  它在 AGENTS §2 的指定技术栈里，但当前环境未安装；按 §2.2 需要先确认再改依赖。
- **双进程验收**：用两个 uvicorn（8000/8001）共用同一 Redis，临时把 cron 设成每 10 秒、锁 TTL 设成 5 秒
  （TTL < 周期），观察 40 秒内每个 tick 两个进程只执行一次（总执行次数 ≈ tick 数，而不是两倍）；
  结束后恢复默认 02:00 / TTL 3600。
- **文档对账范围**：只保证"实际已实现路由都在 API.md 里"；API.md 里标注 S3/S4/S5 的未实现接口保持原样。

## 7. 验证

```bash
cd backend
../.venv/Scripts/python.exe -m pytest -p no:warnings
../.venv/Scripts/python.exe -m pytest -m integration -p no:warnings
../.venv/Scripts/python.exe -m alembic check
```

- 双进程：两个 uvicorn + 同一 Redis，`ANCHOR_CRON_SECOND=*/10`、`ANCHOR_LOCK_TTL_SECONDS=5`，
  40s 内执行次数 ≈ tick 数（实测 4 个 tick 共 4 次，另一侧 4 次 skipped）；
- `test_api_docs` 全绿；`smoke.py` 全绿；清理 Redis 锁/session key。

## 8. 收口

- `PROGRESS` 勾 S2-12、阶段 2 完成、下一步 S3-1；
- `DECISIONS` D-050（APScheduler 版本、进程内调度 + Redis 锁、cron 时区、文档对账测试）；
- `HANDOFF` 下一步 S3-1（OCR 全链路，注意 `ocr_jobs.metrics` 等契约缺口）；
- 提交 `feat(s2-12): ...`；**push 由用户执行**。

**实测（2026-11-19）**：`pytest` 341 passed / 集成 146；新增 `test_scheduler.py` 8 条 + `test_api_docs.py` 1 条；
双 uvicorn + 同一 Redis、`*/10`、TTL=5：4 个 tick 共执行 **4** 次、另一侧 **4** 次 `ANCHOR_LOCKED` skipped
（每 tick 两边都触发，但只放行一个）；`alembic check` 零差异；`smoke.py` 9 OK；锁定 key 已清理。
第一次实测（TTL=60、成功即释放）出现同一 tick 双执行，据此把语义改成"成功后锁保留到 TTL"。