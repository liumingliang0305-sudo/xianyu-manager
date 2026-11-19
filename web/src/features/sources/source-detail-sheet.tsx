import {
  BotIcon,
  CopyIcon,
  ExternalLinkIcon,
  ImageDownIcon,
  Loader2Icon,
  SquarePenIcon,
} from "lucide-react"
import { useState, type ReactNode } from "react"
import { toast } from "sonner"

import { Callout } from "@/components/callout"
import { LoadError, RowsSkeleton } from "@/components/states"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useUrlParam } from "@/hooks/use-url-param"
import type { SourceImage, SourceSummary } from "@/lib/api"
import { count, dateTime } from "@/lib/format"

import { useSource } from "./queries"
import { SourcePhoto } from "./source-photo"

function Field({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="grid grid-cols-[4.5rem_1fr] gap-3">
      <dt className="text-muted-foreground">{label}</dt>
      <dd className="min-w-0 wrap-anywhere">{children}</dd>
    </div>
  )
}

export function SourceDetailSheet({
  sourceId,
  summary,
  onClose,
  onChat,
  chatting,
  onDraft,
  drafting,
  canDraft,
  onFetchImages,
  fetching,
}: {
  sourceId: string
  summary?: SourceSummary
  onClose: () => void
  onChat: () => void
  chatting: boolean
  onDraft: () => void
  drafting: boolean
  canDraft: boolean
  onFetchImages: () => void
  fetching: boolean
}) {
  const detail = useSource(sourceId)
  const source = detail.data
  const [view, setView] = useUrlParam("media", "text", ["text", "images"] as const)
  const [preview, setPreview] = useState<{ image: SourceImage; index: number } | null>(null)
  const copy = () => {
    if (!source) return
    navigator.clipboard.writeText(`${source.title}\n\n${source.content}`).then(
      () => toast.success("已复制正文"),
      () => toast.error("复制失败，请手动选择正文复制"),
    )
  }
  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full gap-0 overflow-y-auto overscroll-contain data-[side=right]:sm:max-w-lg">
        <SheetHeader className="border-b">
          <SheetTitle className="pr-6 leading-snug wrap-anywhere">
            {source?.title ?? summary?.title ?? "第三方货源"}
          </SheetTitle>
          <SheetDescription>
            {source?.platform ?? summary?.platform} ·{" "}
            {source?.author || summary?.author || "公开网页"}
          </SheetDescription>
        </SheetHeader>
        {detail.isPending && <RowsSkeleton rows={5} />}
        {detail.isError && (
          <LoadError
            what="读取第三方货源"
            error={detail.error}
            onRetry={() => void detail.refetch()}
          />
        )}
        {source && (
          <div className="flex flex-col gap-5 p-4">
            <div className="flex flex-wrap gap-2">
              <Button onClick={onChat} disabled={chatting}>
                {chatting ? <Loader2Icon className="animate-spin" /> : <BotIcon />}带入 AI 对话
              </Button>
              <Button variant="outline" onClick={onDraft} disabled={drafting || !canDraft}>
                {drafting ? <Loader2Icon className="animate-spin" /> : <SquarePenIcon />}建草稿
              </Button>
              <Button variant="outline" asChild>
                <a href={source.url} target="_blank" rel="noreferrer">
                  <ExternalLinkIcon />
                  原网页
                </a>
              </Button>
            </div>
            {!canDraft && (
              <p className="-mt-3 text-xs text-muted-foreground">
                添加闲鱼账号后可建发布草稿，保存货源和 AI 对话可以直接使用。
              </p>
            )}
            <dl
              aria-label="货源来源"
              className="flex flex-col gap-2 rounded-lg border p-3 text-[13px]"
            >
              <Field label="网站">
                {source.platform}
                <span className="ml-2 text-muted-foreground">{source.host}</span>
              </Field>
              <Field label="解析方案">{source.parser_name}</Field>
              {source.author && <Field label="作者">{source.author}</Field>}
              <Field label="原网页">
                <a
                  href={source.url}
                  target="_blank"
                  rel="noreferrer"
                  className="text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                >
                  {source.url}
                </a>
              </Field>
            </dl>
            {source.missing_images > 0 && (
              <Callout
                tone="warning"
                title={`还有 ${source.missing_images} 张图片未下载`}
                action={
                  <Button variant="outline" size="sm" disabled={fetching} onClick={onFetchImages}>
                    {fetching ? <Loader2Icon className="animate-spin" /> : <ImageDownIcon />}
                    重新下载
                  </Button>
                }
              >
                正文已经保存，可继续用于 AI 对话。
              </Callout>
            )}
            <Tabs
              value={view}
              onValueChange={(value) => setView(value as "text" | "images")}
              className="gap-4"
            >
              <TabsList
                variant="line"
                className="w-full justify-start border-b pb-1"
                aria-label="货源内容"
              >
                <TabsTrigger value="text" className="flex-none px-3">
                  正文 <span className="tabular-nums">{count(source.content_length)} 字</span>
                </TabsTrigger>
                <TabsTrigger value="images" className="flex-none px-3">
                  图片 <span className="tabular-nums">{count(source.image_count)} 张</span>
                </TabsTrigger>
              </TabsList>
              <TabsContent value="text" className="flex flex-col gap-3">
                <h3 className="sr-only">货源正文</h3>
                <div className="flex justify-end">
                  <Button variant="ghost" size="sm" onClick={copy}>
                    <CopyIcon />
                    复制正文
                  </Button>
                </div>
                {source.content ? (
                  <p className="text-sm leading-6 whitespace-pre-wrap wrap-anywhere">
                    {source.content}
                  </p>
                ) : (
                  <p className="text-sm text-muted-foreground">原网页没有提供文字正文。</p>
                )}
              </TabsContent>
              <TabsContent value="images" className="flex flex-col gap-3">
                <h3 className="text-sm font-medium tabular-nums">
                  {source.image_count - source.missing_images} / {source.image_count} 张已保存
                </h3>
                <div className="grid grid-cols-3 gap-2">
                  {source.images.map((image, index) => (
                    <button
                      key={image.url}
                      type="button"
                      disabled={!image.sha}
                      onClick={() => setPreview({ image, index })}
                      aria-label={`查看第 ${index + 1} 张图片${image.sha ? "" : "（未下载）"}`}
                      className="flex flex-col items-center gap-1 rounded-md outline-none focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-60"
                    >
                      <SourcePhoto sourceId={sourceId} sha={image.sha} size={136} />
                      {!image.sha && <span className="text-xs text-muted-foreground">未下载</span>}
                    </button>
                  ))}
                </div>
                {source.images.length === 0 && (
                  <p className="text-sm text-muted-foreground">没有提取到内容图片。</p>
                )}
              </TabsContent>
            </Tabs>
            <p className="border-t pt-4 text-xs text-muted-foreground">
              首次保存 {dateTime(source.imported_at)}
              <br />
              最近更新 {dateTime(source.updated_at)}
            </p>
          </div>
        )}
        {preview && (
          <Dialog open onOpenChange={(open) => !open && setPreview(null)}>
            <DialogContent className="sm:max-w-3xl">
              <DialogHeader>
                <DialogTitle>图片 {preview.index + 1}</DialogTitle>
                <DialogDescription>保存在后台的图片</DialogDescription>
              </DialogHeader>
              <SourcePhoto sourceId={sourceId} sha={preview.image.sha} size={800} full />
            </DialogContent>
          </Dialog>
        )}
      </SheetContent>
    </Sheet>
  )
}
