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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { ApiError, errorMessage, type Item } from "@/lib/api"
import { parseYuan, yuan } from "@/lib/format"
import { cn } from "@/lib/utils"

import { useChangePrice } from "./queries"

export function YuanInput({
  id,
  value,
  onChange,
  invalid,
  autoFocus,
  placeholder,
}: {
  id: string
  value: string
  onChange: (value: string) => void
  invalid?: boolean
  autoFocus?: boolean
  placeholder?: string
}) {
  return (
    <div className="relative">
      <span className="pointer-events-none absolute top-1/2 left-2.5 -translate-y-1/2 text-muted-foreground">
        ¥
      </span>
      <Input
        id={id}
        inputMode="decimal"
        autoComplete="off"
        autoFocus={autoFocus}
        placeholder={placeholder}
        value={value}
        aria-invalid={invalid || undefined}
        onChange={(e) => onChange(e.target.value)}
        className="pl-6 tabular-nums"
      />
    </div>
  )
}

/** 改价。超过保护线时后端返回 409，这里展示原因并让用户二次确认（force）。 */
export function PriceDialog({
  account,
  item,
  onClose,
}: {
  account: string
  item: Item
  onClose: () => void
}) {
  const [price, setPrice] = useState("")
  const [orig, setOrig] = useState("")
  const [guard, setGuard] = useState<string | null>(null)
  const mutation = useChangePrice(account)

  const cents = parseYuan(price)
  const origCents = orig.trim() ? parseYuan(orig) : undefined
  const priceInvalid = price.trim() !== "" && (cents === null || cents <= 0)
  const origInvalid = origCents === null
  const unchanged = cents === item.price_cent && !orig.trim()
  const canSubmit = cents !== null && cents > 0 && !origInvalid && !unchanged && !mutation.isPending

  const submit = (force: boolean) => {
    mutation.mutate(
      { itemId: item.item_id, price: price.trim(), origPrice: orig.trim() || undefined, force },
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

  const diff = cents !== null && cents > 0 ? cents - item.price_cent : null

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent>
        <form onSubmit={onSubmit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>改价</DialogTitle>
            <DialogDescription className="line-clamp-2">{item.title}</DialogDescription>
          </DialogHeader>

          <div className="flex items-baseline justify-between rounded-lg bg-muted/60 px-3 py-2">
            <span className="text-muted-foreground">当前售价</span>
            <span className="font-heading text-lg font-semibold tabular-nums">
              {yuan(item.price_cent)}
            </span>
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="price">新售价</Label>
            <YuanInput
              id="price"
              value={price}
              autoFocus
              placeholder={(item.price_cent / 100).toString()}
              invalid={priceInvalid}
              onChange={(v) => {
                setPrice(v)
                setGuard(null)
              }}
            />
            <p
              className={cn(
                "min-h-4 text-xs text-muted-foreground",
                priceInvalid && "text-destructive",
              )}
            >
              {priceInvalid
                ? "请输入大于 0 的金额，最多两位小数"
                : unchanged
                  ? "和当前售价相同"
                  : diff !== null && diff !== 0
                    ? `${diff < 0 ? "降价" : "涨价"} ${yuan(Math.abs(diff))}（${diff > 0 ? "+" : "−"}${Math.round((Math.abs(diff) / item.price_cent) * 100)}%）`
                    : ""}
            </p>
          </div>

          <div className="grid gap-1.5">
            <Label htmlFor="orig-price">
              原价 <span className="font-normal text-muted-foreground">（选填，显示为划线价）</span>
            </Label>
            <YuanInput
              id="orig-price"
              value={orig}
              invalid={origInvalid}
              onChange={(v) => {
                setOrig(v)
                setGuard(null)
              }}
            />
          </div>

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
