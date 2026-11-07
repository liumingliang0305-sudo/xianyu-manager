import { BookmarkCheckIcon, BookmarkPlusIcon } from "lucide-react"

import { Button } from "@/components/ui/button"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ItemThumb } from "@/features/items/item-bits"
import type { SearchItem } from "@/lib/api"
import { count, dateTime, timeAgo, yuan } from "@/lib/format"

/** 一个月内的说"3 天前"，再早的只写日期 */
function published(seconds: number | null): string {
  if (!seconds) return "-"
  if (Date.now() / 1000 - seconds < 30 * 86400) return timeAgo(seconds)
  return dateTime(seconds).split(" ")[0]
}

/** 商品名格子：缩略图、标题，下面一行是包邮、成色、品牌、降价这些标签。点开在闲鱼看 */
export function ItemCell({ item }: { item: SearchItem }) {
  const meta = [item.free_ship ? "包邮" : "", ...item.tags].filter(Boolean).join(" · ")
  return (
    <a
      href={item.url}
      target="_blank"
      rel="noreferrer"
      className="group flex min-w-0 items-center gap-3 rounded-md outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
    >
      <ItemThumb url={item.pic_url} />
      <div className="min-w-0">
        <div className="line-clamp-2 font-medium group-hover:underline">{item.title}</div>
        {meta && <div className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">{meta}</div>}
      </div>
      <span className="sr-only">（在闲鱼打开）</span>
    </a>
  )
}

/**
 * 暂存开关：没存时是"暂存"，存了是"已暂存"，再点一下移出。宽度固定，切换时这一列不跳。
 * 暂存列表还没拉下来时（stashed 是 null）点不了，免得把已经存过的又存一遍
 */
function StashButton({
  on,
  disabled,
  onClick,
}: {
  on: boolean
  disabled: boolean
  onClick: () => void
}) {
  return (
    <Button
      variant="outline"
      size="sm"
      disabled={disabled}
      onClick={onClick}
      title={on ? "再点一下移出暂存" : "存到暂存里，以后接着用"}
      data-on={on || undefined}
      className="w-[5.25rem] data-on:bg-secondary data-on:text-foreground"
    >
      {on ? <BookmarkCheckIcon /> : <BookmarkPlusIcon />}
      {on ? "已暂存" : "暂存"}
    </Button>
  )
}

export function ResultsTable({
  items,
  stashed,
  onToggleStash,
}: {
  items: SearchItem[]
  /** 暂存了的商品 ID，还不知道时是 null */
  stashed: ReadonlySet<string> | null
  onToggleStash: (item: SearchItem) => void
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-full min-w-72">商品</TableHead>
          <TableHead className="text-right">售价</TableHead>
          <TableHead className="text-right">想要</TableHead>
          <TableHead>卖家</TableHead>
          <TableHead className="text-right">发布</TableHead>
          <TableHead>
            <span className="sr-only">暂存</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((item) => {
          const where = [item.area, item.seller_tags[0]].filter(Boolean).join(" · ")
          return (
            <TableRow key={item.item_id}>
              <TableCell className="whitespace-normal">
                <ItemCell item={item} />
              </TableCell>
              <TableCell className="text-right tabular-nums">
                <div className="font-medium">{yuan(item.price_cent)}</div>
                {item.orig_price_cent != null && (
                  <div className="text-xs text-muted-foreground line-through">
                    {yuan(item.orig_price_cent)}
                  </div>
                )}
              </TableCell>
              <TableCell className="text-right tabular-nums">{count(item.want_count)}</TableCell>
              <TableCell>
                <div className="max-w-40 truncate" title={item.seller_nick} translate="no">
                  {item.seller_nick || "-"}
                </div>
                {where && (
                  <div className="max-w-40 truncate text-xs text-muted-foreground">{where}</div>
                )}
              </TableCell>
              <TableCell
                className="text-right text-muted-foreground tabular-nums"
                title={item.published_at ? dateTime(item.published_at) : undefined}
              >
                {published(item.published_at)}
              </TableCell>
              <TableCell className="text-right">
                <StashButton
                  on={stashed?.has(item.item_id) ?? false}
                  disabled={stashed == null}
                  onClick={() => onToggleStash(item)}
                />
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}
