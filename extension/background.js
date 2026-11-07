// 插件的后台，做三件事：
// 1. 登录同步：闲鱼的登录 cookie 变了（登录、退出、网页端续期）就推给管理后台，
//    另外每 10 分钟看一次，补上后台没开时漏掉的。推之前比一下登录指纹，没变就不推。
// 2. 导入拼多多商品：弹窗在拼多多商品页上读出商品数据（pdd.js）交给这里，发到管理后台。
//    放在这里发是因为后台要下载图片、要等几秒，弹窗可能先关掉了。
//    1、2 都带上插件令牌（管理后台用户菜单里生成），推到令牌的主人名下。
// 3. 拿闲鱼商品详情：管理后台页面（搜索暂存）通过 bridge.js 要一件商品的详情，这里在后台标签页里打开
//    这件商品的闲鱼页面，用页面自己的请求库拿（goofish.js），关掉标签页，结果交回页面，页面自己存。
//    一次只开一个，两件之间停一会儿；闲鱼要验证（滑块）时把那个标签页留着、切过去，让人过一下。

import { fetchGoofishItemDetail } from "./goofish.js"
import {
  DEFAULT_SERVER,
  LOGIN_COOKIES,
  bridgeMatches,
  cookieHeader,
  flattenCookies,
  goofishItemUrl,
  isFinalFailure,
  isGoofishDomain,
  isItemId,
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

// 允许访问远程后台以后马上推一次（弹窗可能在授权提示出来时已经关了），传话脚本也注入上
chrome.permissions.onAdded.addListener(() => {
  void sync({ force: true })
  void registerBridge()
})

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

// ---- 拿闲鱼商品详情 ----

const BRIDGE_ID = "admin-bridge"
// 商品页 30 秒还没打开就算了
const LOAD_TIMEOUT_MS = 30_000
// 两件之间停一会儿，像人一件一件点开看
const DETAIL_GAP_MS = 1500
// 碰到要验证以后这么久之内不再开新页面，排着的直接告诉页面，免得一下子开出一排要过滑块的标签页
const VALIDATE_PAUSE_MS = 60_000

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

/** 在插件里填的管理后台上注入传话脚本；远程后台还没允许访问时不注入 */
async function registerBridge() {
  const { server = DEFAULT_SERVER } = await chrome.storage.local.get("server")
  await chrome.scripting.unregisterContentScripts({ ids: [BRIDGE_ID] }).catch(() => {})
  if (!isLocalServer(server) && !(await chrome.permissions.contains(serverPermission(server)))) return
  await chrome.scripting
    .registerContentScripts([
      { id: BRIDGE_ID, matches: bridgeMatches(server), js: ["bridge.js"], runAt: "document_start" },
    ])
    .catch((error) => console.warn("注入管理后台传话脚本失败", error))
}

/** 等标签页加载完（status 是 complete） */
function waitForLoad(tabId) {
  return new Promise((resolve, reject) => {
    const finish = (error) => {
      clearTimeout(timer)
      chrome.tabs.onUpdated.removeListener(listener)
      if (error) reject(error)
      else resolve()
    }
    const listener = (id, info) => {
      if (id === tabId && info.status === "complete") finish()
    }
    const timer = setTimeout(() => finish(new Error("闲鱼商品页 30 秒还没打开")), LOAD_TIMEOUT_MS)
    chrome.tabs.onUpdated.addListener(listener)
    chrome.tabs
      .get(tabId)
      .then((tab) => tab.status === "complete" && finish())
      .catch((error) => finish(error))
  })
}

let validateUntil = 0

/** 开一个后台标签页打开商品页，在页面里拿详情，拿完关掉。要验证时标签页留着、切过去 */
async function fetchItemDetail(itemId) {
  if (!isItemId(itemId)) return { error: "invalid", message: "商品 ID 不对" }
  if (Date.now() < validateUntil) {
    return {
      error: "validate",
      message: "闲鱼刚要求验证：先在 Chrome 里那个闲鱼页面上过一下滑块，过一分钟再点「拉详情」",
    }
  }
  let tab = null
  let keep = false
  try {
    tab = await chrome.tabs.create({ url: goofishItemUrl(itemId), active: false })
    await waitForLoad(tab.id)
    const [injection] = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      world: "MAIN",
      func: fetchGoofishItemDetail,
      args: [itemId],
    })
    const result = injection?.result ?? { error: "failed", message: "闲鱼页面上没跑起来，再试一次" }
    if (result.error === "validate") {
      keep = true
      validateUntil = Date.now() + VALIDATE_PAUSE_MS
      await chrome.tabs.update(tab.id, { active: true }).catch(() => {})
    }
    return result
  } catch (error) {
    return { error: "failed", message: `打不开闲鱼商品页：${error.message}` }
  } finally {
    if (tab && !keep) await chrome.tabs.remove(tab.id).catch(() => {})
  }
}

// 一件一件来：页面一下子要好几件时排队
let detailQueue = Promise.resolve()

function queueItemDetail(itemId) {
  const run = detailQueue.then(() => fetchItemDetail(itemId))
  detailQueue = run.then(() => sleep(DETAIL_GAP_MS))
  return run
}

chrome.storage.onChanged.addListener((changes) => {
  if (changes.server) void registerBridge()
})

chrome.runtime.onMessage.addListener((message, _sender, reply) => {
  if (message?.type === "sync") {
    sync({ force: true }).finally(() => reply(true))
    return true // 异步回复
  }
  if (message?.type === "import-pdd") {
    importPdd(message.data).finally(() => reply(true))
    return true
  }
  if (message?.type === "fetch-item-detail") {
    queueItemDetail(String(message.itemId)).then(reply)
    return true
  }
  return false
})

chrome.runtime.onInstalled.addListener(() => {
  void sync({ force: true })
  void registerBridge()
})
chrome.runtime.onStartup.addListener(() => {
  void sync()
  void registerBridge()
})

// 浏览器重启以后定时任务可能没了，每次后台启动时补上
void chrome.alarms.get(ALARM).then((alarm) => {
  if (!alarm) void chrome.alarms.create(ALARM, { periodInMinutes: ALARM_MINUTES })
})
