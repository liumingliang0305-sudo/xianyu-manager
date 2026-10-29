import { Loader2Icon, TriangleAlertIcon } from "lucide-react"
import { useState, type FormEvent } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { YuanInput } from "@/features/items/price-dialog"
import { ApiError, errorMessage, type Order } from "@/lib/api"
import { parseYuan, yuan } from "@/lib/format"

import { useChangeOrderPrice, useOrderPriceFields } from "./queries"

/**
 * 待付款订单改价。能改哪几项（商品价格、运费……）由闲鱼返回，和网页版的改价弹窗一样逐项填，
 * 没填的项保持原值。总价变化超过保护线时后端返回 409，这里展示原因并让用户二次确认（force）。
 */
export function OrderPriceDialog({
  account,
  order,
  onClose,
}: {
  account: string
  order: Order
  onClose: () => void
}) {
  const fields = useOrderPriceFields(account, order.order_id)
  const [values, setValues] = useState<Record<string, string>>({})
  const [guard, setGuard] = useState<string | null>(null)
  const mutation = useChangeOrderPrice(account)

  const rows = (fields.data ?? []).map((field) => {
    const text = (values[field.key] ?? "").trim()
    return { field, text, cents: text ? parseYuan(text) : field.price_cent }
  })
  const invalid = rows.some((r) => r.text && r.cents === null)
  const missing = rows.some((r) => r.cents == null)
  const oldTotal = rows.reduce((sum, r) => sum + (r.field.price_cent ?? 0), 0)
  const newTotal = invalid || missing ? null : rows.reduce((sum, r) => sum + (r.cents ?? 0), 0)
  const changed = rows.some((r) => r.text && r.cents !== r.field.price_cent)
  const canSubmit =
    rows.length > 0 && newTotal !== null && newTotal > 0 && changed && !mutation.isPending

  const submit = (force: boolean) => {
    const fees = Object.fromEntries(rows.filter((r) => r.text).map((r) => [r.field.key, r.text]))
    mutation.mutate(
      { orderId: order.order_id, fees, force },
      {
        onSuccess: (r) => {
          toast.success(`已改价：${yuan(r.old_price_cent)} → ${yuan(r.price_cent)}`)
          onClose()
        },
        onError: (error) => {
          if (error instanceof ApiError && error.code === "price_guard") setGuard(error.message)
          else toast.error(errorMessage(error))
        },
      },
    )
  }

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (canSubmit) submit(guard !== null)
  }

  const diff = newTotal !== null ? newTotal - oldTotal : null

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <form onSubmit={onSubmit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>订单改价</DialogTitle>
            <DialogDescription className="line-clamp-2">
              {order.title || order.order_id}
            </DialogDescription>
          </DialogHeader>

          {fields.isPending && (
            <div className="grid gap-3">
              <Skeleton className="h-10 w-full" />
              <Skeleton className="h-10 w-full" />
            </div>
          )}
          {fields.isError && (
            <p className="text-destructive">拉取当前价格失败：{errorMessage(fields.error)}</p>
          )}
          {fields.data?.length === 0 && (
            <p className="text-muted-foreground">
              闲鱼没有返回可以改的价格，这个订单现在可能已经付款或关闭了。
            </p>
          )}

          {rows.map(({ field, text, cents }, i) => (
            <div key={field.key} className="grid gap-1.5">
              <Label htmlFor={`fee-${field.key}`} className="justify-between">
                {field.name}
                <span className="font-normal text-muted-foreground tabular-nums">
                  当前 {yuan(field.price_cent)}
                </span>
              </Label>
              <YuanInput
                id={`fee-${field.key}`}
                value={values[field.key] ?? ""}
                autoFocus={i === 0}
                placeholder={field.price_cent != null ? String(field.price_cent / 100) : "必填"}
                invalid={Boolean(text) && cents === null}
                onChange={(v) => {
                  setValues((prev) => ({ ...prev, [field.key]: v }))
                  setGuard(null)
                }}
              />
            </div>
          ))}

          {rows.length > 0 && (
            <div className="flex items-baseline justify-between rounded-lg bg-muted/60 px-3 py-2">
              <span className="text-muted-foreground">订单总价</span>
              <span className="tabular-nums">
                <span className="text-muted-foreground">{yuan(oldTotal)}</span>
                {newTotal !== null && changed && (
                  <>
                    {" → "}
                    <span className="font-heading text-lg font-semibold">{yuan(newTotal)}</span>
                    {diff !== 0 && oldTotal > 0 && (
                      <span className="ml-1.5 text-xs text-muted-foreground">
                        {diff! > 0 ? "+" : "−"}
                        {Math.round((Math.abs(diff!) / oldTotal) * 100)}%
                      </span>
                    )}
                  </>
                )}
              </span>
            </div>
          )}
          {invalid && <p className="text-xs text-destructive">金额最多两位小数</p>}
          {newTotal === 0 && <p className="text-xs text-destructive">订单总价必须大于 0</p>}

          {guard && (
            <div className="flex gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-amber-900 dark:text-amber-200">
              <TriangleAlertIcon className="mt-0.5 size-4 shrink-0" />
              <p>{guard}，确认没输错的话点「仍然修改」。</p>
            </div>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              取消
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {mutation.isPending && <Loader2Icon className="animate-spin" />}
              {guard ? "仍然修改" : "确认改价"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
