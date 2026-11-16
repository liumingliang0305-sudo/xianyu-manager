import { CopyIcon, SquarePenIcon } from "lucide-react"
import { toast } from "sonner"

import { LoadError, RowsSkeleton } from "@/components/states"
import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { dateTime } from "@/lib/format"

import { usePromptTemplate } from "./queries"

export function TemplateDetail({
  templateId,
  onClose,
  onEdit,
}: {
  templateId: number
  onClose: () => void
  onEdit: () => void
}) {
  const detail = usePromptTemplate(templateId)
  const template = detail.data
  const copy = async () => {
    if (!template) return
    try {
      await navigator.clipboard.writeText(template.content)
      toast.success("已复制模板内容")
    } catch {
      toast.error("复制失败，请选中文字后复制")
    }
  }
  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full gap-0 data-[side=right]:sm:max-w-lg">
        <SheetHeader className="border-b">
          <SheetTitle className="pr-6 break-words">{template?.name ?? "模板详情"}</SheetTitle>
          <SheetDescription>模板完整内容</SheetDescription>
        </SheetHeader>
        {detail.isPending && <RowsSkeleton rows={4} height="h-12" />}
        {detail.isError && (
          <LoadError what="读取模板" error={detail.error} onRetry={() => void detail.refetch()} />
        )}
        {template && (
          <>
            <div className="flex gap-2 border-b p-4">
              <Button onClick={onEdit}>
                <SquarePenIcon aria-hidden />
                编辑模板
              </Button>
              <Button variant="outline" onClick={() => void copy()}>
                <CopyIcon aria-hidden />
                复制内容
              </Button>
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4 text-sm leading-6 break-words whitespace-pre-wrap">
              {template.content}
            </div>
            <div className="grid gap-1 border-t p-4 text-xs text-muted-foreground tabular-nums">
              <p>创建于 {dateTime(template.created_at)}</p>
              <p>最近修改 {dateTime(template.updated_at)}</p>
            </div>
          </>
        )}
      </SheetContent>
    </Sheet>
  )
}
