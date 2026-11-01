// 插件弹窗：看上次同步的结果，手动同步，改管理后台地址和插件令牌。

import {
  DEFAULT_SERVER,
  isLocalServer,
  normalizeServer,
  normalizeToken,
  serverPermission,
} from "./lib.js"

const $ = (id) => document.getElementById(id)

function render({ status, server = DEFAULT_SERVER, token = "" }) {
  if (document.activeElement !== $("server")) $("server").value = server
  if (document.activeElement !== $("token")) $("token").value = token
  if (!status) return
  // 和管理后台一样按北京时间显示
  const time = new Date(status.at).toLocaleString("zh-CN", {
    timeZone: "Asia/Shanghai",
    hourCycle: "h23",
  })
  $("status").className = status.ok ? "ok" : "error"
  $("status").textContent = status.ok
    ? `已同步（账号 ${status.account}）${status.changed ? "" : "，登录没变"}`
    : "同步失败"
  $("detail").textContent = status.message
  $("time").textContent = `上次同步 ${time}`
}

async function load() {
  render(await chrome.storage.local.get(["status", "server", "token"]))
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

chrome.storage.onChanged.addListener(() => void load())
void load()
