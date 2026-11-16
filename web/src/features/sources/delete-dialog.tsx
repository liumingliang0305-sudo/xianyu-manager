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
import { errorMessage, type SourceSummary } from "@/lib/api"

import { useDeleteSource } from "./queries"

export function DeleteSourceDialog({
  source,
  onClose,
  onDeleted,
}: {
  source: SourceSummary
  onClose: () => void
  onDeleted: () => void
}) {
  const remove = useDeleteSource()
  return (
    <AlertDialog open onOpenChange={(open) => !open && !remove.isPending && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除这个第三方货源？</AlertDialogTitle>
          <AlertDialogDescription>
            <span className="mb-2 block font-medium text-foreground">{source.title}</span>
            删除保存的文字和仅该货源使用的图片。已建好的草稿和 AI
            聊天记录保留，后续对话会提示货源已删除。原网页不受影响。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={remove.isPending}>取消</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={remove.isPending}
            onClick={(event) => {
              event.preventDefault()
              remove.mutate(source.source_id, {
                onSuccess: () => {
                  toast.success("已删除货源")
                  onDeleted()
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
