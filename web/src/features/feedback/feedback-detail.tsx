import { CircleCheckIcon, ExternalLinkIcon, Loader2Icon, RotateCcwIcon, SendIcon } from "lucide-react"
import type { ReactNode } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { errorMessage, type Feedback } from "@/lib/api"
import { dateTime } from "@/lib/format"

import { FeedbackImageLink } from "./feedback-image"
import { FeedbackStatusPill } from "./feedback-status"
import { KIND_LABEL, useSetFeedbackStatus } from "./queries"

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[72px_1fr] gap-3">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 break-words">{children}</dd>
    </div>
  )
}

export function FeedbackDetail({
  feedback,
  isAdmin,
  onClose,
  onPush,
}: {
  feedback: Feedback
  isAdmin: boolean
  onClose: () => void
  onPush: () => void
}) {
  const setStatus = useSetFeedbackStatus()
  const done = feedback.status === "done"
  const toggle = () =>
    setStatus.mutate(
      { id: feedback.id, status: done ? "open" : "done" },
      {
        onSuccess: () => toast.success(done ? "已重新打开" : "已标记为处理完"),
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full gap-0 data-[side=right]:sm:max-w-lg">
        <SheetHeader className="border-b">
          <SheetTitle className="pr-6 break-words">{feedback.title}</SheetTitle>
          <SheetDescription>
            {KIND_LABEL[feedback.kind]} · {dateTime(feedback.created_at)}
          </SheetDescription>
        </SheetHeader>
        {(isAdmin || feedback.issue_url) && (
          <div className="flex flex-wrap gap-2 border-b p-4">
            {feedback.issue_url ? (
              <Button variant={isAdmin ? "outline" : "default"} asChild>
                <a href={feedback.issue_url} target="_blank" rel="noreferrer">
                  <ExternalLinkIcon aria-hidden />
                  GitHub #{feedback.issue_number}
                </a>
              </Button>
            ) : (
              <Button onClick={onPush}>
                <SendIcon aria-hidden />
                推到 GitHub
              </Button>
            )}
            {isAdmin && (
              <Button variant="outline" onClick={toggle} disabled={setStatus.isPending}>
                {setStatus.isPending ? (
                  <Loader2Icon className="animate-spin" aria-hidden />
                ) : done ? (
                  <RotateCcwIcon aria-hidden />
                ) : (
                  <CircleCheckIcon aria-hidden />
                )}
                {done ? "重新打开" : "标记已处理"}
              </Button>
            )}
          </div>
        )}
        <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain p-4">
          <p className="text-sm leading-6 break-words whitespace-pre-wrap">{feedback.content}</p>
          {feedback.images.length > 0 && (
            <ul aria-label="截图" className="mt-4 flex flex-wrap gap-2">
              {feedback.images.map((sha, i) => (
                <li key={sha}>
                  <FeedbackImageLink feedbackId={feedback.id} sha={sha} index={i + 1} />
                </li>
              ))}
            </ul>
          )}
        </div>
        <dl className="grid gap-2 border-t p-4 text-xs tabular-nums">
          <Field label="状态">
            <FeedbackStatusPill status={feedback.status} isAdmin={isAdmin} />
          </Field>
          {isAdmin && <Field label="提交人">{feedback.username}</Field>}
          <Field label="页面">
            {feedback.page ? <code className="font-mono">{feedback.page}</code> : "-"}
          </Field>
          <Field label="后台版本">
            {feedback.revision ? <code className="font-mono">{feedback.revision}</code> : "-"}
          </Field>
          <Field label="浏览器">
            <span className="text-muted-foreground">{feedback.user_agent || "-"}</span>
          </Field>
          {feedback.pushed_at && <Field label="推送于">{dateTime(feedback.pushed_at)}</Field>}
          {feedback.updated_at !== feedback.created_at && (
            <Field label="最近变动">{dateTime(feedback.updated_at)}</Field>
          )}
        </dl>
      </SheetContent>
    </Sheet>
  )
}
