import type { ReactNode } from "react"

import { Badge } from "@/components/ui/badge"
import { cn } from "@/lib/utils"

/**
 * 状态标签只有这几种语气，商品、订单、消息共用：
 * positive 成了（在售、交易成功）；negative 出了问题（退款中）；warning 等对方（待付款）；
 * action 轮到我做（待发货），近黑实心，全站最显眼的一种，只给真正要动手的状态；
 * neutral 进行中或已结束（已卖出、已发货）；outline 不在了（不在主页、已关闭）。
 */
export type Tone = "positive" | "negative" | "warning" | "action" | "neutral" | "outline"

const TONE_CLASS: Record<Tone, string> = {
  positive: "bg-positive-soft text-positive-ink",
  negative: "bg-negative-soft text-negative-ink",
  warning: "bg-warning-soft text-warning-ink",
  action: "bg-primary text-primary-foreground",
  neutral: "bg-secondary text-secondary-foreground",
  outline: "border-border bg-transparent text-muted-foreground",
}

export function StatusPill({
  tone,
  title,
  className,
  children,
}: {
  tone: Tone
  title?: string
  className?: string
  children: ReactNode
}) {
  return (
    <Badge className={cn(TONE_CLASS[tone], className)} title={title}>
      {children}
    </Badge>
  )
}
