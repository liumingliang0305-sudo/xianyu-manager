import { CheckIcon, Loader2Icon } from "lucide-react"
import { useState, type FormEvent } from "react"
import { toast } from "sonner"

import { Callout } from "@/components/callout"
import { LoadError, RowsSkeleton } from "@/components/states"
import { Badge } from "@/components/ui/badge"
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
import { Textarea } from "@/components/ui/textarea"
import { errorMessage, type Feedback, type IssueDraft } from "@/lib/api"
import { cn } from "@/lib/utils"

import { FeedbackImage } from "./feedback-image"
import { useIssueDraft, usePushIssue } from "./queries"

// GitHub issue 标题的上限（app/feedback.py 的 MAX_ISSUE_TITLE）
const MAX_TITLE = 256

function IssueForm({
  feedback,
  draft,
  onClose,
}: {
  feedback: Feedback
  draft: IssueDraft
  onClose: () => void
}) {
  const push = usePushIssue(feedback.id)
  const [title, setTitle] = useState(draft.title)
  const [body, setBody] = useState(draft.body)
  // 一起推的截图，默认全勾
  const [chosen, setChosen] = useState(() => new Set(feedback.images))
  const [submitted, setSubmitted] = useState(false)
  const titleLength = Array.from(title.trim()).length
  const titleError =
    titleLength > MAX_TITLE
      ? `标题最多 ${MAX_TITLE} 字`
      : submitted && !titleLength
        ? "issue 要有标题"
        : ""
  const blocked = !!draft.missing

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (push.isPending || blocked) return
    setSubmitted(true)
    if (!titleLength || titleLength > MAX_TITLE) return
    push.mutate(
      { title, body, images: feedback.images.filter((sha) => chosen.has(sha)) },
      {
        onSuccess: (pushed) => {
          toast.success(`已推到 GitHub：#${pushed.issue_number}`, {
            action: pushed.issue_url
              ? {
                  label: "打开",
                  onClick: () => window.open(pushed.issue_url!, "_blank", "noopener,noreferrer"),
                }
              : undefined,
          })
          onClose()
        },
      },
    )
  }

  return (
    <form noValidate onSubmit={submit} className="grid gap-4">
      {blocked ? (
        <Callout tone="negative" title="还推不了 GitHub">
          <p>{draft.missing}。配好后重启后台再推。</p>
        </Callout>
      ) : (
        <Callout tone="warning">
          公开仓库的 issue 谁都看得到。推送前确认标题、正文
          {feedback.images.length > 0 && "和勾上的截图"}
          里没有手机号、订单号、闲鱼账号这类信息。
        </Callout>
      )}
      <div className="grid gap-2">
        <Label htmlFor="issue-title">issue 标题</Label>
        <Input
          id="issue-title"
          name="title"
          autoComplete="off"
          disabled={push.isPending || blocked}
          value={title}
          onChange={(event) => {
            setTitle(event.target.value)
            push.reset()
          }}
          aria-invalid={!!titleError}
        />
        {titleError && (
          <p aria-live="polite" className="text-xs text-destructive">
            {titleError}
          </p>
        )}
      </div>
      <div className="grid gap-2">
        <div className="flex items-center justify-between gap-4">
          <Label htmlFor="issue-body">issue 正文</Label>
          <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
            标签
            {draft.labels.map((label) => (
              <Badge key={label} variant="outline" className="font-mono">
                {label}
              </Badge>
            ))}
          </span>
        </div>
        <Textarea
          id="issue-body"
          name="body"
          autoComplete="off"
          spellCheck={false}
          disabled={push.isPending || blocked}
          rows={14}
          value={body}
          onChange={(event) => {
            setBody(event.target.value)
            push.reset()
          }}
          className="max-h-[50svh] min-h-64 resize-y font-mono text-[13px] leading-5"
        />
        <p className="text-xs text-muted-foreground">
          GitHub 按 Markdown 显示。默认正文带了页面、版本和浏览器，没带提交人。
        </p>
      </div>
      {feedback.images.length > 0 && (
        <fieldset className="grid gap-2" disabled={push.isPending || blocked}>
          <legend className="mb-2 flex w-full items-center justify-between gap-4 text-sm leading-none font-medium">
            一起推的截图
            <span className="text-xs font-normal text-muted-foreground tabular-nums">
              勾了 {chosen.size} / {feedback.images.length} 张
            </span>
          </legend>
          <ul className="flex flex-wrap gap-3">
            {feedback.images.map((sha, i) => {
              const on = chosen.has(sha)
              return (
                <li key={sha}>
                  <label className="group grid cursor-pointer gap-1 text-center text-xs text-muted-foreground has-disabled:cursor-default">
                    <span className="relative">
                      <FeedbackImage
                        feedbackId={feedback.id}
                        sha={sha}
                        alt={`截图 ${i + 1}`}
                        className={cn(
                          "size-24 transition",
                          on ? "border-primary ring-1 ring-primary" : "opacity-45",
                        )}
                      />
                      <input
                        type="checkbox"
                        className="peer sr-only"
                        checked={on}
                        onChange={() => {
                          setChosen((old) => {
                            const next = new Set(old)
                            if (next.has(sha)) next.delete(sha)
                            else next.add(sha)
                            return next
                          })
                          push.reset()
                        }}
                      />
                      <span
                        aria-hidden
                        className={cn(
                          "absolute top-1 left-1 flex size-5 items-center justify-center rounded border bg-background shadow-xs",
                          "peer-focus-visible:ring-3 peer-focus-visible:ring-ring/50",
                          on && "border-primary bg-primary text-primary-foreground",
                        )}
                      >
                        {on && <CheckIcon className="size-3.5" />}
                      </span>
                    </span>
                    <span className={on ? "text-foreground" : undefined}>截图 {i + 1}</span>
                  </label>
                </li>
              )
            })}
          </ul>
          <p className="text-xs text-muted-foreground">
            勾上的截图会传到 GitHub，按顺序接在正文最后。一张要几秒，推送时多等一会儿。
          </p>
        </fieldset>
      )}
      {push.error != null && (
        <p role="alert" className="text-sm text-destructive">
          推送失败：{errorMessage(push.error)}
        </p>
      )}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose} disabled={push.isPending}>
          取消
        </Button>
        <Button type="submit" disabled={push.isPending || blocked}>
          {push.isPending && <Loader2Icon className="animate-spin" aria-hidden />}
          {push.isPending ? "推送中…" : "推到 GitHub"}
        </Button>
      </DialogFooter>
    </form>
  )
}

/** 管理员把一条反馈推成 GitHub issue：先看、改默认的标题和正文，再推 */
export function IssueDialog({ feedback, onClose }: { feedback: Feedback; onClose: () => void }) {
  const draft = useIssueDraft(feedback.id)
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-3xl" onInteractOutside={(event) => event.preventDefault()}>
        <DialogHeader>
          <DialogTitle>推到 GitHub</DialogTitle>
          <DialogDescription>
            {draft.data?.repo
              ? `在 ${draft.data.repo} 建一个 issue，一条反馈只推一次。`
              : "建一个 GitHub issue，一条反馈只推一次。"}
          </DialogDescription>
        </DialogHeader>
        {draft.isPending ? (
          <RowsSkeleton rows={3} height="h-16" />
        ) : draft.isError ? (
          <LoadError
            what="生成 issue 内容"
            error={draft.error}
            onRetry={() => void draft.refetch()}
          />
        ) : (
          <IssueForm feedback={feedback} draft={draft.data} onClose={onClose} />
        )}
      </DialogContent>
    </Dialog>
  )
}
