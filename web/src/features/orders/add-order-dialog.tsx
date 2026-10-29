import { Loader2Icon } from "lucide-react"
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
import { errorMessage, type Order } from "@/lib/api"

import { useAddOrder } from "./queries"

/** 按订单号添加一笔卖出的订单。个人卖家在网页端拿不到卖出订单列表，只能这样一笔笔加。 */
export function AddOrderDialog({
  account,
  onClose,
  onAdded,
}: {
  account: string
  onClose: () => void
  onAdded: (order: Order) => void
}) {
  const [text, setText] = useState("")
  const [error, setError] = useState<string | null>(null)
  const mutation = useAddOrder(account)

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (!text.trim() || mutation.isPending) return
    mutation.mutate(text.trim(), {
      onSuccess: (r) => {
        toast.success(r.added ? "已添加订单" : "订单已在列表里，刷新了一次")
        onAdded(r.order)
      },
      onError: (err) => setError(errorMessage(err)),
    })
  }

  return (
    <Dialog open onOpenChange={(open) => !open && !mutation.isPending && onClose()}>
      <DialogContent>
        <form onSubmit={onSubmit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>添加订单</DialogTitle>
            <DialogDescription>
              在闲鱼 App「我的 → 我卖出的」里点进订单，复制订单编号粘贴到这里。也可以粘贴订单链接。
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-1.5">
            <Label htmlFor="order-id">订单编号</Label>
            <Input
              id="order-id"
              autoFocus
              autoComplete="off"
              inputMode="numeric"
              placeholder="5127…"
              value={text}
              aria-invalid={error ? true : undefined}
              onChange={(e) => {
                setText(e.target.value)
                setError(null)
              }}
              className="font-mono"
            />
            <p className="min-h-4 text-xs text-destructive">{error}</p>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={mutation.isPending}>
              取消
            </Button>
            <Button type="submit" disabled={!text.trim() || mutation.isPending}>
              {mutation.isPending && <Loader2Icon className="animate-spin" />}
              添加
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
