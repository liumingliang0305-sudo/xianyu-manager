import { Loader2Icon } from "lucide-react"
import { useEffect, useRef, useState, type FormEvent } from "react"
import { useNavigate } from "react-router"
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
import { errorMessage, type FeedbackKind } from "@/lib/api"
import { count } from "@/lib/format"
import { cn } from "@/lib/utils"

import { KIND_LABEL, useCreateFeedback } from "./queries"

// 和 app/feedback.py 的限制一致，按 Unicode 字符计数
const MAX_TITLE = 100
const MAX_CONTENT = 5000

const KIND_HINT: Record<FeedbackKind, string> = {
  bug: "报错了、和预期不一样",
  idea: "想要的功能、不顺手的地方",
  other: "别的想说的",
}

const CONTENT_PLACEHOLDER: Record<FeedbackKind, string> = {
  bug: "做了什么、看到了什么、本来应该是什么样。有报错的话把提示原样贴上…",
  idea: "想在哪个页面做什么，现在是怎么做的…",
  other: "写下想说的…",
}

/**
 * 提交问题反馈。`page` 是在哪个页面打开的（前端路由），一起交上去方便排查；
 * 在问题反馈页本身打开时传空串。
 */
export function FeedbackDialog({ page, onClose }: { page: string; onClose: () => void }) {
  const create = useCreateFeedback()
  const navigate = useNavigate()
  const [kind, setKind] = useState<FeedbackKind>("bug")
  const [title, setTitle] = useState("")
  const [content, setContent] = useState("")
  const [submitted, setSubmitted] = useState(false)
  const [discarding, setDiscarding] = useState(false)
  const titleInput = useRef<HTMLInputElement>(null)
  const contentInput = useRef<HTMLTextAreaElement>(null)
  const body = { kind, title: title.trim(), content: content.trim(), page }
  const titleLength = Array.from(body.title).length
  const contentLength = Array.from(body.content).length
  const titleError =
    titleLength > MAX_TITLE
      ? `标题最多 ${MAX_TITLE} 字`
      : submitted && !body.title
        ? "用一句话说说是什么问题"
        : ""
  const contentError =
    contentLength > MAX_CONTENT
      ? `最多 ${count(MAX_CONTENT)} 字`
      : submitted && !body.content
        ? "请写一下具体情况"
        : ""
  const dirty = !!body.title || !!body.content

  useEffect(() => {
    if (!dirty) return
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ""
    }
    window.addEventListener("beforeunload", beforeUnload)
    return () => window.removeEventListener("beforeunload", beforeUnload)
  }, [dirty])

  const close = () => {
    if (create.isPending) return
    if (dirty) setDiscarding(true)
    else onClose()
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (create.isPending) return
    setSubmitted(true)
    if (!body.title || titleLength > MAX_TITLE) {
      titleInput.current?.focus()
      return
    }
    if (!body.content || contentLength > MAX_CONTENT) {
      contentInput.current?.focus()
      return
    }
    create.mutate(body, {
      onSuccess: () => {
        toast.success("已提交，谢谢反馈", {
          action: page ? { label: "查看", onClick: () => navigate("/feedback") } : undefined,
        })
        onClose()
      },
    })
  }

  return (
    <>
      <Dialog open onOpenChange={(open) => !open && close()}>
        <DialogContent
          className="sm:max-w-2xl"
          showCloseButton={!create.isPending}
          onInteractOutside={(event) => event.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>反馈问题</DialogTitle>
            <DialogDescription>
              用着哪里不对、想要什么功能，写下来交给管理员处理。
            </DialogDescription>
          </DialogHeader>
          <form noValidate onSubmit={submit} className="grid gap-4">
            <fieldset className="grid gap-2" disabled={create.isPending}>
              <legend className="mb-2 text-sm leading-none font-medium">种类</legend>
              <div role="radiogroup" aria-label="反馈种类" className="grid grid-cols-3 gap-2">
                {(Object.keys(KIND_LABEL) as FeedbackKind[]).map((value) => (
                  <button
                    key={value}
                    type="button"
                    role="radio"
                    aria-checked={kind === value}
                    onClick={() => {
                      setKind(value)
                      create.reset()
                    }}
                    className={cn(
                      "grid gap-0.5 rounded-lg border px-3 py-2 text-left transition-colors outline-none",
                      "hover:border-input focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50",
                      kind === value && "border-primary ring-1 ring-primary",
                    )}
                  >
                    <span className="text-sm font-medium">{KIND_LABEL[value]}</span>
                    <span className="text-xs text-muted-foreground">{KIND_HINT[value]}</span>
                  </button>
                ))}
              </div>
            </fieldset>
            <div className="grid gap-2">
              <Label htmlFor="feedback-title">标题</Label>
              <Input
                ref={titleInput}
                id="feedback-title"
                name="title"
                autoFocus
                autoComplete="off"
                required
                disabled={create.isPending}
                placeholder="一句话说清楚，例如：订单页点同步一直转圈…"
                value={title}
                onChange={(event) => {
                  setTitle(event.target.value)
                  create.reset()
                }}
                aria-invalid={!!titleError}
                aria-describedby="feedback-title-hint"
              />
              {titleError && (
                <p id="feedback-title-hint" aria-live="polite" className="text-xs text-destructive">
                  {titleError}
                </p>
              )}
            </div>
            <div className="grid gap-2">
              <div className="flex items-center justify-between gap-4">
                <Label htmlFor="feedback-content">具体情况</Label>
                <span className="text-xs text-muted-foreground tabular-nums">
                  {count(contentLength)} / {count(MAX_CONTENT)} 字
                </span>
              </div>
              <Textarea
                ref={contentInput}
                id="feedback-content"
                name="content"
                autoComplete="off"
                required
                disabled={create.isPending}
                rows={8}
                placeholder={CONTENT_PLACEHOLDER[kind]}
                value={content}
                onChange={(event) => {
                  setContent(event.target.value)
                  create.reset()
                }}
                aria-invalid={!!contentError}
                aria-describedby="feedback-content-hint"
                className="max-h-[50svh] min-h-44 resize-y leading-6"
              />
              <p
                id="feedback-content-hint"
                aria-live="polite"
                className={contentError ? "text-xs text-destructive" : "text-xs text-muted-foreground"}
              >
                {contentError ||
                  (page
                    ? `会一起记下所在页面（${page}）和浏览器版本，方便排查。`
                    : "会一起记下浏览器版本，方便排查。")}
              </p>
            </div>
            {create.error != null && (
              <p role="alert" className="text-sm text-destructive">
                提交失败：{errorMessage(create.error)}
              </p>
            )}
            <DialogFooter>
              <Button type="button" variant="outline" onClick={close} disabled={create.isPending}>
                取消
              </Button>
              <Button type="submit" disabled={create.isPending}>
                {create.isPending && <Loader2Icon className="animate-spin" aria-hidden />}
                {create.isPending ? "提交中…" : "提交反馈"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>
      <AlertDialog open={discarding} onOpenChange={setDiscarding}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>放弃这条反馈？</AlertDialogTitle>
            <AlertDialogDescription>写的内容还没有提交，关闭后会丢失。</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>继续写</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={onClose}>
              放弃
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
