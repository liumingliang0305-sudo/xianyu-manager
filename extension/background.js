// 登录同步插件的后台：闲鱼的登录 cookie 变了（登录、退出、网页端续期）就推给管理后台，
// 另外每 10 分钟看一次，补上后台没开时漏掉的。推之前比一下登录指纹，没变就不推。
// 推的时候带上插件令牌（管理后台用户菜单里生成），推过来的账号归到令牌的主人名下。

import {
  DEFAULT_SERVER,
  LOGIN_COOKIES,
  cookieHeader,
  flattenCookies,
  isFinalFailure,
  isGoofishDomain,
  isLocalServer,
  isLoggedIn,
  loginKey,
  serverPermission,
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
  const {
    lastKey,
    server = DEFAULT_SERVER,
    token,
  } = await chrome.storage.local.get(["lastKey", "server", "token"])
  if (!token) {
    await setStatus({
      ok: false,
      message: "还没填插件令牌：在管理后台左下角的用户菜单里生成，填到这里",
    })
    return
  }
  if (!force && key === lastKey) return
  if (!isLocalServer(server) && !(await chrome.permissions.contains(serverPermission(server)))) {
    await setStatus({
      ok: false,
      message: `还没允许插件访问 ${server}：点「立即同步」，在弹出的提示里点允许`,
    })
    return
  }

  let resp
  try {
    resp = await fetch(`${server}/api/login/import`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
      body: JSON.stringify({ cookie: cookieHeader(cookies) }),
    })
  } catch {
    // 不记 lastKey，定时任务会再试
    const hint = isLocalServer(server) ? "确认 uv run xym serve 在运行" : "确认服务器开着、地址没填错"
    await setStatus({ ok: false, message: `连不上管理后台（${server}），${hint}` })
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
  let message = body?.message ?? `管理后台返回 HTTP ${resp.status}`
  if (body?.code === "unauthenticated") {
    message = "插件令牌不对或者已经作废，到管理后台用户菜单里重新生成，填到这里"
  } else if (resp.status === 401) {
    message = "浏览器里的闲鱼登录已经失效，打开 goofish.com 刷新一下（网页会自动续期），续上以后会再同步"
  }
  await setStatus({ ok: false, message })
}

chrome.cookies.onChanged.addListener(({ cookie }) => {
  if (!isGoofishDomain(cookie.domain) || !LOGIN_COOKIES.includes(cookie.name)) return
  clearTimeout(timer)
  timer = setTimeout(() => void sync(), DEBOUNCE_MS)
})

// 允许访问远程后台以后马上推一次（弹窗可能在授权提示出来时已经关了）
chrome.permissions.onAdded.addListener(() => void sync({ force: true }))

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
