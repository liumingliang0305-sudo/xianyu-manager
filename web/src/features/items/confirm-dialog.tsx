import { Loader2Icon } from "lucide-react"
import { toast } from "sonner"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { errorMessage, type Item } from "@/lib/api"

import { useDeleteItem, useDownshelf } from "./queries"

const COPY = {
  downshelf: {
    title: "下架这个商品？",
    body: "下架后买家就看不到了。网页端没有重新上架的入口，之后要在闲鱼 App 里重新上架。",
    action: "下架",
    done: "已下架",
  },
  delete: {
    title: "删除这个商品？",
    body: "删除后在闲鱼上无法恢复，本地的商品记录也会一并删除（改动记录保留）。",
    action: "删除",
    done: "已删除",
  },
}

export function ConfirmItemDialog({
  account,
  item,
  kind,
  onClose,
}: {
  account: string
  item: Item
  kind: "downshelf" | "delete"
  onClose: () => void
}) {
  const downshelf = useDownshelf(account)
  const remove = useDeleteItem(account)
  const mutation = kind === "delete" ? remove : downshelf
  const copy = COPY[kind]

  return (
    <AlertDialog open onOpenChange={(open) => !open && !mutation.isPending && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{copy.title}</AlertDialogTitle>
          <AlertDialogDescription>
            <span className="mb-2 block font-medium text-foreground">{item.title}</span>
            {copy.body}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={mutation.isPending}>取消</AlertDialogCancel>
          <AlertDialogAction
            variant={kind === "delete" ? "destructive" : "default"}
            disabled={mutation.isPending}
            onClick={(e) => {
              // 等请求结束再关，失败时用户还能看到弹窗
              e.preventDefault()
              mutation.mutate(item.item_id, {
                onSuccess: () => {
                  toast.success(copy.done)
                  onClose()
                },
                onError: (error) => toast.error(errorMessage(error)),
              })
            }}
          >
            {mutation.isPending && <Loader2Icon className="animate-spin" />}
            {copy.action}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
