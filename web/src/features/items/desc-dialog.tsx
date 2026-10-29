import { Loader2Icon } from "lucide-react"
import { useState } from "react"
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
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { errorMessage, type Item } from "@/lib/api"

import { useChangeDesc, useItemDetail } from "./queries"

function DescForm({
  account,
  item,
  initial,
  onClose,
}: {
  account: string
  item: Item
  initial: string
  onClose: () => void
}) {
  const [text, setText] = useState(initial)
  const mutation = useChangeDesc(account)
  const trimmed = text.trim()
  const canSubmit = trimmed !== "" && trimmed !== initial.trim() && !mutation.isPending

  const submit = () =>
    mutation.mutate(
      { itemId: item.item_id, desc: trimmed },
      {
        onSuccess: () => {
          toast.success("描述已更新，标题会在下次同步时刷新")
          onClose()
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )

  return (
    <>
      <div className="grid gap-1.5">
        <Textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={14}
          className="max-h-[50vh] resize-y"
          autoFocus
        />
        <p className="flex justify-between text-xs text-muted-foreground">
          <span>标题由闲鱼从描述开头截取</span>
          <span className="tabular-nums">{text.length} 字</span>
        </p>
      </div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          取消
        </Button>
        <Button onClick={submit} disabled={!canSubmit}>
          {mutation.isPending && <Loader2Icon className="animate-spin" />}
          保存
        </Button>
      </DialogFooter>
    </>
  )
}

/** 改描述。打开时先从闲鱼拉一次当前描述，本地库里只存了标题。 */
export function DescDialog({
  account,
  item,
  onClose,
}: {
  account: string
  item: Item
  onClose: () => void
}) {
  const detail = useItemDetail(account, item.item_id)

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>改描述</DialogTitle>
          <DialogDescription className="line-clamp-1">{item.title}</DialogDescription>
        </DialogHeader>
        {detail.isPending ? (
          <Skeleton className="h-72 w-full" />
        ) : detail.isError ? (
          <p className="text-destructive">拉取当前描述失败：{errorMessage(detail.error)}</p>
        ) : (
          <DescForm account={account} item={item} initial={detail.data.desc} onClose={onClose} />
        )}
      </DialogContent>
    </Dialog>
  )
}
