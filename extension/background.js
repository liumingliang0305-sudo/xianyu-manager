// 插件的后台，做两件事，都带上插件令牌（管理后台用户菜单里生成），推到令牌的主人名下：
// 1. 登录同步：闲鱼的登录 cookie 变了（登录、退出、网页端续期）就推给管理后台，
//    另外每 10 分钟看一次，补上后台没开时漏掉的。推之前比一下登录指纹，没变就不推。
// 2. 导入拼多多商品：弹窗在拼多多商品页上读出商品数据（pdd.js）交给这里，发到管理后台。
//    放在这里发是因为后台要下载图片、要等几秒，弹窗可能先关掉了。

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

async function setPddStatus(status) {
  await chrome.storage.local.set({ pddStatus: { ...status, at: Date.now() } })
}

/** 把弹窗读出来的拼多多商品（{ goods, mall }）发给管理后台，结果记在 pddStatus 里给弹窗看 */
async function importPdd(data) {
  const { server = DEFAULT_SERVER, token } = await chrome.storage.local.get(["server", "token"])
  const title = data?.goods?.goodsName ?? ""
  if (!token) {
    await setPddStatus({
      ok: false,
      title,
      message: "还没填插件令牌：在管理后台左下角的用户菜单里生成，填到下面",
    })
    return
  }
  if (!isLocalServer(server) && !(await chrome.permissions.contains(serverPermission(server)))) {
    await setPddStatus({
      ok: false,
      title,
      message: `还没允许插件访问 ${server}：再点一次「导入这个商品」，在弹出的提示里点允许`,
    })
    return
  }
  await setPddStatus({ pending: true, title, message: "管理后台正在下载图片，要几秒钟…" })
  let resp
  try {
    resp = await fetch(`${server}/api/pdd/goods`, {
      method: "POST",
      headers: { "content-type": "application/json", authorization: `Bearer ${token}` },
      body: JSON.stringify(data),
    })
  } catch {
    const hint = isLocalServer(server) ? "确认 uv run xym serve 在运行" : "确认服务器开着、地址没填错"
    await setPddStatus({ ok: false, title, message: `连不上管理后台（${server}），${hint}` })
    return
  }
  const body = await resp.json().catch(() => null)
  if (!resp.ok) {
    let message = body?.message ?? `管理后台返回 HTTP ${resp.status}`
    if (body?.code === "unauthenticated") {
      message = "插件令牌不对或者已经作废，到管理后台用户菜单里重新生成，填到下面"
    }
    await setPddStatus({ ok: false, title, message })
    return
  }
  const goods = body.goods
  const missing = body.total - body.saved
  await setPddStatus({
    ok: true,
    added: body.added,
    title: goods.title,
    goodsId: goods.goods_id,
    message:
      `主图 ${goods.image_count} 张、详情图 ${goods.detail_count} 张、规格 ${goods.sku_count} 个` +
      (missing > 0 ? `；${missing} 张图没下载下来，可以到管理后台重新下载` : ""),
  })
}

chrome.runtime.onMessage.addListener((message, _sender, reply) => {
  if (message?.type === "sync") {
    sync({ force: true }).finally(() => reply(true))
    return true // 异步回复
  }
  if (message?.type === "import-pdd") {
    importPdd(message.data).finally(() => reply(true))
    return true
  }
  return false
})

chrome.runtime.onInstalled.addListener(() => void sync({ force: true }))
chrome.runtime.onStartup.addListener(() => void sync())

// 浏览器重启以后定时任务可能没了，每次后台启动时补上
void chrome.alarms.get(ALARM).then((alarm) => {
  if (!alarm) void chrome.alarms.create(ALARM, { periodInMinutes: ALARM_MINUTES })
})
