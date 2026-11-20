# 前端联调测试流程（人工走查清单）

> 不是契约文档，只是**测试脚本**：按顺序走一遍即可覆盖阶段 1–3 的用户可见功能。
> 服务：API `:8000`、ARQ worker、Vite `:5173`、PostgreSQL/Redis/MinIO（docker）。
> 数据：`test_sub` 名下的 5 张票据（111/112/113/114/117）；117 是真实 PDF（D-069 修复后：金额 1057.43、税额 10.57、合计 1068.00）。

## 0. 账号

| 用户名 | 口令 | 角色 | 登录后落地 |
|---|---|---|---|
| `test_sub` | `Test_sub!2026` | 提交人 | `/invoices`（我的票据） |
| `test_rev` | `Test_rev!2026` | 审核人 | `/review`（待核对） |
| `test_admin` | `Test_admin!2026` | 管理员 | `/templates`（票种模板） |

## 1. 登录与角色落地

| # | 操作 | 预期 |
|---|---|---|
| A1 | `test_sub` 登录 | 落到 `/invoices`，标题"我的票据"，只见自己的 5 张票（111/112/113/114/117） |
| A2 | `test_rev` 登录 | 落到 `/review`（`extract_status=need_confirm` 的列表），能看到全部提交人的待核对票 |
| A3 | `test_admin` 登录 | 落到 `/templates`，侧栏只有模板/证书/审计（后两个是占位页） |
| A4 | 错误口令登录 | 统一"用户名或密码错误"，不泄露账号是否存在 |
| A5 | 已登录 `test_sub` 直接访问 `/templates` | 跳到 `/403`（角色无权限） |

## 2. 票据列表

| # | 操作 | 预期 |
|---|---|---|
| B1 | `test_sub` 打开"我的票据" | 5 行：111/112/113/114/117；117 是真实 PDF（数电票），识别结果金额 1057.43 / 税额 10.57 / 合计 1068.00，税号显示 `91130730MAK5MAN60W` |
| B2 | 票种筛选 `vat_general` | 只剩 111 与 114（114 是 111 的重复影像变体） |
| B3 | 抽取状态筛选 `need_confirm` | 4 行都在（都待确认） |
| B4 | 搜索票号 `05975405` | 命中 111 与 114 |
| B5 | 刷新页面 | 筛选条件保留（`route.query` 是唯一状态源） |
| B6 | `test_rev` 打开"待核对" | 能看到 `test_sub` 的票（reviewer 读全量），列表与筛选一致 |

## 3. 上传（`test_sub`）

| # | 操作 | 预期 |
|---|---|---|
| C1 | 上传页选 1 张 JPG | "上传中"→"识别中… N%"→"识别完成"；完成后出现"查看详情" |
| C2 | 一次选 3 张不同图 | 走批量接口：总进度条 + 每项状态"排队识别中…"→"识别完成"；服务端最多同时 3 个 |
| C3 | 把已传过的图再传一次 | 绿色"已上传"标签 + "查看已上传票据"链接（409 是正常业务） |
| C4 | 选一个 `.txt` | 本地直接"文件不合规"，不发请求 |
| C5 | 一次选 30 张（可选） | 全部到 100%；期间刷新页面不会丢任务（服务端排队）；约 1 分钟 |

## 4. 详情：左图右表 + 框选高亮（打开 `/invoices/111`）

| # | 操作 | 预期 |
|---|---|---|
| D1 | 打开 111 | 左侧票据影像、右侧 12 个字段；上方"票据详情"卡片显示票种/金额/日期/状态；右上角有"返回列表"，点击回到列表并保留筛选 |
| D2 | 鼠标移到表格 `amount` 行 | 图上 `amount` 框高亮（红）并加粗；移出恢复 |
| D3 | 点击图上 `amount` 框 | 右侧 `amount` 行滚到视口中间并闪烁一次（黄底） |
| D4 | 点击"放大"两次 | 比例 100%→120%→144%；框仍与文字对齐；滚轮缩放以光标为中心；拖拽可平移 |
| D5 | 观察行底色 | `invoice_no / tax_amount / total_amount / total_amount_upper` 黄底；`amount` 红底并显示"冲突候选：… 未自动替换" |
| D6 | 观察行框 | 灰蓝色细线画出文字行；高亮字段的所属行变蓝加粗 |
| D7 | 打开 `/invoices/113`（出租车票） | 字段少（5 个），低置信更多；红/黄底与画框一致 |
| D8 | 打开 `/invoices/114`（重复票） | 顶部红色"疑似重复票据（同票号/同影像）"横幅，含 111 的"查看"链接；"确认整单"按钮被隐藏 |
| D10 | 打开 `/invoices/113` | 已复核态：右上角"已复核"标签、无编辑/复核按钮（S3-9 冒烟已复核） |
| D9 | 打开旧票（列表里另一张） | 空字段表显示空态；影像可看（本地明文） |

## 5. 编辑字段（草稿态）

| # | 操作 | 预期 |
|---|---|---|
| E1 | `test_sub` 点"编辑字段"，改 `buyer_name` → 保存 | 成功提示，`version +1`，字段来源变人工 |
| E2 | `test_sub` 编辑金额字段（不勾选） | 提示必须先勾选“确认修改金额”；勾选后可保存（S3-9 起提交人也能改金额） |
| E3 | `test_rev` 编辑 `amount` 但不勾选 | 同样提示必须先勾选；勾选后保存成功 |
| E4 | 开两个标签页同时编辑同一字段并先后保存 | 后保存者收到 409"版本冲突"并自动刷新，不覆盖前者 |

