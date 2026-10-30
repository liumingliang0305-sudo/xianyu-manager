// 插件弹窗：看上次同步的结果，手动同步，改管理后台地址。

import { DEFAULT_SERVER, normalizeServer } from "./lib.js"

const $ = (id) => document.getElementById(id)

function render({ status, server = DEFAULT_SERVER }) {
  if (document.activeElement !== $("server")) $("server").value = server
  if (!status) return
  const time = new Date(status.at).toLocaleString("zh-CN", { hour12: false })
  $("status").className = status.ok ? "ok" : "error"
  $("status").textContent = status.ok
    ? `已同步（账号 ${status.account}）${status.changed ? "" : "，登录没变"}`
    : "同步失败"
  $("detail").textContent = status.message
  $("time").textContent = `上次同步 ${time}`
}

async function load() {
  render(await chrome.storage.local.get(["status", "server"]))
}

$("sync").addEventListener("click", async () => {
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
  // 换了后台就当没同步过，下次一定推
  await chrome.storage.local.set({ server, lastKey: null })
})

chrome.storage.onChanged.addListener(() => void load())
void load()
