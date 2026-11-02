import { StatusPill, type Tone } from "@/components/status-pill"
import { EmptyNote } from "@/components/states"
import type { OrderOp, OrderSide, OrderStatus } from "@/lib/api"
import { dateTime, orderStatusLabel, yuan } from "@/lib/format"
import { cn } from "@/lib/utils"

// 轮到我动手的用最显眼的一种：卖出的是待发货，买到的是待付款；在等对方的是琥珀色
const STATUS_TONE: Record<OrderSide, Record<OrderStatus, Tone>> = {
  sold: {
    unpaid: "warning",
    to_ship: "action",
    shipped: "neutral",
    done: "positive",
    closed: "outline",
    refund: "negative",
    other: "neutral",
  },
  bought: {
    unpaid: "action",
    to_ship: "warning",
    shipped: "neutral",
    done: "positive",
    closed: "outline",
    refund: "negative",
    other: "neutral",
  },
}

/** 状态徽标。`text` 是平台原文，和归一后的叫法不一样时鼠标悬停能看到。 */
export function OrderStatusBadge({
  status,
  side,
  text,
}: {
  status: OrderStatus
  side: OrderSide
  text?: string
}) {
  const label = orderStatusLabel(status, side)
  return (
    <StatusPill tone={STATUS_TONE[side][status]} title={text && text !== label ? text : undefined}>
      {label}
    </StatusPill>
  )
}

/** 订单改动记录。`titles` 用来把订单号换成商品标题，找不到时显示订单号。 */
export function OrderOpsList({ ops, titles }: { ops: OrderOp[]; titles?: Map<string, string> }) {
  if (ops.length === 0) {
    return <EmptyNote>还没有改动记录。在这里给待付款订单改价后，结果会记在这里</EmptyNote>
  }
  return (
    <ul className="divide-y divide-rule">
      {ops.map((op) => (
        <li key={op.id} className="flex items-start gap-3 py-2.5 text-sm">
          <span
            className={cn(
              "mt-1.5 size-2 shrink-0 rounded-full",
              op.ok ? "bg-positive" : "bg-destructive",
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
