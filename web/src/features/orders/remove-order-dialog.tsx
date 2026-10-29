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
import { errorMessage, type Order } from "@/lib/api"

import { useRemoveOrder } from "./queries"

export function RemoveOrderDialog({
  account,
  order,
  onClose,
}: {
  account: string
  order: Order
  onClose: () => void
}) {
  const mutation = useRemoveOrder(account)
  return (
    <AlertDialog open onOpenChange={(open) => !open && !mutation.isPending && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>从列表移除这笔订单？</AlertDialogTitle>
          <AlertDialogDescription>
            <span className="mb-2 block font-medium text-foreground">
              {order.title || order.order_id}
            </span>
            只是从本系统的列表里拿掉，闲鱼上的订单不受影响，之后可以按订单号重新添加。改动记录会保留。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={mutation.isPending}>取消</AlertDialogCancel>
          <AlertDialogAction
            disabled={mutation.isPending}
            onClick={(e) => {
              e.preventDefault()
              mutation.mutate(order.order_id, {
                onSuccess: () => {
                  toast.success("已从列表移除")
                  onClose()
                },
                onError: (error) => toast.error(errorMessage(error)),
              })
            }}
          >
            {mutation.isPending && <Loader2Icon className="animate-spin" />}
            移除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
