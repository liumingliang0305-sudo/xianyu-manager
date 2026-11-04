// 账号显示用的名字和文字，账号菜单、账号矩阵共用

import type { Account, ImState, LoginState, LoginStatus } from "@/lib/api"
import { shortTime } from "@/lib/format"

export function maskId(id: string) {
  return id.length > 6 ? `${id.slice(0, 3)}…${id.slice(-4)}` : id
}

/** 账号叫什么：有闲鱼昵称用昵称，还没拉到资料时用打了码的 ID */
export function accountName(account: Pick<Account, "id" | "profile">): string {
  return account.profile.nick || maskId(account.id)
}

export const LOGIN_LABEL: Record<LoginState, string> = {
  valid: "有效",
  expired: "已失效",
  risk: "风控",
  error: "未知",
}

export function loginHint(login: LoginStatus): string {
  switch (login.state) {
    case "valid":
      return login.keep_login_until
        ? `保持登录到 ${shortTime(login.keep_login_until)}`
        : "没开保持登录，过期后要重新登录"
    case "expired":
      return "要重新登录"
    case "risk":
      return "到闲鱼 App 或网页完成验证后再检查"
    case "error":
      return "没查出来，稍后重新检查"
  }
}

/** 表格里放的短说明 */
export function loginNote(login: LoginStatus): string {
  switch (login.state) {
    case "valid":
      return login.keep_login_until
        ? `保持登录到 ${shortTime(login.keep_login_until)}`
        : "没开保持登录"
    case "expired":
      return "要重新登录"
    case "risk":
      return "到闲鱼完成验证后再检查"
    case "error":
      return "稍后重新检查"
  }
}

export const IM_LABEL: Record<ImState, string> = {
  online: "在线",
  connecting: "连接中",
  waiting: "等着重连",
  stopped: "已断开",
  off: "没开",
}
