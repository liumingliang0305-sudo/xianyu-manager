import { Loader2Icon } from "lucide-react"
import { useState, type FormEvent } from "react"
import { toast } from "sonner"

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
import { errorMessage } from "@/lib/api"

import { useImportSource } from "./queries"

export function ImportSourceDialog({
  onClose,
  onImported,
}: {
  onClose: () => void
  onImported: (id: string) => void
}) {
  const [url, setUrl] = useState("")
  const [error, setError] = useState("")
  const importing = useImportSource()
  const submit = (event: FormEvent) => {
    event.preventDefault()
    if (!url.trim() || importing.isPending) return
    setError("")
    importing.mutate(url.trim(), {
      onSuccess: ({ source, added }) => {
        toast.success(
          `${added ? "已保存" : "已更新"}货源：${source.content_length} 字，${source.image_count - source.missing_images} / ${source.image_count} 张图片已下载`,
        )
        onImported(source.source_id)
      },
      onError: (err) => setError(errorMessage(err)),
    })
  }
  return (
    <Dialog open onOpenChange={(open) => !open && !importing.isPending && onClose()}>
      <DialogContent className="sm:max-w-lg" showCloseButton={!importing.isPending}>
        <form onSubmit={submit} className="flex flex-col gap-5">
          <DialogHeader>
            <DialogTitle>添加第三方货源</DialogTitle>
            <DialogDescription>保存公开网页的文字和图片，名下所有闲鱼账号共用。</DialogDescription>
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
                setError("")
              }}
              placeholder="https://www.jianpian.cn/a/…"
              autoComplete="off"
              spellCheck={false}
              disabled={importing.isPending}
              aria-describedby={`source-url-hint${error ? " source-url-error" : ""}`}
              aria-invalid={!!error}
            />
            <p id="source-url-hint" className="text-xs text-muted-foreground">
              支持简篇及普通公开图文网页；需要登录或仅在 App
              内可见的内容可能无法提取。同一链接再次导入会更新。
            </p>
            {error && (
              <p id="source-url-error" role="alert" className="text-sm text-destructive">
                {error}
              </p>
            )}
            <p aria-live="polite" className="text-xs text-muted-foreground">
              {importing.isPending
                ? "正在提取正文并下载图片，图片多时需要稍等…"
                : "图片会下载到后台，保存后可在 AI 新对话里选择。"}
            </p>
          </div>
          <DialogFooter>
            <Button
              type="button"
              variant="outline"
              onClick={onClose}
              disabled={importing.isPending}
            >
              取消
            </Button>
            <Button type="submit" disabled={!url.trim() || importing.isPending}>
              {importing.isPending && <Loader2Icon className="animate-spin" />}
              {importing.isPending ? "提取保存中…" : "提取并保存"}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
