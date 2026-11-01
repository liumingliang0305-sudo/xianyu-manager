// 管理后台的登录（后端 /api/auth，见 app/api.py）。
// 访问令牌只放在内存里，刷新令牌在 HttpOnly cookie 里，页面脚本读不到。打开页面时先拿 cookie 换一个访问令牌，
// 换不到就是没登录；访问令牌快过期时、接口说它作废了时，自动再换一个。

export interface AuthUser {
  id: number
  username: string
}

/** 登录成功、刷新、改密码都返回这个 */
export interface AuthSession {
  access_token: string
  /** 访问令牌几秒后过期 */
  expires_in: number
  user: AuthUser
}

export type AuthState =
  | { status: "checking" }
  | { status: "offline"; message: string }
  | { status: "signed-out" }
  | { status: "signed-in"; user: AuthUser }

/** 离过期不到这么久就先换新的，免得请求到一半过期 */
const EARLY_REFRESH_MS = 30_000

let state: AuthState = { status: "checking" }
let token: string | null = null
let expiresAt = 0
let refreshing: Promise<string | null> | null = null
const listeners = new Set<() => void>()

// 一个标签页里退出登录，别的标签页跟着退出
const channel = typeof BroadcastChannel === "undefined" ? null : new BroadcastChannel("xym-auth")
channel?.addEventListener("message", (event) => {
  if (event.data === "signed-out") forget()
})

function setState(next: AuthState) {
  state = next
  for (const listener of listeners) listener()
}

export function subscribeAuth(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

export function getAuthState(): AuthState {
  return state
}

/** 记下新的访问令牌（登录、刷新、改密码以后） */
export function acceptSession(session: AuthSession) {
  token = session.access_token
  expiresAt = Date.now() + session.expires_in * 1000
  const current = state.status === "signed-in" ? state.user : null
  if (current?.id !== session.user.id || current.username !== session.user.username) {
    setState({ status: "signed-in", user: session.user })
  }
}

/** 丢掉访问令牌，回到登录页 */
function forget() {
  token = null
  expiresAt = 0
  if (state.status !== "signed-out") setState({ status: "signed-out" })
}

/**
 * 拿 cookie 里的刷新令牌换一个访问令牌，同一时间只换一次。
 * 没登录、登录过期了返回 null（页面回到登录页）；连不上后台时抛错。
 */
export function refreshAccess(): Promise<string | null> {
  refreshing ??= (async () => {
    try {
      let resp: Response
      try {
        resp = await fetch("/api/auth/refresh", { method: "POST" })
      } catch {
        throw new Error("连不上后台，确认 uv run xym serve 在运行")
      }
      if (resp.status === 401) {
        forget()
        return null
      }
      if (resp.status === 404) {
        // 拉了新代码，但跑着的还是以前的 xym serve
        throw new Error("后台还是旧版本，没有登录接口。停掉正在跑的 xym serve，重新启动")
      }
      if (!resp.ok) throw new Error(`后台出错了（HTTP ${resp.status}）`)
      acceptSession((await resp.json()) as AuthSession)
      return token
    } finally {
      refreshing = null
    }
  })()
  return refreshing
}

/** 打开页面时：看看还是不是登录着的 */
export async function checkSession() {
  setState({ status: "checking" })
  try {
    await refreshAccess()
  } catch (error) {
    setState({ status: "offline", message: error instanceof Error ? error.message : String(error) })
  }
}

/** 请求要带的访问令牌：快过期了先换新的；没登录时是 null */
export async function accessToken(): Promise<string | null> {
  if (token && Date.now() < expiresAt - EARLY_REFRESH_MS) return token
  if (state.status !== "signed-in") return null
  return refreshAccess()
}

/** 接口说访问令牌作废了（改了密码、被停用）：换一个；换不到就回登录页 */
export function tokenRejected(): Promise<string | null> {
  token = null
  return refreshAccess()
}

export async function signOut() {
  // 先回登录页，后台删刷新令牌失败也不影响（cookie 还会在 7 天没用后过期）
  forget()
  channel?.postMessage("signed-out")
  await fetch("/api/auth/logout", { method: "POST" }).catch(() => {})
}
