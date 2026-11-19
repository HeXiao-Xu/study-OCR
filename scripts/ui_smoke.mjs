#!/usr/bin/env node
/**
 * 前端 UI 冒烟脚本（无头 Edge + CDP，零额外依赖）。
 *
 * 为什么存在：`vite build` 只能证明"能编译"，证明不了"页面真的渲染出来、真的打到了后端"。
 * Windows 版 Edge 的 `--dump-dom` 不往 stdout 输出，所以这里用 CDP（Chrome DevTools Protocol）
 * 直连，读**渲染后**的正文与控制台错误。
 *
 * 用法（先起后端 8000 与前端 5173，再起一个带调试端口的 Edge）：
 *   "msedge.exe" --headless=new --remote-debugging-port=9222 --user-data-dir=<临时目录> about:blank &
 *   node scripts/ui_smoke.mjs <url> [--expect 关键字]... [--click 按钮文字] [--eval "<js>"]
 *
 * 例：
 *   node scripts/ui_smoke.mjs http://127.0.0.1:5173/ --expect 登录 --expect S0-6
 *   node scripts/ui_smoke.mjs "http://127.0.0.1:5173/debug?dev_token=smoke" --expect 后端连通性
 *   node scripts/ui_smoke.mjs ".../debug?dev_token=smoke" --click "触发一个 404" --expect 资源不存在
 *   node scripts/ui_smoke.mjs ".../debug?dev_token=smoke" --eval "[...document.querySelectorAll('.el-tag')].map(t=>t.innerText)"
 *
 * 退出码：0 断言全过；1 有失败。
 */

const CDP_PORT = process.env.CDP_PORT || '9222'
const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

// ---------- 参数 ----------
const argv = process.argv.slice(2)
const url = argv[0]
if (!url || url.startsWith('--')) {
  console.error('用法: node scripts/ui_smoke.mjs <url> [--expect k]... [--click text] [--eval js]')
  process.exit(2)
}
const expects = []
const clicks = []
const evals = []
const fills = []
let graceMs = 2500 // 页面导航后先等异步请求落地，再断言（健康检查最长 2s 超时）
let clearStorage = false
for (let i = 1; i < argv.length; i += 1) {
  const flag = argv[i]
  const value = argv[i + 1]
  if (flag === '--clear') {
    clearStorage = true
  } else if (flag === '--expect') {
    if (!value) throw new Error('--expect 需要值')
    expects.push(value)
    i += 1
  } else if (flag === '--fill') {
    const selector = argv[i + 1]
    const fillValue = argv[i + 2]
    if (!selector || fillValue === undefined) throw new Error('--fill 需要 "选择器" 与 "值"')
    fills.push({ selector, value: fillValue })
    i += 2
  } else if (flag === '--click') {
    if (!value) throw new Error('--click 需要值')
    clicks.push(value)
    i += 1
  } else if (flag === '--eval') {
    if (!value) throw new Error('--eval 需要值')
    evals.push(value)
    i += 1
  } else if (flag === '--wait') {
    if (!value) throw new Error('--wait 需要毫秒数')
    graceMs = Number(value)
    i += 1
  }
}

// ---------- CDP 客户端 ----------
async function findTarget() {
  for (let i = 0; i < 40; i += 1) {
    try {
      const list = await (await fetch(`http://127.0.0.1:${CDP_PORT}/json/list`)).json()
      const page = list.find((t) => t.type === 'page')
      if (page?.webSocketDebuggerUrl) return page.webSocketDebuggerUrl
    } catch {
      /* Edge 还没起来 */
    }
    await sleep(250)
  }
  throw new Error(`连不上 CDP（127.0.0.1:${CDP_PORT}）：先启动带 --remote-debugging-port 的 Edge`)
}

let nextId = 1
const pending = new Map()

function send(ws, method, params = {}) {
  const id = nextId++
  ws.send(JSON.stringify({ id, method, params }))
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject })
    setTimeout(() => {
      if (pending.has(id)) {
        pending.delete(id)
        reject(new Error(`CDP 超时: ${method}`))
      }
    }, 20000)
  })
}

const evaluate = async (ws, expression) => {
  const res = await send(ws, 'Runtime.evaluate', {
    expression,
    returnByValue: true,
    awaitPromise: true,
  })
  if (res.exceptionDetails) throw new Error('页面执行报错: ' + res.exceptionDetails.text)
  return res.result?.value
}

