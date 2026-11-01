import { ShieldAlertIcon, TriangleAlertIcon } from "lucide-react"
import type { ReactNode } from "react"

import { cn } from "@/lib/utils"

/**
 * 页面顶部的提示条（登录失效、风控熔断、保持登录快到期）。
 * negative 是已经不能用了，warning 是提前提醒。整条用同一种颜色的浅底、细边和深字，不用大面积红。
 */
export function Callout({
  tone,
  title,
  action,
  children,
}: {
  tone: "negative" | "warning"
  title?: ReactNode
  action?: ReactNode
  children?: ReactNode
}) {
  const Icon = tone === "negative" ? ShieldAlertIcon : TriangleAlertIcon
  return (
    <div
      role={tone === "negative" ? "alert" : "status"}
      className={cn(
        "flex flex-wrap items-start gap-x-3 gap-y-2 rounded-xl border px-4 py-3 text-sm",
        tone === "negative"
          ? "border-negative-line bg-negative-soft text-negative-ink"
          : "border-warning-line bg-warning-soft text-warning-ink",
      )}
    >
      <Icon className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1">
        {title && <p className="font-medium">{title}</p>}
        {children && <div className={cn(title && "mt-0.5", "flex flex-col gap-0.5")}>{children}</div>}
      </div>
      {action && <div className="-my-0.5 shrink-0">{action}</div>}
    </div>
  )
}
