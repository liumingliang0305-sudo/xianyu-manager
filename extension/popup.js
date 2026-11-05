// 插件弹窗：在拼多多商品页上导入商品；看上次登录同步的结果，手动同步，改管理后台地址和插件令牌。

import {
  DEFAULT_SERVER,
  isLocalServer,
  isPddGoodsPage,
  isPddPage,
  normalizeServer,
  normalizeToken,
  serverPermission,
} from "./lib.js"
import { readPddGoods } from "./pdd.js"

const $ = (id) => document.getElementById(id)

// 和管理后台一样按北京时间显示
const beijing = (at) =>
  new Date(at).toLocaleString("zh-CN", { timeZone: "Asia/Shanghai", hourCycle: "h23" })

/** 导入中的状态过了这么久还没结果，就是插件后台中途停了 */
const PENDING_STALE_MS = 2 * 60 * 1000

/** 弹窗打开时的那个标签页。点开插件图标就有这个标签页的临时权限（activeTab），读得到网址、能注入脚本 */
let tab = null

function renderPdd(pddStatus, server) {
  const onGoods = Boolean(tab?.url && isPddGoodsPage(tab.url))
  $("pdd-import").hidden = !onGoods
  $("pdd-hint").textContent = onGoods
    ? "把这个商品的标题、图片、规格、参数导入管理后台的「拼多多货源」"
    : tab?.url && isPddPage(tab.url)
      ? "打开一个商品页（先登录拼多多），再点这里导入"
      : "在这个浏览器里打开拼多多商品页（mobile.yangkeduo.com），再点插件图标导入"
  const status = pddStatus
  $("pdd-status").hidden = !status
  $("pdd-detail").hidden = !status
  $("pdd-open").hidden = !status?.ok
  if (!status) return
  if (status.pending && Date.now() - status.at > PENDING_STALE_MS) {
    renderPdd({ ...status, pending: false, ok: false, message: "导入没做完（插件后台中断了），再点一次导入" }, server)
    return
  }
  $("pdd-status").className = status.pending ? "" : status.ok ? "ok" : "error"
  const what = status.pending ? "导入中" : status.ok ? (status.added ? "已导入" : "已更新") : "导入失败"
  $("pdd-status").textContent = status.title ? `${what}：${status.title}` : what
  $("pdd-detail").textContent = `${status.message}（${beijing(status.at)}）`
  if (status.ok) $("pdd-open").dataset.url = `${server}/pdd?goods=${status.goodsId}`
}

function render({ status, pddStatus, server = DEFAULT_SERVER, token = "" }) {
  if (document.activeElement !== $("server")) $("server").value = server
  if (document.activeElement !== $("token")) $("token").value = token
  renderPdd(pddStatus, server)
  if (!status) return
  const time = beijing(status.at)
  $("status").className = status.ok ? "ok" : "error"
  $("status").textContent = status.ok
    ? `已同步（账号 ${status.account}）${status.changed ? "" : "，登录没变"}`
    : "同步失败"
  $("detail").textContent = status.message
  $("time").textContent = `上次同步 ${time}`
}

async function load() {
  render(await chrome.storage.local.get(["status", "pddStatus", "server", "token"]))
}

/**
 * 远程后台要单独允许插件访问（只申请这一个地址）。要在点击、回车的处理函数里一开始就调，
 * 浏览器只在用户刚操作过时弹授权提示。允许以后后台会自己推一次（见 background.js）
 */
function requestAccess(server) {
  if (!server || isLocalServer(server)) return
  chrome.permissions.request(serverPermission(server)).catch(() => {})
}

$("sync").addEventListener("click", async () => {
  requestAccess(normalizeServer($("server").value))
  const button = $("sync")
  button.disabled = true
  button.textContent = "同步中…"
  try {
    await chrome.runtime.sendMessage({ type: "sync" })
  } finally {
    button.disabled = false
    button.textContent = "立即同步"
  }
})

$("server").addEventListener("change", async (event) => {
  const server = normalizeServer(event.target.value)
  $("server-error").hidden = server !== null
  if (!server) return
  event.target.value = server
  // 换了后台就当没同步过，下次一定推。先存（不等它），再申请权限：授权提示出来时弹窗可能会关掉
  void chrome.storage.local.set({ server, lastKey: null })
  requestAccess(server)
})

$("token").addEventListener("change", async (event) => {
  const token = normalizeToken(event.target.value)
  $("token-error").hidden = token !== null
  if (!token) return
  event.target.value = token
  // 换了令牌马上推一次（推到哪个用户名下跟着变了）
  await chrome.storage.local.set({ token, lastKey: null })
  await chrome.runtime.sendMessage({ type: "sync" })
})

$("pdd-import").addEventListener("click", async () => {
  // 远程后台的访问权限要在点击时一开始就申请（同"立即同步"）
  requestAccess(normalizeServer($("server").value))
  const button = $("pdd-import")
  button.disabled = true
  button.textContent = "读取中…"
  try {
    let result
    try {
      // 在页面自己的环境里跑才读得到 window.rawData；只读，不改页面
      const [injection] = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        world: "MAIN",
        func: readPddGoods,
      })
      result = injection?.result
    } catch (error) {
      result = { message: `读不了这个页面：${error.message}。刷新一下页面再试` }
    }
    if (!result?.goods) {
      await chrome.storage.local.set({
        pddStatus: { ok: false, message: result?.message ?? "这个页面上没找到商品数据", at: Date.now() },
      })
      return
    }
    button.textContent = "导入中…"
    await chrome.runtime.sendMessage({ type: "import-pdd", data: result })
  } finally {
    button.disabled = false
    button.textContent = "导入这个商品"
  }
})

$("pdd-open").addEventListener("click", (event) => {
  event.preventDefault()
  void chrome.tabs.create({ url: event.currentTarget.dataset.url })
})

chrome.storage.onChanged.addListener(() => void load())
void chrome.tabs
  .query({ active: true, currentWindow: true })
  .then(([active]) => {
    tab = active ?? null
  })
  .finally(() => void load())
