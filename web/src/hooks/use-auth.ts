import { useSyncExternalStore } from "react"

import { getAuthState, subscribeAuth, type AuthState, type AuthUser } from "@/lib/auth"

export function useAuth(): AuthState {
  return useSyncExternalStore(subscribeAuth, getAuthState)
}

/** 登录以后的页面里用：外层 App 已经保证登录了才渲染 */
export function useUser(): AuthUser {
  const auth = useAuth()
  if (auth.status !== "signed-in") throw new Error("还没有登录")
  return auth.user
}
