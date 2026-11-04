import { useState } from "react"

import type { Account, LoginState } from "@/lib/api"
import { thumb } from "@/lib/format"
import { cn } from "@/lib/utils"

const LOGIN_DOT: Record<LoginState, string> = {
  valid: "bg-positive",
  expired: "bg-destructive",
  risk: "bg-warning-ink",
  error: "bg-muted-foreground",
}

export function LoginDot({ state }: { state: LoginState }) {
  return <span className={cn("size-2 shrink-0 rounded-full", LOGIN_DOT[state])} aria-hidden />
}

/**
 * 账号头像：闲鱼头像，没有或加载失败时是昵称的第一个字。头像是圆的（人），商品图才是方的。
 */
export function AccountAvatar({
  account,
  size = 32,
  className,
}: {
  account: Pick<Account, "id" | "profile">
  size?: number
  className?: string
}) {
  const [failed, setFailed] = useState(false)
  const box = cn("shrink-0 overflow-hidden rounded-full bg-secondary", className)
  const { avatar, nick } = account.profile
  if (!avatar || failed) {
    return (
      <span
        aria-hidden
        className={cn(box, "flex items-center justify-center font-medium text-secondary-foreground")}
        style={{ width: size, height: size, fontSize: Math.round(size * 0.42) }}
      >
        {nick ? Array.from(nick)[0] : "鱼"}
      </span>
    )
  }
  return (
    <img
      src={thumb(avatar, size * 2)}
      alt=""
      width={size}
      height={size}
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      // 图床上没有这张图时回一张 1×1 的空图，不报错
      onLoad={(e) => e.currentTarget.naturalWidth <= 1 && setFailed(true)}
      className={cn(box, "object-cover")}
      style={{ width: size, height: size }}
    />
  )
}
