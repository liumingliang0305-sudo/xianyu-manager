import { Loader2Icon } from "lucide-react"
import { useEffect, useState, type FormEvent, type ReactNode } from "react"
import { toast } from "sonner"

import { NativeSelect } from "@/components/native-select"
import { StatusPill } from "@/components/status-pill"
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
import { errorMessage, type SourceDetection, type SourceParser } from "@/lib/api"

import { useDetectSource, useImportSource, useSourceParsers } from "./queries"

/** 像个完整链接才去识别，免得每敲一个字就请求 */
const LINK = /^https?:\/\/[^\s/]+\.[^\s]+$/i

function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return settled
}

function Field({ label, children }: { label: ReactNode; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[4.5rem_1fr] items-baseline gap-3">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 wrap-anywhere">{children}</dd>
    </div>
  )
}

export function ImportSourceDialog({
  onClose,
  onImported,
}: {
  onClose: () => void
  onImported: (id: string) => void
}) {
  const [url, setUrl] = useState("")
  /** auto：按识别结果；否则是手动选的方案 */
  const [choice, setChoice] = useState("auto")
  const [error, setError] = useState("")
  const trimmed = url.trim()
  const target = useDebounced(LINK.test(trimmed) ? trimmed : "", 400)
  const detection = useDetectSource(target)
  const parsers = useSourceParsers()
  const importing = useImportSource()
  const byId = new Map((parsers.data ?? []).map((parser) => [parser.id, parser]))
  const detected = target === trimmed ? detection.data : undefined
  const effective = byId.get(choice === "auto" ? (detected?.parser ?? "") : choice)

  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!trimmed || importing.isPending) return
    setError("")
    importing.mutate(
      { url: trimmed, parser: choice },
      {
        onSuccess: ({ source, added }) => {
          toast.success(
            `${added ? "已保存" : "已更新"}货源（${source.platform} · ${source.parser_name}）：${source.content_length} 字，${source.image_count - source.missing_images} / ${source.image_count} 张图片已下载`,
          )
          onImported(source.source_id)
        },
        onError: (err) => setError(errorMessage(err)),
      },
    )
  }
  return (
    <Dialog open onOpenChange={(open) => !open && !importing.isPending && onClose()}>
      <DialogContent className="sm:max-w-xl" showCloseButton={!importing.isPending}>
        <form onSubmit={submit} className="flex flex-col gap-5">
          <DialogHeader>
            <DialogTitle>添加第三方货源</DialogTitle>
            <DialogDescription>
              粘贴链接后自动识别网站和解析方案，保存网页的文字和图片，名下所有闲鱼账号共用。
            </DialogDescription>
          </DialogHeader>
          <div className="flex flex-col gap-2">
            <Label htmlFor="source-url">货源链接</Label>
            <Input
              id="source-url"
              name="source-url"
              type="url"
              value={url}
              required
              autoFocus
              onChange={(event) => {
                setUrl(event.target.value)
                setChoice("auto")
                setError("")
              }}
              placeholder="https://…"
              autoComplete="off"
              spellCheck={false}
              disabled={importing.isPending}
              aria-describedby={`source-url-hint${error ? " source-url-error" : ""}`}
              aria-invalid={!!error}
            />
            <p id="source-url-hint" className="text-xs text-muted-foreground">
              支持简篇、美篇、秀米、135编辑器、自我游分销商城（含 s.leleku.cn
              短链接）和普通公开图文网页；同一链接再次导入会更新。
            </p>
          </div>
          {LINK.test(trimmed) && (
            <DetectionPanel
              pending={target !== trimmed || detection.isFetching}
              failure={detection.isError ? errorMessage(detection.error) : ""}
              detection={detected}
              parsers={parsers.data ?? []}
              choice={choice}
              onChoose={(value) => {
                setChoice(value)
                setError("")
              }}
              effective={effective}
              disabled={importing.isPending}
            />
          )}
          {error && (
            <p id="source-url-error" role="alert" className="-mt-2 text-sm text-destructive">
              {error}
            </p>
          )}
          <DialogFooter className="items-center">
            <p aria-live="polite" className="mr-auto text-xs text-muted-foreground">
              {importing.isPending
                ? `正在用「${effective?.name ?? "自动识别的方案"}」提取，图片多时需要稍等…`
                : "图片会下载到后台，保存后可在 AI 新对话里选择。"}
            </p>
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              disabled={importing.isPending}
            >
              取消
            </Button>
            <Button type="submit" disabled={!trimmed || importing.isPending}>
              {importing.isPending && <Loader2Icon className="animate-spin" />}
              {importing.isPending ? "提取保存中…" : "提取并保存"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

function DetectionPanel({
  pending,
  failure,
  detection,
  parsers,
  choice,
  onChoose,
  effective,
  disabled,
}: {
  pending: boolean
  failure: string
  detection: SourceDetection | undefined
  parsers: SourceParser[]
  choice: string
  onChoose: (value: string) => void
  effective: SourceParser | undefined
  disabled: boolean
}) {
  const detectedName = parsers.find((parser) => parser.id === detection?.parser)?.name
  const inputHost = detection ? new URL(detection.input_url).hostname : ""
  const manual = choice !== "auto"
  return (
    <section aria-labelledby="source-detect-title" className="rounded-lg border p-3 text-[13px]">
      <div className="mb-3 flex items-center justify-between gap-3">
        <h3 id="source-detect-title" className="text-sm font-medium">
          识别结果
        </h3>
        <span aria-live="polite" className="flex items-center gap-1.5">
          {pending ? (
            <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
              <Loader2Icon className="size-3.5 animate-spin" />
              正在识别链接…
            </span>
          ) : detection ? (
            <StatusPill tone={manual ? "warning" : "positive"}>
              {manual ? "手动选择方案" : "自动识别"}
            </StatusPill>
          ) : null}
        </span>
      </div>
      {failure && !pending ? (
        <p role="alert" className="text-destructive">
          识别失败：{failure}
        </p>
      ) : (
        <dl className="flex flex-col gap-2">
          <Field label="网站">
            {detection && !pending ? (
              <>
                <span className="font-medium">{detection.site}</span>
                <span className="ml-2 text-muted-foreground">{detection.host}</span>
              </>
            ) : (
              <Placeholder />
            )}
          </Field>
          <Field label="链接">
            {detection && !pending ? (
              <>
                {detection.link}
                {inputHost !== detection.host && (
                  <span className="mt-0.5 block text-xs text-muted-foreground">
                    {inputHost} → {detection.host}
                  </span>
                )}
              </>
            ) : (
              <Placeholder />
            )}
          </Field>
          <Field label="识别方式">
            {detection && !pending ? detection.reason : <Placeholder />}
          </Field>
          <Field label={<label htmlFor="source-parser">解析方案</label>}>
            <NativeSelect
              id="source-parser"
              value={choice}
              onChange={(event) => onChoose(event.target.value)}
              disabled={disabled || parsers.length === 0}
              className="w-full max-w-72"
              aria-describedby="source-parser-summary"
            >
              <option value="auto">
                {detectedName && !pending ? `自动：${detectedName}` : "自动识别"}
              </option>
              {parsers.map((parser) => (
                <option key={parser.id} value={parser.id}>
                  {parser.name}
                </option>
              ))}
            </NativeSelect>
            <p id="source-parser-summary" className="mt-1.5 text-xs text-muted-foreground">
              {effective ? (
                <>
                  适用：{effective.sites}。{effective.summary}。
                </>
              ) : (
                "识别完成后显示这个方案怎么取内容。"
              )}
            </p>
            {manual && detection && !pending && choice !== detection.parser && (
              <p className="mt-1 text-xs text-warning-ink">
                和识别结果「{detectedName}」不同：这个方案在网页里找不到数据时会直接提示失败。
              </p>
            )}
          </Field>
        </dl>
      )}
    </section>
  )
}

function Placeholder() {
  return <span className="inline-block h-3.5 w-32 animate-pulse rounded bg-muted align-middle" />
}
