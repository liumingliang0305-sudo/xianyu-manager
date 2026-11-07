// 注入到管理后台页面里的内容脚本：页面和插件后台之间传话。
// 页面（搜索暂存）要一件商品的详情时 postMessage 过来，这里转给插件后台，插件后台在闲鱼商品页上拿到
// （见 goofish.js），结果再 postMessage 回页面，页面自己存到管理后台。
// 只在插件里填的管理后台地址上注入（background.js 的 registerBridge），只认同一个页面自己发的消息。
//
// 页面发：{ source: "xym-admin", type: "fetch-item-detail", id, itemId }，{ source: "xym-admin", type: "ping" }
// 回页面：{ source: "xym-extension", type: "item-detail", id, result }，{ source: "xym-extension", type: "ready", version }
;(() => {
  const version = chrome.runtime.getManifest().version
  const reply = (message) => window.postMessage({ source: "xym-extension", ...message }, location.origin)

  // 页面一打开就能看出装了插件（document_start 时就写上）
  document.documentElement.dataset.xymExtension = version
  reply({ type: "ready", version })

  window.addEventListener("message", (event) => {
    if (event.source !== window || event.origin !== location.origin) return
    const message = event.data
    if (message?.source !== "xym-admin") return
    if (message.type === "ping") {
      reply({ type: "ready", version })
      return
    }
    if (message.type !== "fetch-item-detail" || typeof message.id !== "string") return
    const done = (result) => reply({ type: "item-detail", id: message.id, result })
    try {
      chrome.runtime
        .sendMessage({ type: "fetch-item-detail", itemId: String(message.itemId) })
        .then((result) => done(result ?? { error: "failed", message: "插件后台没有回结果" }))
        .catch((error) => done({ error: "failed", message: `插件后台没回应：${error.message}` }))
    } catch {
      // 插件更新、重新加载以后，已经打开的页面里这段脚本就断了
      done({ error: "reload", message: "插件刚更新过，刷新一下这个页面再试" })
    }
  })
})()