async function main() {
  const wsUrl = await findTarget()
  const ws = await new Promise((resolve, reject) => {
    const socket = new WebSocket(wsUrl)
    socket.onopen = () => resolve(socket)
    socket.onerror = () => reject(new Error('WebSocket 连接失败'))
  })

  const consoleLogs = []
  ws.onmessage = (event) => {
    const msg = JSON.parse(event.data)
    if (msg.id && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id)
      pending.delete(msg.id)
      msg.error ? reject(new Error(JSON.stringify(msg.error))) : resolve(msg.result)
      return
    }
    if (msg.method === 'Runtime.consoleAPICalled') {
      const text = (msg.params.args || []).map((a) => a.value ?? a.description ?? '').join(' ')
      consoleLogs.push(`[${msg.params.type}] ${text}`)
    }
    if (msg.method === 'Log.entryAdded') {
      consoleLogs.push(`[${msg.params.entry.level}] ${msg.params.entry.text}`)
    }
  }

  await send(ws, 'Runtime.enable')
  await send(ws, 'Log.enable')
  await send(ws, 'Page.enable')
  await send(ws, 'Page.navigate', { url })

  // 同一浏览器 profile 会保留上一个角色的 localStorage；需要干净会话时先清再重载
  if (clearStorage) {
    await sleep(800)
    await evaluate(ws, 'localStorage.clear(); sessionStorage.clear(); "cleared"')
    await send(ws, 'Page.navigate', { url })
  }

  // 先给一段固定宽限（页面正在等后端响应），再轮询到关键字齐备或超时。
  // 有 --fill/--click 时不做"先等关键字"（登录前正文里还没有菜单），动作完成后再轮询。
  await sleep(graceMs)
  const deadline = Date.now() + 15000
  let bodyText = ''
  if (!fills.length && !clicks.length) {
    while (Date.now() < deadline) {
      bodyText = (await evaluate(ws, 'document.body ? document.body.innerText : ""')) ?? ''
      if (!expects.length) break
      if (expects.every((k) => bodyText.includes(k))) break
      await sleep(400)
    }
  }

  // 表单填充要触发 input 事件（Vue 的 v-model 监听的是 input，不是直接改 value）
for (const { selector, value } of fills) {
  const result = await evaluate(
    ws,
    `(() => {
      const el = document.querySelector(${JSON.stringify(selector)});
      if (!el) return 'NOT_FOUND';
      const proto = el instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
      setter.call(el, ${JSON.stringify(value)});
      el.dispatchEvent(new Event('input', { bubbles: true }));
      return 'FILLED';
    })()`,
  )
  if (result !== 'FILLED') throw new Error(`没找到输入框：${selector}`)
  console.log(`已填写：${selector}`)
}
if (fills.length) await sleep(300)

for (const text of clicks) {
    const result = await evaluate(
      ws,
      `(() => {
        const btn = [...document.querySelectorAll('button')].find((b) => b.innerText.includes(${JSON.stringify(text)}));
        if (!btn) return 'NOT_FOUND';
        btn.click();
        return 'CLICKED';
      })()`,
    )
    if (result !== 'CLICKED') throw new Error(`没找到按钮：${text}`)
    console.log(`已点击按钮：${text}`)
    await sleep(2000)
    bodyText = (await evaluate(ws, 'document.body ? document.body.innerText : ""')) ?? ''
  }

  // 动作（填表/点击）后再轮询关键字，给登录跳转与异步请求留时间
  if (fills.length || clicks.length) {
    while (Date.now() < deadline) {
      bodyText = (await evaluate(ws, 'document.body ? document.body.innerText : ""')) ?? ''
      if (!expects.length || expects.every((k) => bodyText.includes(k))) break
      await sleep(400)
    }
  }

  const location = await evaluate(ws, 'location.pathname + location.search')
  console.log('URL      :', location)
  console.log('正文长度 :', bodyText.length)
  console.log('--- 正文（前 800 字）---')
  console.log(bodyText.slice(0, 800))

  for (const expression of evals) {
    const value = await evaluate(ws, expression)
    console.log(`--- eval: ${expression.slice(0, 60)} ---`)
    console.log(typeof value === 'string' ? value : JSON.stringify(value, null, 2))
  }

  const consoleErrors = consoleLogs.filter((l) => l.startsWith('[error]') || l.startsWith('[severe]'))
  console.log('--- 控制台 error 级日志 ---')
  console.log(consoleLogs.length ? consoleLogs.join('\n') : '(无)')

  const missing = expects.filter((k) => !bodyText.includes(k))
  const corsIssue = consoleLogs.some((l) => /CORS|Access-Control-Allow/i.test(l))
  const failed = missing.length > 0 || corsIssue

  if (missing.length) console.error('\n❌ 正文缺少关键字:', missing.join(' / '))
  if (corsIssue) console.error('❌ 控制台出现 CORS 相关报错')
  if (consoleErrors.length) console.warn('\n⚠️ 控制台有 error 级日志（可能只是 favicon，请人工确认）：')
  if (!failed) console.log('\n✅ 断言通过:', expects.length ? expects.join(' / ') : '(仅渲染检查)')

  process.exit(failed ? 1 : 0)
}

main().catch((error) => {
  console.error('❌', error.message)
  process.exit(1)
})