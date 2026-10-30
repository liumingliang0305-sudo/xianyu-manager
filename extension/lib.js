// 登录同步插件的纯函数。background.js 和 popup.js 共用，不碰 chrome.* 接口，方便直接用 node 测。

export const DEFAULT_SERVER = "http://127.0.0.1:8000"

// 这几个 cookie 变了才算登录变了：账号、会话、保持登录标记。和后台 app/login.py 的 SAME_LOGIN_COOKIES 一致
export const LOGIN_COOKIES = ["unb", "cookie2", "havana_lgc_exp", "cookie3_bak_exp"]

const ROOT_DOMAIN = "goofish.com"

/** cookie 的 domain（可能带前导点）是不是 goofish.com 或它的子域 */
export function isGoofishDomain(domain) {
  const host = domain.replace(/^\./, "")
  return host === ROOT_DOMAIN || host.endsWith(`.${ROOT_DOMAIN}`)
}

/**
 * chrome.cookies.getAll 的结果压平成 name → value。
 * 同名 cookie 可能同时种在 .goofish.com 和某个子域上，以 .goofish.com 的为准（和后台 jar_to_cookies 一样）。
 */
export function flattenCookies(cookies) {
  const shared = {}
  const others = {}
  for (const cookie of cookies) {
    if (!isGoofishDomain(cookie.domain)) continue
    const target = cookie.domain.replace(/^\./, "") === ROOT_DOMAIN ? shared : others
    target[cookie.name] = cookie.value
  }
  return { ...others, ...shared }
}

/** 拼成 Cookie 请求头的格式，后台 /api/login/import 收的就是这个 */
export function cookieHeader(cookies) {
  return Object.entries(cookies)
    .map(([name, value]) => `${name}=${value}`)
    .join("; ")
}

/** 登录的指纹：这几个 cookie 都没变就不用再推 */
export function loginKey(cookies) {
  return LOGIN_COOKIES.map((name) => cookies[name] ?? "").join("\n")
}

export function isLoggedIn(cookies) {
  return Boolean(cookies.unb && cookies.cookie2)
}

/** 后台地址只允许本机的 http 地址，cookie 不能发到别处。不合格时返回 null */
export function normalizeServer(text) {
  let url
  try {
    url = new URL(text.trim())
  } catch {
    return null
  }
  if (url.protocol !== "http:" || !["127.0.0.1", "localhost"].includes(url.hostname)) return null
  return url.origin
}

/**
 * 推送失败以后要不要等 cookie 变了再推。
 * 4xx 是这份 cookie 本身的问题（过期、缺字段、不是这个账号），原样再推也一样；
 * 429 限流和 5xx（风控、平台出错）是暂时的，定时任务会再试。
 */
export function isFinalFailure(status) {
  return status >= 400 && status < 500 && status !== 429
}
