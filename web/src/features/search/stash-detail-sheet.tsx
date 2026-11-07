import {
  BookmarkXIcon,
  CopyIcon,
  ExternalLinkIcon,
  ImageOffIcon,
  Loader2Icon,
  RefreshCwIcon,
} from "lucide-react"
import { useState, type ReactNode } from "react"
import { toast } from "sonner"

import { StatusPill } from "@/components/status-pill"
import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import type { StashItem } from "@/lib/api"
import { count, dateTime, thumb, yuan } from "@/lib/format"
import { cn } from "@/lib/utils"

function Block({ title, aside, children }: { title: string; aside?: ReactNode; children: ReactNode }) {
  return (
    <section>
      <div className="mb-1.5 flex items-baseline justify-between gap-3">
        <h3 className="text-xs font-medium text-muted-foreground">{title}</h3>
        {aside && <span className="text-xs text-muted-foreground">{aside}</span>}
      </div>
      {children}
    </section>
  )
}

/** 一张图，取不到时显示占位 */
function Picture({ url, size, className }: { url: string; size: number; className?: string }) {
  const [failed, setFailed] = useState(false)
  if (failed)
    return (
      <div className={cn("flex items-center justify-center bg-muted text-muted-foreground", className)}>
        <ImageOffIcon className="size-5" />
      </div>
    )
  return (
    <img
      src={thumb(url, size)}
      alt=""
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      className={cn("bg-muted object-contain", className)}
    />
  )
}

/** 大图 + 下面一排缩略图，点缩略图换大图 */
function Gallery({ images }: { images: string[] }) {
  const [index, setIndex] = useState(0)
  const current = images[Math.min(index, images.length - 1)]
  return (
    <div className="flex flex-col gap-2">
      <Picture
        key={current}
        url={current}
        size={960}
        className="aspect-square w-full rounded-lg border"
      />
      <div className="flex items-center justify-between gap-3 text-xs text-muted-foreground">
        <span className="tabular-nums">
          {images.length > 1 ? `第 ${index + 1} / ${images.length} 张` : "1 张图"}
        </span>
        <a
          href={thumb(current, 1200)}
          target="_blank"
          rel="noreferrer"
          className="rounded-sm underline-offset-4 outline-none hover:text-foreground hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          看大图
        </a>
      </div>
      {images.length > 1 && (
        <div className="flex gap-1.5 overflow-x-auto overscroll-contain pb-1">
          {images.map((url, i) => (
            <button
              key={url}
              type="button"
              onClick={() => setIndex(i)}
              aria-label={`看第 ${i + 1} 张`}
              aria-current={i === index ? "true" : undefined}
              className={cn(
                "shrink-0 rounded-md outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                i === index ? "ring-2 ring-foreground" : "opacity-70 hover:opacity-100",
              )}
            >
              <Picture url={url} size={112} className="size-14 rounded-md" />
            </button>
          ))}
        </div>
      )}
    </div>
  )
}

function Stat({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-xs text-muted-foreground">{label}</dt>
      <dd className="font-medium tabular-nums">{value}</dd>
    </div>
  )
}

function copy(text: string, what: string) {
  navigator.clipboard.writeText(text).then(
    () => toast.success(`已复制${what}`),
    () => toast.error(`复制失败（浏览器没给剪贴板权限），选中${what}手动复制`),
  )
}

/** 没有详情时的说明：在拿、能拿（装了插件）、拿不了（没装插件）三种 */
function NoDetail({
  extension,
  fetching,
  onFetch,
}: {
  extension: boolean
  fetching: boolean
  onFetch: () => void
}) {
  let text = "只有搜到时的样子。完整描述和全部图片从你的 Chrome 里拿：插件会在后台打开一次这件商品的闲鱼页面，拿完就关"
  if (fetching) text = "正在从你的 Chrome 里拿完整描述和全部图片，一件几秒…"
  else if (!extension)
    text = "只有搜到时的样子。装上浏览器插件（0.4.0 以上）、在插件里填好这个后台的地址，就能从你的 Chrome 里拿完整描述和全部图片"
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-dashed px-3 py-2.5">
      <p className="min-w-0 flex-1 text-sm text-muted-foreground" aria-live="polite">
        {text}
      </p>
      {extension && (
        <Button size="sm" onClick={onFetch} disabled={fetching}>
          {fetching ? <Loader2Icon className="animate-spin" /> : <RefreshCwIcon />}
          {fetching ? "拿详情中…" : "拉详情"}
        </Button>
      )}
    </div>
  )
}

/**
 * 暂存商品的详情：图片、价格、想要浏览收藏、描述、标签，都是存在本地的，打开不请求闲鱼。
 * 有详情（浏览器插件拿来的）显示全部图片和完整描述；没有的只有搜到时的样子
 * （一张图、标题，不少卖家把描述写进了标题），装了插件能拉。
 */