## 6. 整单确认

| # | 操作 | 预期 |
|---|---|---|
| F1 | 111 点"确认整单" | 弹二次确认；确认后返回 422，字段卡上方红色列表：`amount`（冲突）、`invoice_no/tax_amount/total_amount/total_amount_upper`（低置信未确认） |
| F2 | 对低置信非金额字段点行内"确认" | 该行状态变"已确认"，红/黄标记消失，`version +1` |
| F3 | 金额字段由 `test_sub` 自己改值（勾选确认金额）并逐字段确认 | 再点"确认整单" → 成功，状态变 **"待复核"** |
| F4 | `test_rev` 打开该票据 → 点"复核通过" | 弹二次确认；确认后状态变 **"已复核"**，复核时间落库；列表 `review_status=reviewed` 可见 |
| F5 | 复核后 `test_sub`/`test_rev` 再改字段 | 409"已复核，不可修改"；再次复核 → 200 幂等、版本不变 |
| F6 | 对 114 点"确认整单" | 按钮已隐藏；若直接调接口应 **409/40900** + 已有 `invoice_id=111`（强重复） |
| F7 | 已确认/已复核票再确认（接口/重复点击） | 幂等：200，版本不再增加 |

## 7. 局部重识别

| # | 操作 | 预期 |
|---|---|---|
| G1 | 低置信字段点"重识别" | 提示"已提交重识别"；2s 轮询刷新后置信度变化、来源显示"已重识别"（`ocr_retry`） |
| G2 | 已重识别/已人工改过的字段 | 不再显示"重识别"按钮 |

## 8. 权限边界

| # | 操作 | 预期 |
|---|---|---|
| H1 | `test_rev` 打开上传页 `/upload` | 403（上传仅提交人） |
| H2 | `test_rev` 详情页 | 不显示"确认整单"（仅本人可确认）；可编辑字段（金额需勾选） |
| H3 | `test_admin` 打开 `/invoices/111` | 404"票据不存在或无权访问"（admin 不进业务表） |
| H4 | `test_admin` 打开 `/templates` | 5 个模板；详情抽屉、字段规格 JSON、规则编辑、试跑；**没有"新建模板"入口** |

## 9. 三态与异常

| # | 操作 | 预期 |
|---|---|---|
| J1 | 直接访问 `/invoices/999999` | 结果页"票据不存在或无权访问" + 回列表按钮 |
| J2 | 未登录直接访问 `/invoices` | 跳登录页；登录后回到目标页 |
| J3 | 停掉 API 再点刷新（可选） | 错误提示，不白屏；表单一/列表保留旧数据 |

## 10. 自动化辅助（可选）

```bash
# 登录 + 列表断言
node scripts/ui_smoke.mjs "http://127.0.0.1:5173/login" --clear --view 1440x900 \
  --fill 'input[autocomplete="username"]' test_sub \
  --fill 'input[autocomplete="current-password"]' 'Test_sub!2026' \
  --click 登录 --expect 我的票据
# 详情页重复横幅 + 截图（114）
node scripts/ui_smoke.mjs "http://127.0.0.1:5173/invoices/114" --view 1440x900 \
  --expect "疑似重复票据" --shot /tmp/ui-114.png
# 后端回归
cd backend && ../.venv/Scripts/python.exe -m pytest -q
```

## 11. 已知限制（别当 bug 提）

- **影像明文仅本地**：`GET /image` 只在 `STORAGE_ALLOW_PLAINTEXT=true` 且未加密时返回；生产/密文会 409"待 S4"。
- **114 是我构造的重复样本**（同票面重新编码、不同 sha），用于验证 `duplicate_of` 与红色横幅；不是真实重复上传。
- **强重复的 409 只在 confirm 返回**；上传同图是 409/40901（影像去重），两者语义不同。
- **弱重复不阻断**：只在详情页黄条提示；当前种子数据没有 weak 对（可造：同金额+日期+销方、不同票号）。
- 批量页 30 文件的浏览器点击未自动化（接口层有 30 文件验收）。
- 同一影像**跨账号**上传会被全库 sha256 去重拦成 409，且不返回对方 `invoice_id`（提示"已被其他账号上传"）；同账号重复上传才给详情链接。
- reviewer 不能确认整单（契约"本人"），审核流在 S5。

## 12. 测试后清理（可选，不清理下次还能用）

```bash
cd backend
../.venv/Scripts/python.exe - <<'PY'
import sys
sys.path.insert(0,'.'); sys.path.insert(0,'../scripts')
from sqlalchemy import text
from app.core.database import get_session_factory
from ocr_acceptance import cleanup

db = get_session_factory()()
ids = [r[0] for r in db.execute(text(
    "select id from invoices where uploader_id=(select id from users where username='test_sub')")).all()]
db.close()
if ids:
    cleanup(ids)
    print("removed invoices:", len(ids))

db = get_session_factory()()
db.execute(text("delete from users where username in ('test_sub','test_rev','test_admin')"))
db.commit()
print("removed test accounts")
db.close()
PY
```