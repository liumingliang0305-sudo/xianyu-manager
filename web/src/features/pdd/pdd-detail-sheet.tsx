import { ExternalLinkIcon, ImageDownIcon, Loader2Icon, PlayIcon, SquarePenIcon } from "lucide-react"
import type { ReactNode } from "react"

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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { LoadError } from "@/components/states"
import type { PddGoods, PddGoodsSummary } from "@/lib/api"
import { count, dateTime, yuan } from "@/lib/format"

import { PddThumb } from "./pdd-bits"
import { priceRange } from "./pdd-format"
import { usePddGoodsDetail } from "./queries"

function Block({ title, aside, children }: { title: string; aside?: string; children: ReactNode }) {
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

function Body({
  goods,
  onDraft,
  drafting,
  canDraft,
  onFetchImages,
  fetching,
}: {
  goods: PddGoods
  onDraft: () => void
  drafting: boolean
  canDraft: boolean
  onFetchImages: () => void
  fetching: boolean
}) {
  const normal = priceRange(goods.min_normal_price_cent, goods.max_normal_price_cent)
  return (
    <div className="flex flex-col gap-5 p-4">
      {goods.images.length > 0 && (
        <div className="flex gap-2 overflow-x-auto overscroll-contain pb-1">
          {goods.images.map((img) => (
            <a key={img.url} href={img.url} target="_blank" rel="noreferrer" aria-label="看原图">
              <PddThumb url={img.url} size={88} />
            </a>
          ))}
        </div>
      )}

      <div className="flex items-baseline gap-2">
        <span className="font-heading text-2xl font-semibold whitespace-nowrap tabular-nums">
          {priceRange(goods.min_price_cent, goods.max_price_cent)}
        </span>
        <span className="text-xs whitespace-nowrap text-muted-foreground tabular-nums">
          拼单价{normal !== "-" && ` · 单买 ${normal}`}
        </span>
        <span className="ml-auto min-w-0 truncate text-xs text-muted-foreground">
          {goods.mall_name}
        </span>
      </div>

      <div className="flex flex-wrap gap-2">
        <Button onClick={onDraft} disabled={drafting || !canDraft}>
          {drafting ? <Loader2Icon className="animate-spin" /> : <SquarePenIcon />}
          {drafting ? "建草稿中…" : "建发布草稿"}
        </Button>
        <Button variant="outline" asChild>
          <a href={goods.url} target="_blank" rel="noreferrer">
            <ExternalLinkIcon />
            在拼多多打开
          </a>
        </Button>
        {goods.missing_images > 0 && (
          <Button variant="outline" onClick={onFetchImages} disabled={fetching}>
            {fetching ? <Loader2Icon className="animate-spin" /> : <ImageDownIcon />}
            重新下载图片
          </Button>
        )}
      </div>
      <p className="-mt-3 text-xs text-muted-foreground">
        {canDraft
          ? "草稿建在当前账号上：描述是标题、参数和可选规格，图是下面的主图（最多 9 张），价格到发布页填。"
          : "还没有闲鱼账号，先在账号矩阵里添加一个才能建草稿。"}
        {goods.missing_images > 0 && ` 还有 ${goods.missing_images} 张图没下载到后台。`}
      </p>

      {goods.properties.length > 0 && (
        <Block title="商品参数">
          <dl className="grid grid-cols-[auto_1fr] gap-x-4 gap-y-1 text-sm">
            {goods.properties.map((prop) => (
              <div key={prop.key} className="contents">
                <dt className="text-muted-foreground">{prop.key}</dt>
                <dd>{prop.value}</dd>
              </div>
            ))}
          </dl>
        </Block>
      )}

      {goods.skus.length > 0 && (
        <Block title={`规格 ${goods.skus.length} 个`} aside="拼单价 / 单买价 / 库存">
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-full">规格</TableHead>
                  <TableHead className="text-right">拼单</TableHead>
                  <TableHead className="text-right">单买</TableHead>
                  <TableHead className="text-right">库存</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {goods.skus.map((sku) => (
                  <TableRow key={sku.sku_id} className={sku.on_sale ? undefined : "text-muted-foreground"}>
                    <TableCell className="whitespace-normal">
                      <div className="flex items-center gap-2">
                        <PddThumb url={sku.thumb} size={32} />
                        <span className="text-[13px] leading-snug">
                          {sku.name || "-"}
                          {!sku.on_sale && " · 不卖了"}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell className="text-right font-medium tabular-nums">
                      {yuan(sku.price_cent)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {yuan(sku.normal_price_cent)}
                    </TableCell>
                    <TableCell className="text-right tabular-nums text-muted-foreground">
                      {count(sku.quantity)}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        </Block>
      )}

      {goods.videos.length > 0 && (
        <Block title="视频" aside="只记了地址，没下载">
          <div className="flex flex-wrap gap-2">
            {goods.videos.map((video, i) => (
              <Button key={video.url} variant="outline" size="sm" asChild>
                <a href={video.url} target="_blank" rel="noreferrer">
                  <PlayIcon />
                  视频 {i + 1}
                </a>
              </Button>
            ))}
          </div>
        </Block>
      )}

      {goods.detail_images.length > 0 && (
        <Block title={`详情图 ${goods.detail_images.length} 张`} aside="拼多多的详情全是图片">
          <div className="flex flex-col overflow-hidden rounded-lg border">
            {goods.detail_images.map((img) => (
              <img
                key={img.url}
                src={img.url}
                alt=""
                width={img.width ?? undefined}
                height={img.height ?? undefined}
                loading="lazy"
                referrerPolicy="no-referrer"
                className="h-auto w-full bg-muted"
              />
            ))}
          </div>
        </Block>
      )}

      <Separator />
      <p className="text-xs text-muted-foreground">
        第一次导入 {dateTime(goods.imported_at)} · 最近导入 {dateTime(goods.updated_at)}
      </p>
    </div>
  )
}

/** 货源详情：图、价格、参数、规格、详情图，都是导入时存下的，不请求拼多多。 */
export function PddDetailSheet({
  goodsId,
  summary,
  onClose,
  onDraft,
  drafting,
  canDraft,
  onFetchImages,
  fetching,
}: {
  goodsId: string
  /** 列表里已经有的这一行，详情没回来时先显示标题 */
  summary?: PddGoodsSummary
  onClose: () => void
  onDraft: () => void
  drafting: boolean
  canDraft: boolean
  onFetchImages: () => void
  fetching: boolean
}) {
  const detail = usePddGoodsDetail(goodsId)
  const goods = detail.data
  const title = goods?.title ?? summary?.title ?? "拼多多商品"
  const tip = goods?.sales_tip ?? summary?.sales_tip
  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      {/* 基础样式里是 data-[side=right]:sm:max-w-sm，要用同样的写法才盖得住（设计规范：抽屉最宽 512px） */}
      <SheetContent className="w-full gap-0 overflow-y-auto overscroll-contain data-[side=right]:sm:max-w-lg">
        <SheetHeader className="border-b">
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="font-mono" translate="no">
              {goodsId}
            </span>
            {tip && <span>· {tip}</span>}
          </div>
          <SheetTitle className="pr-6 leading-snug">{title}</SheetTitle>
          <SheetDescription className="sr-only">拼多多货源详情</SheetDescription>
        </SheetHeader>
        {detail.isPending && (
          <div className="flex flex-col gap-3 p-4">
            <Skeleton className="h-[88px] w-full" />
            <Skeleton className="h-10 w-full" />
            <Skeleton className="h-40 w-full" />
          </div>
        )}
        {detail.isError && (
          <LoadError what="读取货源" error={detail.error} onRetry={() => void detail.refetch()} />
        )}
        {goods && (
          <Body
            goods={goods}
            onDraft={onDraft}
            drafting={drafting}
            canDraft={canDraft}
            onFetchImages={onFetchImages}
            fetching={fetching}
          />
        )}
      </SheetContent>
    </Sheet>
  )
}
