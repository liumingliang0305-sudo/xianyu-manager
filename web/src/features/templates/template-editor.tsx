import { Loader2Icon } from "lucide-react"
import { useEffect, useRef, useState, type FormEvent } from "react"
import { toast } from "sonner"

import { LoadError, RowsSkeleton } from "@/components/states"
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
import { errorMessage, type PromptTemplate, type PromptTemplateInput } from "@/lib/api"
import { count } from "@/lib/format"

import { usePromptTemplate, useSavePromptTemplate } from "./queries"

// 和 app/prompt_templates.py 的限制一致。按 Unicode 字符计数，与后端和数据库一致。
const MAX_NAME = 100
const MAX_CONTENT = 20000

function TemplateForm({
  template,
  busy,
  error,
  onChange,
  onDirtyChange,
  onSubmit,
  onClose,
}: {
  template?: PromptTemplate
  busy: boolean
  error: unknown
  onChange: () => void
  onDirtyChange: (dirty: boolean) => void
  onSubmit: (body: PromptTemplateInput) => void
  onClose: () => void
}) {
  const [name, setName] = useState(template?.name ?? "")
  const [content, setContent] = useState(template?.content ?? "")
  const [submitted, setSubmitted] = useState(false)
  const nameInput = useRef<HTMLInputElement>(null)
  const contentInput = useRef<HTMLTextAreaElement>(null)
  const body = { name: name.trim(), content: content.trim() }
  const nameLength = Array.from(body.name).length
  const contentLength = Array.from(body.content).length
  const nameError =
    nameLength > MAX_NAME
      ? `模板名称最多 ${MAX_NAME} 字`
      : submitted && !body.name
        ? "请输入模板名称"
        : ""
  const contentError =
    contentLength > MAX_CONTENT
      ? `模板内容最多 ${count(MAX_CONTENT)} 字`
      : submitted && !body.content
        ? "请输入模板内容"
        : ""
  const dirty = body.name !== (template?.name ?? "") || body.content !== (template?.content ?? "")

  useEffect(() => {
    if (!dirty) return
    const beforeUnload = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ""
    }
    window.addEventListener("beforeunload", beforeUnload)
    return () => window.removeEventListener("beforeunload", beforeUnload)
  }, [dirty])

  const changed = (nextName: string, nextContent: string) => {
    onChange()
    onDirtyChange(
      nextName.trim() !== (template?.name ?? "") ||
        nextContent.trim() !== (template?.content ?? ""),
    )
  }

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (busy) return
    setSubmitted(true)
    if (!body.name || nameLength > MAX_NAME) {
      nameInput.current?.focus()
      return
    }
    if (!body.content || contentLength > MAX_CONTENT) {
      contentInput.current?.focus()
      return
    }
    onSubmit(body)
  }

  return (
    <form noValidate onSubmit={submit} className="grid gap-4">
      <div className="grid gap-2">
        <Label htmlFor="template-name">模板名称</Label>
        <Input
          ref={nameInput}
          id="template-name"
          name="name"
          autoFocus
          autoComplete="off"
          required
          disabled={busy}
          placeholder="例如：家电商品描述…"
          value={name}
          onChange={(event) => {
            setName(event.target.value)
            changed(event.target.value, content)
          }}
          aria-invalid={!!nameError}
          aria-describedby="template-name-hint"
        />
        <p
          id="template-name-hint"
          aria-live="polite"
          className={nameError ? "text-xs text-destructive" : "text-xs text-muted-foreground"}
        >
          {nameError || `最多 ${MAX_NAME} 字`}
        </p>
      </div>
      <div className="grid gap-2">
        <div className="flex items-center justify-between gap-4">
          <Label htmlFor="template-content">模板内容</Label>
          <span className="text-xs text-muted-foreground tabular-nums">
            {count(contentLength)} / {count(MAX_CONTENT)} 字
          </span>
        </div>
        <Textarea
          ref={contentInput}
          id="template-content"
          name="content"
          autoComplete="off"
          required
          disabled={busy}
          spellCheck={false}
          rows={15}
          placeholder="写下文案结构、写作要求或参考示例…"
          value={content}
          onChange={(event) => {
            setContent(event.target.value)
            changed(name, event.target.value)
          }}
          aria-invalid={!!contentError}
          aria-describedby="template-content-hint"
          className="max-h-[50svh] min-h-72 resize-y leading-6"
        />
        <p
          id="template-content-hint"
          aria-live="polite"
          className={contentError ? "text-xs text-destructive" : "text-xs text-muted-foreground"}
        >
          {contentError || "按原文保存，保留段落和换行。"}
        </p>
      </div>
      {error != null && (
        <p role="alert" className="text-sm text-destructive">
          保存失败：{errorMessage(error)}
        </p>
      )}
      <DialogFooter>
        <Button type="button" variant="outline" onClick={onClose} disabled={busy}>
          取消
        </Button>
        <Button type="submit" disabled={busy}>
          {busy && <Loader2Icon className="animate-spin" aria-hidden />}
          {busy ? "保存中…" : "保存模板"}
        </Button>
      </DialogFooter>
    </form>
  )
}

export function TemplateEditor({
  templateId,
  onClose,
}: {
  templateId: number | null
  onClose: () => void
}) {
  const detail = usePromptTemplate(templateId)
  const save = useSavePromptTemplate(templateId)
  const dirty = useRef(false)
  const [discarding, setDiscarding] = useState(false)
  const close = () => {
    if (save.isPending) return
    if (dirty.current) setDiscarding(true)
    else onClose()
  }
  return (
    <>
      <Dialog open onOpenChange={(open) => !open && close()}>
        <DialogContent
          className="sm:max-w-3xl"
          showCloseButton={!save.isPending}
          onInteractOutside={(event) => event.preventDefault()}
        >
          <DialogHeader>
            <DialogTitle>{templateId === null ? "新建模板" : "编辑模板"}</DialogTitle>
            <DialogDescription>保存常用的写作要求、文案结构和参考示例。</DialogDescription>
          </DialogHeader>
          {templateId !== null && detail.isPending ? (
            <RowsSkeleton rows={3} height="h-16" />
          ) : templateId !== null && detail.isError ? (
            <LoadError what="读取模板" error={detail.error} onRetry={() => void detail.refetch()} />
          ) : (
            <TemplateForm
              key={templateId ?? "new"}
              template={detail.data}
              busy={save.isPending}
              error={save.error}
              onChange={save.reset}
              onDirtyChange={(value) => {
                dirty.current = value
              }}
              onClose={close}
              onSubmit={(body) =>
                save.mutate(body, {
                  onSuccess: () => {
                    toast.success(templateId === null ? "已创建模板" : "已保存模板")
                    onClose()
                  },
                })
              }
            />
          )}
        </DialogContent>
      </Dialog>
      <AlertDialog open={discarding} onOpenChange={setDiscarding}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>放弃未保存的修改？</AlertDialogTitle>
            <AlertDialogDescription>
              这次输入的内容还没有保存，关闭后会丢失。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>继续编辑</AlertDialogCancel>
            <AlertDialogAction variant="destructive" onClick={onClose}>
              放弃修改
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  )
}