export function StashDetailSheet({
  item,
  extension,
  fetching,
  onFetch,
  onClose,
  onRemove,
}: {
  item: StashItem
  /** 装了能拿详情的浏览器插件 */
  extension: boolean
  /** 正在请插件拿这一件的详情 */
  fetching: boolean
  onFetch: () => void
  onClose: () => void
  onRemove: () => void
}) {
  const hasDetail = item.detail_at != null
  const images = item.images.length > 0 ? item.images : item.pic_url ? [item.pic_url] : []
  const tags = [item.free_ship ? "包邮" : "", ...item.tags].filter(Boolean)
  const seller = [item.area, ...item.seller_tags].filter(Boolean).join(" · ")

  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      {/* 基础样式里是 data-[side=right]:sm:max-w-sm，要用同样的写法才盖得住（设计规范：抽屉最宽 512px） */}
      <SheetContent className="w-full gap-0 overflow-y-auto overscroll-contain data-[side=right]:sm:max-w-lg">
        <SheetHeader className="border-b">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="font-mono" translate="no">
              {item.item_id}
            </span>
            {item.status === 0 && <StatusPill tone="positive">在售</StatusPill>}
            {item.status === 1 && <StatusPill tone="neutral">已卖出</StatusPill>}
            {item.status != null && item.status > 1 && item.status_text && (
              <StatusPill tone="outline">{item.status_text}</StatusPill>
            )}
          </div>
          <SheetTitle className="pr-6 leading-snug">{item.title}</SheetTitle>
          <SheetDescription className="sr-only">暂存商品的详情</SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-5 p-4">
          {!hasDetail && <NoDetail extension={extension} fetching={fetching} onFetch={onFetch} />}

          {images.length > 0 && <Gallery key={`${item.item_id}-${images.length}`} images={images} />}

          <div className="flex items-baseline gap-2">
            <span className="font-heading text-2xl font-semibold whitespace-nowrap tabular-nums">
              {yuan(item.price_cent)}
            </span>
            {item.orig_price_cent != null && (
              <span className="text-sm text-muted-foreground line-through tabular-nums">
                {yuan(item.orig_price_cent)}
              </span>
            )}
            <span className="ml-auto min-w-0 truncate text-right text-sm" translate="no">
              {item.seller_nick || "-"}
            </span>
          </div>
          {seller && <p className="-mt-4 text-right text-xs text-muted-foreground">{seller}</p>}

          <dl
            className={cn(
              "grid gap-3 rounded-lg border px-3 py-2.5 text-sm",
              hasDetail ? "grid-cols-4" : "grid-cols-2",
            )}
          >
            <Stat label="想要" value={count(item.want_count)} />
            {/* 浏览、收藏只有详情里有 */}
            {hasDetail && <Stat label="浏览" value={count(item.browse_count)} />}
            {hasDetail && <Stat label="收藏" value={count(item.collect_count)} />}
            <Stat
              label="发布"
              value={item.published_at ? dateTime(item.published_at).split(" ")[0] : "-"}
            />
          </dl>

          <div className="flex flex-wrap gap-2">
            {item.description ? (
              <Button size="sm" onClick={() => copy(item.description, "描述")}>
                <CopyIcon />
                复制描述
              </Button>
            ) : (
              <Button variant="outline" size="sm" onClick={() => copy(item.title, "标题")}>
                <CopyIcon />
                复制标题
              </Button>
            )}
            <Button variant="outline" size="sm" asChild>
              <a href={item.url} target="_blank" rel="noreferrer">
                <ExternalLinkIcon />
                在闲鱼打开
              </a>
            </Button>
            {hasDetail && extension && (
              <Button variant="outline" size="sm" onClick={onFetch} disabled={fetching}>
                {fetching ? <Loader2Icon className="animate-spin" /> : <RefreshCwIcon />}
                {fetching ? "拿详情中…" : "重新拉详情"}
              </Button>
            )}
            <Button variant="ghost" size="sm" onClick={onRemove} className="ml-auto text-destructive">
              <BookmarkXIcon />
              移出暂存
            </Button>
          </div>

          {hasDetail && (
            <Block title="描述" aside={`${count(item.description.length)} 字`}>
              {item.description ? (
                <p className="text-sm leading-relaxed break-words whitespace-pre-wrap">
                  {item.description}
                </p>
              ) : (
                <p className="text-sm text-muted-foreground">卖家没写描述</p>
              )}
            </Block>
          )}

          {tags.length > 0 && (
            <Block title="标签">
              <div className="flex flex-wrap gap-1.5">
                {tags.map((tag) => (
                  <StatusPill key={tag} tone="neutral">
                    {tag}
                  </StatusPill>
                ))}
              </div>
            </Block>
          )}

          <Separator />
          <p className="text-xs text-muted-foreground">
            {item.keyword && `搜「${item.keyword}」时暂存 · `}
            暂存于 {dateTime(item.saved_at)}
            {hasDetail && ` · 详情拿于 ${dateTime(item.detail_at)}`}
            。价格、想要这些是那时候的
          </p>
        </div>
      </SheetContent>
    </Sheet>
  )
}
