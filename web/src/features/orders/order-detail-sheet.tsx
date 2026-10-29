import { CheckIcon, ExternalLinkIcon, MapPinIcon, PencilIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { ItemThumb } from "@/features/items/item-bits"
import { errorMessage, type Order, type OrderDetail } from "@/lib/api"
import { dateTime, orderUrl, yuan } from "@/lib/format"
import { cn } from "@/lib/utils"

import { OrderOpsList, OrderStatusBadge } from "./order-bits"
import { useOrderDetail, useOrderOps } from "./queries"

/** 秒级时间戳 → "9/25 16:53" */
function shortTime(seconds: number): string {
  const d = new Date(seconds * 1000)
  const pad = (n: number) => String(n).padStart(2, "0")
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`
}

function Steps({ steps }: { steps: OrderDetail["steps"] }) {
  if (steps.length === 0) return null
  return (
    <ol className="flex items-start">
      {steps.map((step, i) => (
        <li key={`${step.title}-${i}`} className="flex flex-1 flex-col items-center gap-1 text-center">
          <div className="flex w-full items-center">
            <span className={cn("h-px flex-1", i > 0 && (step.done ? "bg-primary" : "bg-border"))} />
            <span
              className={cn(
                "flex size-5 items-center justify-center rounded-full border text-[10px]",
                step.done ? "border-primary bg-primary text-primary-foreground" : "bg-background",
              )}
            >
              {step.done ? <CheckIcon className="size-3" /> : i + 1}
            </span>
            <span
              className={cn(
                "h-px flex-1",
                i < steps.length - 1 && (steps[i + 1].done ? "bg-primary" : "bg-border"),
              )}
            />
          </div>
          <span className={cn("text-xs", step.done ? "text-foreground" : "text-muted-foreground")}>
            {step.title}
          </span>
          {step.time && (
            <span className="text-[10px] leading-tight text-muted-foreground tabular-nums">
              {shortTime(step.time)}
            </span>
          )}
        </li>
      ))}
    </ol>
  )
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 text-sm">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <span className="min-w-0 text-right break-all">{children}</span>
    </div>
  )
}

/** 订单详情：实时从闲鱼拉，下面附本系统对它做过的改动。 */
export function OrderDetailSheet({
  account,
  order,
  onClose,
  onChangePrice,
}: {
  account: string
  order: Order
  onClose: () => void
  onChangePrice: () => void
}) {
  const detail = useOrderDetail(account, order.order_id)
  const ops = useOrderOps(account, order.order_id)
  const d = detail.data
  const status = d && d.status !== "other" ? d.status : order.status

  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full gap-0 overflow-y-auto sm:max-w-lg">
        <SheetHeader className="border-b">
          <div className="flex items-center gap-2">
            <OrderStatusBadge status={status} text={d?.status_title || order.status_text} />
            <span className="font-mono text-xs text-muted-foreground">{order.order_id}</span>
          </div>
          <SheetTitle className="pr-6 leading-snug">
            {d?.status_title || order.status_text || "订单详情"}
          </SheetTitle>
          <SheetDescription className={cn(!d?.status_desc && "sr-only")}>
            {d?.status_desc || "订单详情"}
          </SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-5 p-4">
          {detail.isPending && (
            <div className="flex flex-col gap-3">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-32 w-full" />
            </div>
          )}
          {detail.isError && (
            <p className="text-destructive">拉取详情失败：{errorMessage(detail.error)}</p>
          )}

          {d && (
            <>
              <Steps steps={d.steps} />

              <div className="flex gap-3">
                <ItemThumb url={d.pic_url || order.pic_url} size={72} />
                <div className="min-w-0 flex-1">
                  <div className="line-clamp-2 font-medium">{d.title || order.title}</div>
                  <div className="mt-1 text-xs text-muted-foreground">
                    {[d.sku, d.quantity > 1 ? `数量 ×${d.quantity}` : ""].filter(Boolean).join(" · ")}
                  </div>
                </div>
              </div>

              <div className="flex flex-col gap-1.5 rounded-lg bg-muted/60 px-3 py-2.5">
                {d.bills.map((bill) => (
                  <Row key={bill.title} label={bill.title}>
                    <span className="tabular-nums">{yuan(bill.cent)}</span>
                  </Row>
                ))}
                <div className="flex items-baseline justify-between">
                  <span className="text-sm text-muted-foreground">{d.amount_title || "订单金额"}</span>
                  <span className="font-heading text-xl font-semibold tabular-nums">
                    {yuan(d.amount_cent ?? order.price_cent)}
                  </span>
                </div>
                {d.income_cent != null && (
                  <Row label={d.income_title || "到手"}>
                    <span className="font-medium text-emerald-700 tabular-nums dark:text-emerald-400">
                      {yuan(d.income_cent)}
                    </span>
                  </Row>
                )}
              </div>

              {(d.receiver_name || d.receiver_address) && (
                <div className="flex gap-2 text-sm">
                  <MapPinIcon className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                  <div>
                    <div className="font-medium">
                      {d.receiver_name} <span className="text-muted-foreground">{d.receiver_phone}</span>
                    </div>
                    <div className="text-muted-foreground">{d.receiver_address}</div>
                  </div>
                </div>
              )}

              {d.fields.length > 0 && (
                <div className="flex flex-col gap-1.5">
                  {d.fields.map((f) => (
                    <Row key={f.title} label={f.title}>
                      {f.value}
                    </Row>
                  ))}
                </div>
              )}

              {d.actions.length > 0 && (
                <p className="text-xs text-muted-foreground">
                  闲鱼上可以做的操作：{d.actions.join("、")}
                </p>
              )}
            </>
          )}

          <div className="flex flex-wrap gap-2">
            {status === "unpaid" && (
              <Button onClick={onChangePrice}>
                <PencilIcon />
                改价
              </Button>
            )}
            <Button variant="outline" asChild>
              <a href={orderUrl(order.order_id)} target="_blank" rel="noreferrer">
                <ExternalLinkIcon />
                在闲鱼打开
              </a>
            </Button>
          </div>

          <Separator />

          <div>
            <h3 className="mb-1 text-xs font-medium text-muted-foreground">本系统的改动记录</h3>
            {ops.data ? <OrderOpsList ops={ops.data} /> : <Skeleton className="h-12 w-full" />}
          </div>

          <p className="text-xs text-muted-foreground">
            本地记录：首次同步 {dateTime(order.first_seen_at)}，最近同步 {dateTime(order.synced_at)}
          </p>
        </div>
      </SheetContent>
    </Sheet>
  )
}
