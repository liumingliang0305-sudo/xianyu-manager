// 登录同步插件的后台：闲鱼的登录 cookie 变了（登录、退出、网页端续期）就推给本机的管理后台，
// 另外每 10 分钟看一次，补上后台没开时漏掉的。推之前比一下登录指纹，没变就不推。

import {
  DEFAULT_SERVER,
  LOGIN_COOKIES,
  cookieHeader,
  flattenCookies,
  isFinalFailure,
  isGoofishDomain,
  isLoggedIn,
  loginKey,
} from "./lib.js"

const ALARM = "sync"
const ALARM_MINUTES = 10
// 登录和续期时一串 cookie 接连变化，等它们都落定再推
const DEBOUNCE_MS = 3000

let timer = null

async function setStatus(status) {
  await chrome.storage.local.set({ status: { ...status, at: Date.now() } })
  await chrome.action.setBadgeText({ text: status.ok ? "" : "!" })
  await chrome.action.setBadgeBackgroundColor({ color: "#d93025" })
}

/** 推一次。force 时不管登录指纹变没变（弹窗里的"立即同步"） */
async function sync({ force = false } = {}) {
  const cookies = flattenCookies(await chrome.cookies.getAll({ domain: "goofish.com" }))
  if (!isLoggedIn(cookies)) {
    await setStatus({ ok: false, message: "浏览器里没有登录闲鱼，先在 goofish.com 登录" })
    return
  }
  const key = loginKey(cookies)
  const { lastKey, server = DEFAULT_SERVER } = await chrome.storage.local.get(["lastKey", "server"])
  if (!force && key === lastKey) return

  let resp
  try {
    resp = await fetch(`${server}/api/login/import`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ cookie: cookieHeader(cookies) }),
    })
  } catch {
    // 不记 lastKey，定时任务会再试
    await setStatus({ ok: false, message: `连不上管理后台（${server}），确认 uv run xym serve 在运行` })
    return
  }
  const body = await resp.json().catch(() => null)
  if (resp.ok) {
    await chrome.storage.local.set({ lastKey: key })
    await setStatus({
      ok: true,
      changed: body.changed,
      account: body.account_id,
      message: body.message,
    })
    return
  }
  if (isFinalFailure(resp.status)) await chrome.storage.local.set({ lastKey: key })
  const message =
    resp.status === 401
      ? "浏览器里的闲鱼登录已经失效，打开 goofish.com 刷新一下（网页会自动续期），续上以后会再同步"
      : (body?.message ?? `管理后台返回 HTTP ${resp.status}`)
  await setStatus({ ok: false, message })
}

chrome.cookies.onChanged.addListener(({ cookie }) => {
  if (!isGoofishDomain(cookie.domain) || !LOGIN_COOKIES.includes(cookie.name)) return
  clearTimeout(timer)
  timer = setTimeout(() => void sync(), DEBOUNCE_MS)
})

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM) void sync()
})

chrome.runtime.onMessage.addListener((message, _sender, reply) => {
  if (message?.type !== "sync") return false
  sync({ force: true }).finally(() => reply(true))
  return true // 异步回复
})

chrome.runtime.onInstalled.addListener(() => void sync({ force: true }))
chrome.runtime.onStartup.addListener(() => void sync())

// 浏览器重启以后定时任务可能没了，每次后台启动时补上
void chrome.alarms.get(ALARM).then((alarm) => {
  if (!alarm) void chrome.alarms.create(ALARM, { periodInMinutes: ALARM_MINUTES })
})
