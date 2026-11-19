# S2-9 实现计划（plan）

> 对应 spec：`docs/spec/s2-9-fe-login.md` ｜ 契约：`docs/API.md` §1 ｜ 状态：**已实现（2026-11-19）**，计划与实现同步落盘

## Step 1 — 类型与 API

- `web/src/types/auth.ts`：`LoginData/AuthTokens/RefreshData`（字段与 API.md §1 一致）；
- `web/src/api/auth.ts`：`login/refresh/me/logout`，全部走 `http.ts`；
- `stores/user.ts`：加 `refreshToken`、`permissions`、`hasRole()`、`setSession()`、`hydrate()`；localStorage 三个 key。

**验证**：`npm run typecheck`。

## Step 2 — http 刷新单飞

- 拦截器：401（且非 login/refresh、未重试过）→ `refreshAccessToken()`（**模块级同一个 Promise**）→
  更新 session → `http.request(config)` 重放；refresh 失败 → 清会话 + 跳登录 + 单条提示。
- DEV 下导出 `refreshCount` 并挂到 `window.__httpDebug`（生产摇掉）。

**验证**：typecheck；后面用真浏览器验证并发刷新次数。

## Step 3 — 路由与布局

- `router/index.ts`：父路由 `component: MainLayout`，子路由 home/debug/invoices/upload/review/finance/templates/rules/certs/audit；
  `meta.roles` 生效；守卫水合 `/me`；`/403`、404。
- `layout/MainLayout.vue`：侧边栏按 `role` 过滤菜单；顶栏显示姓名/角色标签/退出。
- `views/{Forbidden,Placeholder}.vue`；`App.vue` 只留 `<router-view/>`。

**验证**：`npm run build`；浏览器打开 `/` 未登录跳登录。

## Step 4 — 登录页

- `views/Login.vue`：el-form（用户名 3–64、口令 8–128）+ 登录按钮 loading + 回车提交；
  调 `auth.login` → `store.setSession` → 跳 `redirect`；`40101` 时显示统一文案（http 已弹，避免双弹）。
- 移除 S0-6 的占位 token 逻辑（`main.ts` 的 `?dev_token=` 自动化入口保留，守卫会用 `/me` 水合）。

**验证**：手工/冒烟登录。

## Step 5 — 冒烟脚本与实测

- `scripts/ui_smoke.mjs` 增加 `--fill`（selector + value，触发 input 事件，兼容 Vue v-model）。
- 起后端 8000 与 Vite 5173；建 submitter/reviewer/admin 三个临时用户；
- 冒烟：
  1. `/login` 渲染；
  2. 三角色登录后菜单不同；
  3. admin 打开 `/upload` → `/403`；
  4. 临时把后端 `ACCESS_TOKEN_TTL_SECONDS=1` 重启，登录后等 2s，`__httpDebug` 并发两个 `/auth/me`
     → `refreshCount === 1`、两个请求都成功；
  5. 清理临时用户。
- `npm run typecheck` / `npm run build` 全绿。

## Step 6 — 收口

- `DECISIONS` D-047；`PROGRESS` 勾 S2-9；`AGENTS` 锚点；`HANDOFF` 下一步 S2-10；
- 提交 `feat(s2-9): 登录/主框架/401 单飞刷新`；**不 push**。

## 回滚与降级

| 情况 | 处理 |
|---|---|
| refresh 失败风暴 | 单飞 Promise + 只跳一次登录 + 只提示一次；测试用计数验证 |
| 后端 CORS 未放行 5173 | 检查 `Settings.cors_origins`（默认已含） |
| 角色菜单与后端不一致 | 菜单只是体验；接口 403/404 是最终裁决 |
| 占位页被误解为已完成 | 页面明确写"待 S2-10/S2-11/S5 实现"，不放假数据 |