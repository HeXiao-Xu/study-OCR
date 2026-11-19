# S2-9 前端登录与主框架 — 设计（spec）

> 任务卡：S2-9 ｜ 契约：`docs/API.md` §0（权限矩阵/统一壳）§1（login/refresh/me/logout）｜ 上游：S0-6 前端骨架、S2-2 认证接口
> 状态：**已实现（2026-11-19）**；实测见 §5。

## 1. 目标

三角色登录后进入**按角色渲染的主框架**；access token 过期时自动 refresh 一次（并发去重），
失败才回登录页；无权限路由跳 `/403`。前端只经 `src/api/` 调后端。

**做**：
- `api/auth.ts` + `types/auth.ts`：login / refresh / me / logout（统一壳 + `unwrap`）；
- `stores/user.ts`：`token/refreshToken/user` 持久化到 localStorage；`role`、`permissions`、`hasRole()`、`setSession()`、`hydrate()`、`clear()`；
- `api/http.ts`：401 自动 refresh **一次**，并发请求复用同一个 `Promise`；refresh 失败清会话跳登录；登录/刷新接口本身不触发刷新；
- `layout/MainLayout.vue`：侧边栏按角色出菜单 + 顶栏用户/角色/退出；
- `views/Login.vue`：Element Plus 表单 + 前端校验 + 真实登录；
- `views/Forbidden.vue`（403）、`views/Placeholder.vue`（尚未实现的业务页占位）；
- `router/index.ts`：`meta.roles` 生效 + 角色菜单路由 + 守卫（未登录→登录、已登录无 user→`/me` 水合、无权限→`/403`）；
- `App.vue` 简化为 `<router-view/>`，布局由路由的父级 layout 承担；
- `scripts/ui_smoke.mjs` 增加 `--fill`（登录表单要能自动化填值），并支持真实登录冒烟。

**不做**：上传页/列表/详情/财务页（S2-10/S2-11/S5）；后端权限（已有，前端隐藏菜单不等于授权）；
不改后端接口；不引入新前端依赖（不装图标库，菜单用文字）。

## 2. 角色与菜单（API.md §0）

| 角色 | 菜单 |
|---|---|
| `submitter` | 我的票据 `/invoices`、上传票据 `/upload` |
| `reviewer` | 待核对 `/review`、待审核 `/finance/pending` |
| `admin` | 模板 `/templates`、规则 `/rules`、证书 `/certs`、审计 `/audit` |
| 所有角色 | 概览 `/`、联调自检 `/debug`（保留 S0-6 联调入口） |

未实现的业务页统一渲染 `Placeholder.vue`（显示"该页面将在 S2-10/S2-11/S5 实现"），
**不做假数据、不伪造功能**；菜单先按角色出齐，路由也先占位。

## 3. 认证与会话

- **存储**：`study_ocr_token`（access）、`study_ocr_refresh`、`study_ocr_user` 全部 localStorage（D-021 的决定，XSS 风险由"只走 Vue 插值、禁止 v-html"约束）。
- **登录**：`POST /auth/login` → `{access_token, refresh_token, expires_in, refresh_expires_in, user}` → 存 session → 跳 `redirect` 或 `/`。
- **水合**：刷新浏览器后只有 token 没有 user 时，守卫调 `GET /auth/me`；失败（401）由 http 的 refresh 逻辑处理，refresh 也失败才清会话跳登录。
- **刷新**：401/40101 且请求不是 `/auth/login`、`/auth/refresh` → 标记 `_retried` → 复用同一个 refresh Promise →
  成功后更新 token 并重放原请求；失败 → 清会话 + 跳登录 + 一条提示（并发 401 不重复弹窗）。
- **登出**：`POST /auth/logout`（带 access + refresh），无论成败都清本地并回登录页。
- **过期检测**：不解析 JWT 过期时间，完全依赖 401；避免前后端时钟偏差。

## 4. 路由守卫

1. `meta.public`（登录/404）直接放行；
2. 未登录访问受保护页 → `/login?redirect=...`；
3. 已登录但 `user` 为空 → `await store.hydrate()`（调 `/auth/me`）；失败由 http 处理；
4. `meta.roles` 非空且角色不在其中 → `/403`（不暴露页面存在性，前端层面仅体验）；
5. 已登录访问 `/login` → `/`。

## 5. 验证方式

- `npm run typecheck`、`npm run build`；
- 起后端（8000）+ Vite（5173），`scripts/ui_smoke.mjs`：
  - `/login` 渲染登录表单；
  - 三个用户分别 `--fill 用户名/口令 --click 登录`，断言菜单各不同（我的票据 / 待核对 / 模板）；
  - admin 打开 submitter-only 的 `/upload` → 断言 `/403`；
  - **刷新去重**：把后端 `ACCESS_TOKEN_TTL_SECONDS=1` 起临时实例，登录后等 2s，
    用（仅 DEV 暴露的）`window.__httpDebug` 并发发两个 `/auth/me`，断言 `refreshCount === 1` 且两个请求都成功；
  - 冒烟结束清理临时用户。
- 控制台无 CORS 报错。

**实测（2026-11-19）**：`npm run typecheck` / `npm run build` 通过；后端 `ACCESS_TOKEN_TTL_SECONDS=1` +
Vite + 无头 Edge：submitter 登录后菜单含「我的票据」、reviewer 含「待核对/待审核」、admin 含「票种模板/审计日志」；
admin 访问 `/upload` → `/403`；登录后等 2s 用 `__httpDebug` 并发两个 `/auth/me` →
**refreshCount=1**、两个请求都返回 `fe_sub`；`scripts/smoke.py` **9 OK / 0 FAIL**（前端 dev server 在跑）；
测试用户与 Redis session key 已清理。

## 6. 需要知道的取舍

1. **DEV-only 诊断钩子**：为验证"并发 401 只刷新一次"，在 `import.meta.env.DEV` 下把
   `{ get, refreshCount }` 挂到 `window.__httpDebug`；生产构建分支被摇掉。属于测试缝，不影响业务。
2. **未实现页面用占位**：S2-10/S2-11/S5 的页面还没做，菜单/路由先占位并明确标注，
   避免"看起来能点、点开是假数据"。

## 7. 收口

- `PROGRESS` 勾 S2-9 + 实测；`DECISIONS` D-047（refresh 单飞 + 守卫水合 + DEV 诊断钩子）；
- `AGENTS` §10.8 前端锚点补充；
- `HANDOFF` 下一步 S2-10；
- 提交 `feat(s2-9): ...`；**push 由用户执行**。