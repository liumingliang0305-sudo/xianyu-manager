import { useEffect, useState } from "react"

/**
 * 和浏览器插件（extension/）说话。插件 0.4.0 起往管理后台页面里注入一段传话脚本（extension/bridge.js），
 * 页面用 postMessage 请它办事，它把结果 postMessage 回来。
 *
 * 现在只有一件事：拿闲鱼商品详情。管理后台的服务端调商品详情接口看别人的商品一调就要滑块，
 * 插件在用户自己的 Chrome 里打开那件商品的闲鱼页面、用页面自己的请求库拿（extension/goofish.js）。
 */

const FROM_PAGE = "xym-admin"
const FROM_EXTENSION = "xym-extension"
// 插件那边一次只开一个商品页，排在后面的要等前面的；一件一般几秒
const DETAIL_TIMEOUT_MS = 120_000

/** 插件的版本，没装（或者插件里填的不是这个后台）是 null */
function installedVersion(): string | null {
  return document.documentElement.dataset.xymExtension ?? null
}

/** 装没装插件。传话脚本可能比页面晚一点注入，听它报到 */
export function useExtension(): string | null {
  const [version, setVersion] = useState(installedVersion)
  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.source !== window || event.data?.source !== FROM_EXTENSION) return
      if (event.data.type === "ready") setVersion(String(event.data.version))
    }
    window.addEventListener("message", onMessage)
    window.postMessage({ source: FROM_PAGE, type: "ping" }, location.origin)
    return () => window.removeEventListener("message", onMessage)
  }, [])
  return version
}

/** 插件没拿到详情。code：validate 闲鱼要验证，gone 商品没了，timeout 插件没回应，别的见 goofish.js */
export class ExtensionError extends Error {
  readonly code: string

  constructor(code: string, message: string) {
    super(message)
    this.code = code
  }
}

let nextId = 0

/** 请插件在 Chrome 里打开这件商品的闲鱼页面拿详情。返回 pc.detail 的 itemDO（挑过字段），原样交给后台存 */
export function fetchItemDetailViaExtension(itemId: string): Promise<Record<string, unknown>> {
  if (!installedVersion()) {
    return Promise.reject(
      new ExtensionError("missing", "没检测到浏览器插件：装上 0.4.0 以上的插件，在插件里填好这个后台的地址"),
    )
  }
  const id = `${Date.now()}-${nextId++}`
  return new Promise((resolve, reject) => {
    const finish = () => {
      clearTimeout(timer)
      window.removeEventListener("message", onMessage)
    }
    const onMessage = (event: MessageEvent) => {
      const data = event.data
      if (event.source !== window || data?.source !== FROM_EXTENSION) return
      if (data.type !== "item-detail" || data.id !== id) return
      finish()
      const result = data.result ?? {}
      if (result.item && typeof result.item === "object") resolve(result.item)
      else reject(new ExtensionError(result.error ?? "failed", result.message ?? "插件没拿到详情"))
    }
    const timer = setTimeout(() => {
      finish()
      reject(new ExtensionError("timeout", "插件两分钟没回应：看看 Chrome 里有没有打开的闲鱼页面在等验证"))
    }, DETAIL_TIMEOUT_MS)
    window.addEventListener("message", onMessage)
    window.postMessage({ source: FROM_PAGE, type: "fetch-item-detail", id, itemId }, location.origin)
  })
}
