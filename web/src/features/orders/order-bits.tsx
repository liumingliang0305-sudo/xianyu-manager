import { Badge } from "@/components/ui/badge"
import type { OrderOp, OrderStatus } from "@/lib/api"
import { dateTime, ORDER_STATUS_LABEL, yuan } from "@/lib/format"
import { cn } from "@/lib/utils"

const STATUS_CLASS: Record<OrderStatus, string> = {
  unpaid: "bg-amber-500/10 text-amber-700 dark:text-amber-400",
  to_ship: "bg-sky-500/10 text-sky-700 dark:text-sky-400",
  shipped: "bg-violet-500/10 text-violet-700 dark:text-violet-400",
  done: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  closed: "border-border bg-transparent text-muted-foreground",
  refund: "bg-destructive/10 text-destructive",
  other: "bg-secondary text-secondary-foreground",
}

/** 状态徽标。`text` 是平台原文，和归一后的叫法不一样时鼠标悬停能看到。 */
export function OrderStatusBadge({ status, text }: { status: OrderStatus; text?: string }) {
  const label = ORDER_STATUS_LABEL[status] ?? status
  return (
    <Badge className={STATUS_CLASS[status]} title={text && text !== label ? text : undefined}>
      {label}
    </Badge>
  )
}

/** 订单改动记录。`titles` 用来把订单号换成商品标题，找不到时显示订单号。 */
export function OrderOpsList({ ops, titles }: { ops: OrderOp[]; titles?: Map<string, string> }) {
  if (ops.length === 0) {
    return <p className="py-6 text-center text-sm text-muted-foreground">还没有改动记录</p>
  }
  return (
    <ul className="divide-y">
      {ops.map((op) => (
        <li key={op.id} className="flex items-start gap-3 py-2.5 text-sm">
          <span
            className={cn(
              "mt-1.5 size-2 shrink-0 rounded-full",
              op.ok ? "bg-emerald-500" : "bg-destructive",
            )}
            aria-label={op.ok ? "成功" : "失败"}
          />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="font-medium">{op.action === "price" ? "改价" : op.action}</span>
              {op.action === "price" && (
                <span className="tabular-nums">
                  {yuan(op.detail.from)} → {yuan(op.detail.to)}
                </span>
              )}
              {titles && (
                <span className="truncate text-muted-foreground">
                  {titles.get(op.order_id) || op.order_id}
                </span>
              )}
            </div>
            {!op.ok && <p className="text-xs text-destructive">失败：{op.error}</p>}
          </div>
          <time className="shrink-0 text-xs text-muted-foreground tabular-nums">
            {dateTime(op.created_at)}
          </time>
        </li>
      ))}
    </ul>
  )
}
