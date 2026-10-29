import { ExternalLinkIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Separator } from "@/components/ui/separator"
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from "@/components/ui/sheet"
import { Skeleton } from "@/components/ui/skeleton"
import { errorMessage, type Item } from "@/lib/api"
import { count, dateTime, itemState, itemUrl, yuan } from "@/lib/format"

import { ItemThumb, StateBadge } from "./item-bits"
import { OpsList } from "./ops-list"
import { useItemDetail, useItemOps } from "./queries"

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-muted/60 px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-heading text-base font-semibold tabular-nums">{value}</div>
    </div>
  )
}

/** 商品详情：实时从闲鱼拉，下面附本系统对它做过的改动。 */
export function ItemDetailSheet({
  account,
  item,
  onClose,
}: {
  account: string
  item: Item
  onClose: () => void
}) {
  // 已经不在主页上的商品，详情接口多半拿不到，不去请求
  const live = item.on_platform
  const detail = useItemDetail(account, item.item_id, live)
  const ops = useItemOps(account, item.item_id)
  const d = detail.data

  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full gap-0 overflow-y-auto sm:max-w-lg">
        <SheetHeader className="border-b">
          <div className="flex items-center gap-2">
            <StateBadge state={itemState(item)} />
            <span className="font-mono text-xs text-muted-foreground">{item.item_id}</span>
          </div>
          <SheetTitle className="pr-6 leading-snug">{d?.title ?? item.title}</SheetTitle>
          <SheetDescription className="sr-only">商品详情</SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-5 p-4">
          {live && detail.isPending && (
            <div className="flex flex-col gap-3">
              <Skeleton className="h-20 w-full" />
              <Skeleton className="h-16 w-full" />
              <Skeleton className="h-40 w-full" />
            </div>
          )}
          {live && detail.isError && (
            <p className="text-destructive">拉取详情失败：{errorMessage(detail.error)}</p>
          )}
          {!live && (
            <p className="text-muted-foreground">
              这个商品上次同步时已经不在主页上了（可能被下架或删除），只显示本地记录。
            </p>
          )}

          {d && (
            <>
              {d.image_urls.length > 0 && (
                <div className="flex gap-2 overflow-x-auto pb-1">
                  {d.image_urls.map((url) => (
                    <a key={url} href={url.replace(/^http:/, "https:")} target="_blank" rel="noreferrer">
                      <ItemThumb url={url} size={88} />
                    </a>
                  ))}
                </div>
              )}

              <div className="flex items-baseline gap-2">
                <span className="font-heading text-2xl font-semibold tabular-nums">
                  {yuan(d.price_cent)}
                </span>
                {d.orig_price_cent > 0 && (
                  <span className="text-muted-foreground tabular-nums line-through">
                    {yuan(d.orig_price_cent)}
                  </span>
                )}
                <span className="ml-auto text-xs text-muted-foreground">
                  库存 {d.quantity} · {d.status_text || "-"}
                </span>
              </div>

              <div className="grid grid-cols-3 gap-2">
                <Stat label="浏览" value={count(d.browse_count)} />
                <Stat label="想要" value={count(d.want_count)} />
                <Stat label="收藏" value={count(d.collect_count)} />
              </div>

              <div>
                <h3 className="mb-1.5 text-xs font-medium text-muted-foreground">描述</h3>
                <p className="text-sm leading-relaxed whitespace-pre-wrap">{d.desc}</p>
              </div>

              <p className="text-xs text-muted-foreground">发布于 {dateTime(d.created_at)}</p>
            </>
          )}

          {!d && !live && (
            <div className="grid grid-cols-3 gap-2">
              <Stat label="售价" value={yuan(item.price_cent)} />
              <Stat label="想要" value={count(item.want_count)} />
              <Stat label="浏览" value={count(item.browse_count)} />
            </div>
          )}

          <Button variant="outline" asChild className="self-start">
            <a href={itemUrl(item.item_id)} target="_blank" rel="noreferrer">
              <ExternalLinkIcon />
              在闲鱼打开
            </a>
          </Button>

          <Separator />

          <div>
            <h3 className="mb-1 text-xs font-medium text-muted-foreground">本系统的改动记录</h3>
            {ops.data ? (
              <OpsList ops={ops.data} />
            ) : (
              <Skeleton className="h-12 w-full" />
            )}
          </div>
        </div>
      </SheetContent>
    </Sheet>
  )
}
