# S2-12 实现计划（plan）

> 对应 spec：`docs/spec/s2-12-scheduler.md` ｜ 契约：`API.md` / `SCHEMA.md` ｜ 状态：**已实现（2026-11-19）**，计划与实现同步落盘

## Step 1 — 依赖与配置

- `pip install "APScheduler==3.11.1"`；`requirements.txt` 加 `APScheduler==3.11.1`、`tzlocal==5.4.5`；
  重新生成 lock（CRLF→LF）。
- `core/config.py`：`scheduler_enabled`、`scheduler_timezone`、`anchor_cron_{hour,minute,second}`、
  `anchor_lock_key`、`anchor_lock_ttl_seconds`；`.env.example` 同步。
- `tests/conftest.py` + 各测试的 `Settings(...)`：显式 `scheduler_enabled=False`，避免测试拉起调度器。

**验证**：`python -c "import apscheduler; print(apscheduler.__version__)"`；`pytest -q` 不回归。

## Step 2 — scheduler 模块

- `tasks/scheduler.py`：`SchedulerLock` 协议、`RedisSchedulerLock`（Lua compare-delete）、
  `run_anchor_audit_once`、`build_scheduler`、`start_scheduler`、`shutdown_scheduler`；
- `main.py`：lifespan 启动/关闭（`scheduler_enabled` 控制）；测试 app 不启动。

**验证**：`tests/test_scheduler.py`（假锁：抢不到跳过；抢到执行并释放；cron 参数；禁用时 start 返回 None）。

## Step 3 — 文档对账

- `tests/test_api_docs.py`：`app.openapi()["paths"]` 与 `docs/API.md` 归一后逐条包含；
- 按测试结果补 `API.md` 缺漏（若有）；`alembic check`；`PROGRESS`/`HANDOFF`/`Home` 阶段文案。

**验证**：`pytest -q tests/test_api_docs.py`；`alembic check`。

## Step 4 — 双进程实测

- 起两个 uvicorn（8000/8001）共用 Redis，`SCHEDULER_ENABLED=true`、`ANCHOR_CRON_HOUR=*`、
  `ANCHOR_CRON_MINUTE=*`、`ANCHOR_CRON_SECOND=*/10`；
- 等 30s，统计两边日志中"锚定任务执行"次数 ≈3（不是 6）；停止进程，清理 Redis 锁。

**验证**：日志计数；`smoke.py`。

## Step 5 — 全量回归与收口

```bash
cd backend
../.venv/Scripts/python.exe -m pytest
../.venv/Scripts/python.exe -m pytest -m integration
../.venv/Scripts/python.exe -m alembic check
(cd ../web && npm run typecheck && npm run build)
```

- `DECISIONS` D-050；`PROGRESS` 勾 S2-12、阶段 2 完成；`AGENTS` 锚点；`HANDOFF` 下一步 S3-1；
- 提交 `feat(s2-12): 定时任务骨架与阶段收口`；**不 push**。

## 回滚与降级

| 情况 | 处理 |
|---|---|
| APScheduler 不可用 | 关 `SCHEDULER_ENABLED`，外部 cron 调同一函数；业务不受影响 |
| 锁 TTL 到期任务仍在跑 | 成功锁保留至 TTL（时段去重标记），失败才释放；TTL 必须小于调度周期（默认 1h < 每日）；任务本身需按天幂等（S4-7） |
| 多 worker 重复触发 | 每次执行抢锁，只有一个执行；其余记 "skipped" 日志 |
| 文档对账误报 | 路径参数名归一后比较；文档-only 的未实现接口不参与"实际已实现"集合 |