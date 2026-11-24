import { useEffect, useState } from "react"

/**
 * 和浏览器插件（extension/）说话。插件 0.4.0 起往管理后台页面里注入一段传话脚本（extension/bridge.js），
 * 页面用 postMessage 请它办事，它把结果 postMessage 回来。
 *
 * 现在只有一件事：拿闲鱼商品详情。管理后台的服务端调买家看的商品详情接口会被闲鱼要求过滑块
 * （别人的商品、自己的商品都是），插件在用户自己的 Chrome 里打开那件商品的闲鱼页面、
 * 用页面自己的请求库拿（extension/goofish.js）。搜索暂存用它拿描述和图片，商品页用它刷新浏览、收藏。
 */

const FROM_PAGE = "xym-admin"
const FROM_EXTENSION = "xym-extension"
// 插件那边一次只开一个商品页，排在后面的要等前面的；一件一般几秒
const DETAIL_TIMEOUT_MS = 120_000

/** 能在商品页上自动刷新浏览、收藏的插件版本：0.5.0 起认 quiet（自动刷新要验证时不抢前台）和 resume */
export const QUIET_DETAIL_VERSION = "0.5.0"

/** 插件版本够不够：按点分开逐段比数字，认不出的当不够 */
export function versionAtLeast(version: string | null, min: string): boolean {
  if (!version) return false
  const have = version.split(".").map(Number)
  const need = min.split(".").map(Number)
  if (have.some(Number.isNaN)) return false
  for (let i = 0; i < need.length; i++) {
    const a = have[i] ?? 0
    if (a !== need[i]) return a > need[i]
  }
  return true
}

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

/**
 * 请插件在 Chrome 里打开这件商品的闲鱼页面拿详情。返回 pc.detail 的 itemDO（挑过字段），原样交给后台存。
 * quiet：自动刷新，闲鱼要验证时插件不把那个页面切到前面（关掉它），交给页面提示；
 * resume：人刚说过了滑块，插件不用再等验证后的那一分钟。两个都是 0.5.0 起，旧插件不认、照旧
 */
export function fetchItemDetailViaExtension(
  itemId: string,
  options: { quiet?: boolean; resume?: boolean } = {},
): Promise<Record<string, unknown>> {
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
    window.postMessage(
      {
        source: FROM_PAGE,
        type: "fetch-item-detail",
        id,
        itemId,
        quiet: options.quiet === true,
        resume: options.resume === true,
      },
      location.origin,
    )
  })
}
