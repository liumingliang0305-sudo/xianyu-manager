import { ExternalLinkIcon, Loader2Icon, RefreshCwIcon } from "lucide-react"
import { toast } from "sonner"

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
import { useExtension } from "@/lib/extension"
import { count, itemState, itemUrl, yuan } from "@/lib/format"

import { ItemThumb, StateBadge } from "./item-bits"
import { OpsList } from "./ops-list"
import { statsErrorMessage, useItemDetail, useItemOps, useRefreshItemStats } from "./queries"

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-lg bg-muted/60 px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="font-heading text-base font-semibold tabular-nums">{value}</div>
    </div>
  )
}

/** 浏览、收藏从哪来的说明，装了浏览器插件的带一个刷新按钮 */
function StatsSource({
  extension,
  refreshing,
  onRefresh,
}: {
  extension: boolean
  refreshing: boolean
  onRefresh: () => void
}) {
  let text = "浏览、收藏不随同步更新，由插件在你的 Chrome 里打开一次这件商品的闲鱼页面拿"
  if (refreshing) text = "正在从你的 Chrome 里拿想要、浏览、收藏，一件几秒…"
  else if (!extension)
    text =
      "浏览、收藏不随同步更新。装上浏览器插件（0.4.0 以上）、在插件里填好这个后台的地址，就能从你的 Chrome 里拿"
  return (
    <div className="flex flex-wrap items-center gap-x-3 gap-y-2 rounded-lg border border-dashed px-3 py-2.5">
      <p className="min-w-0 flex-1 text-xs text-muted-foreground" aria-live="polite">
        {text}
      </p>
      {extension && (
        <Button variant="outline" size="sm" onClick={onRefresh} disabled={refreshing}>
          {refreshing ? <Loader2Icon className="animate-spin" /> : <RefreshCwIcon />}
          {refreshing ? "刷新中…" : "刷新浏览/收藏"}
        </Button>
      )}
    </div>
  )
}

/**
 * 商品详情：价格、想要浏览收藏是本地存的；描述、全部图片、库存打开时从闲鱼拉一次（后台用卖家自己的
 * 编辑页接口）。不调买家看的商品详情接口：后台调它会被闲鱼要求验证、账号熔断，只有它里面有的浏览、
 * 收藏由浏览器插件在 Chrome 里拿。下面附本系统对这件商品做过的改动。
 */
export function ItemDetailSheet({
  account,
  item,
  onClose,
}: {
  account: string
  item: Item
  onClose: () => void
}) {
  // 已经不在主页上的商品多半已经删了，不去请求
  const live = item.on_platform
  const detail = useItemDetail(account, item.item_id, live)
  const ops = useItemOps(account, item.item_id)
  const extension = useExtension() !== null
  const stats = useRefreshItemStats(account)
  const d = detail.data
  // 多规格商品表单里的价格不一定是主页上显示的那个，用本地的
  const price = d && !d.multi_sku && d.price_cent > 0 ? d.price_cent : item.price_cent

  const refreshStats = () =>
    stats.mutate(item.item_id, {
      onSuccess: () => toast.success("想要、浏览、收藏已刷新"),
      onError: (error) =>
        toast.error(`浏览、收藏没刷新成：${statsErrorMessage(error)}`, { duration: 8000 }),
    })

  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent className="w-full gap-0 overflow-y-auto sm:max-w-lg">
        <SheetHeader className="border-b">
          <div className="flex items-center gap-2">
            <StateBadge state={itemState(item)} />
            <span className="font-mono text-xs text-muted-foreground" translate="no">
              {item.item_id}
            </span>
          </div>
          <SheetTitle className="pr-6 leading-snug">{item.title}</SheetTitle>
          <SheetDescription className="sr-only">商品详情</SheetDescription>
        </SheetHeader>

        <div className="flex flex-col gap-5 p-4">
          {!live && (
            <p className="text-muted-foreground">
              这个商品上次同步时已经不在主页上了（可能被下架或删除），只显示本地记录。
            </p>
          )}

          {live && detail.isPending && <Skeleton className="h-[88px] w-full" />}
          {d && d.image_urls.length > 0 && (
            <div className="flex gap-2 overflow-x-auto overscroll-contain pb-1">
              {d.image_urls.map((url) => (
                <a key={url} href={url.replace(/^http:/, "https:")} target="_blank" rel="noreferrer">
                  <ItemThumb url={url} size={88} />
                </a>
              ))}
            </div>
          )}

          <div className="flex items-baseline gap-2">
            <span className="font-heading text-2xl font-semibold tabular-nums">{yuan(price)}</span>
            {d && d.orig_price_cent > 0 && (
              <span className="text-muted-foreground tabular-nums line-through">
                {yuan(d.orig_price_cent)}
              </span>
            )}
            {d && (
              <span className="ml-auto text-xs text-muted-foreground tabular-nums">
                库存 {count(d.quantity)}
                {d.multi_sku && " · 多规格"}
              </span>
            )}
          </div>

          <div className="flex flex-col gap-2">
            <div className="grid grid-cols-3 gap-2">
              <Stat label="想要" value={count(item.want_count)} />
              <Stat label="浏览" value={count(item.browse_count)} />
              <Stat label="收藏" value={count(item.collect_count)} />
            </div>
            {live && (
              <StatsSource extension={extension} refreshing={stats.isPending} onRefresh={refreshStats} />
            )}
          </div>

          {live && (
            <div>
              <h3 className="mb-1.5 text-xs font-medium text-muted-foreground">描述</h3>
              {detail.isPending && <Skeleton className="h-24 w-full" />}
              {detail.isError && (
                <p className="text-sm text-destructive" role="alert">
                  描述和图片没拉到：{errorMessage(detail.error)}
                </p>
              )}
              {d && (
                <p className="text-sm leading-relaxed break-words whitespace-pre-wrap">
                  {d.desc || "-"}
                </p>
              )}
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
