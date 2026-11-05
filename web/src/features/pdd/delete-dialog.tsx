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
import { errorMessage, type PddGoodsSummary } from "@/lib/api"

import { useDeletePddGoods } from "./queries"

export function DeletePddDialog({
  goods,
  onClose,
  onDeleted,
}: {
  goods: PddGoodsSummary
  onClose: () => void
  onDeleted?: () => void
}) {
  const remove = useDeletePddGoods()
  return (
    <AlertDialog open onOpenChange={(open) => !open && !remove.isPending && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除这个货源？</AlertDialogTitle>
          <AlertDialogDescription>
            <span className="mb-2 block font-medium text-foreground">{goods.title}</span>
            本地的商品记录和下载下来的图片一起删掉，拼多多上的商品不受影响。照它建好的草稿还在，草稿用的图也留着。
            以后在拼多多商品页上再导入一次就回来了。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={remove.isPending}>取消</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={remove.isPending}
            onClick={(e) => {
              e.preventDefault()
              remove.mutate(goods.goods_id, {
                onSuccess: () => {
                  toast.success("已删除")
                  onDeleted?.()
                  onClose()
                },
                onError: (error) => toast.error(errorMessage(error)),
              })
            }}
          >
            {remove.isPending && <Loader2Icon className="animate-spin" />}
            删除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
