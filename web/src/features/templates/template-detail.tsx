import { BotIcon, CopyIcon, Loader2Icon, SquarePenIcon } from "lucide-react"
import { useNavigate } from "react-router"
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
import { useCreateAgentChat } from "@/features/agent/queries"
import { errorMessage } from "@/lib/api"
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
  const navigate = useNavigate()
  const createChat = useCreateAgentChat()
  const chat = () => {
    if (!template || createChat.isPending) return
    createChat.mutate(
      { template_id: template.id },
      {
        onSuccess: (result) => navigate(`/agent?c=${result.id}`),
        onError: (error) => toast.error(`创建对话失败：${errorMessage(error)}`),
      },
    )
  }
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
              <Button variant="outline" onClick={chat} disabled={createChat.isPending}>
                {createChat.isPending ? <Loader2Icon className="animate-spin" aria-hidden /> : <BotIcon aria-hidden />}
                开 AI 对话
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
