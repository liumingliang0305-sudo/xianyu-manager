import { BookmarkXIcon, EllipsisIcon, ExternalLinkIcon, SearchIcon } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { EmptyState, LoadError, RowsSkeleton, SearchInput } from "@/components/states"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { errorMessage, type StashItem } from "@/lib/api"
import { count, dateTime, timeAgo, yuan } from "@/lib/format"

import { useStash, useStashItem, useUnstashItem } from "./queries"
import { ItemCell } from "./results-table"

function StashMenu({
  item,
  onSearch,
  onRemove,
}: {
  item: StashItem
  onSearch: () => void
  onRemove: () => void
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label={`${item.title} 的更多操作`}>
          <EllipsisIcon />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        <DropdownMenuItem asChild>
          <a href={item.url} target="_blank" rel="noreferrer">
            <ExternalLinkIcon />
            在闲鱼打开
          </a>
        </DropdownMenuItem>
        {item.keyword && (
          <DropdownMenuItem onSelect={onSearch}>
            <SearchIcon />
            再搜「{item.keyword}」
          </DropdownMenuItem>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onSelect={onRemove}>
          <BookmarkXIcon />
          移出暂存
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function StashTable({
  items,
  onSearch,
  onRemove,
}: {
  items: StashItem[]
  onSearch: (keyword: string) => void
  onRemove: (item: StashItem) => void
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-full min-w-72">商品</TableHead>
          <TableHead className="text-right">售价</TableHead>
          <TableHead className="text-right">想要</TableHead>
          <TableHead>卖家</TableHead>
          <TableHead>暂存</TableHead>
          <TableHead>
            <span className="sr-only">操作</span>
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
              <TableCell className="text-xs text-muted-foreground">
                <div className="tabular-nums" title={dateTime(item.saved_at)}>
                  {timeAgo(item.saved_at)}
                </div>
                {item.keyword && (
                  <div className="max-w-32 truncate" title={`搜「${item.keyword}」时暂存的`}>
                    搜「{item.keyword}」
                  </div>
                )}
              </TableCell>
              <TableCell className="text-right">
                <StashMenu
                  item={item}
                  onSearch={() => onSearch(item.keyword)}
                  onRemove={() => onRemove(item)}
                />
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}

/**
 * 暂存的商品：搜索时点「暂存」留下的，按用户存，名下账号共用。
 * 这里看的都是本地库里存的样子（暂存那一刻搜到的），不请求闲鱼；价格、想要人数可能已经变了。
 */
export function StashPanel({ onSearch }: { onSearch: (keyword: string) => void }) {
  const stash = useStash()
  const restash = useStashItem()
  const unstash = useUnstashItem()
  const [filter, setFilter] = useState("")

  const remove = (item: StashItem) =>
    unstash.mutate(item, {
      onSuccess: () =>
        toast.success("已移出暂存", {
          action: {
            label: "撤销",
            onClick: () =>
              restash.mutate(
                { item, keyword: item.keyword },
                { onError: (error) => toast.error(`放回暂存失败：${errorMessage(error)}`) },
              ),
          },
        }),
      onError: (error) => toast.error(`移出暂存失败：${errorMessage(error)}`),
    })

  const all = stash.data ?? []
  const query = filter.trim().toLowerCase()
  const visible = all.filter(
    (it) =>
      !query ||
      it.title.toLowerCase().includes(query) ||
      it.seller_nick.toLowerCase().includes(query) ||
      it.keyword.toLowerCase().includes(query) ||
      it.item_id.includes(query),
  )

  let body
  if (stash.isPending) body = <RowsSkeleton rows={4} height="h-14" />
  else if (stash.isError)
    body = <LoadError what="读取暂存" error={stash.error} onRetry={() => void stash.refetch()} />
  else if (all.length === 0)
    body = (
      <EmptyState title="还没有暂存的商品">
        在搜索结果里点「暂存」，商品会留在这里，换了搜索词、换了账号都还在。存的是搜到那一刻的样子
      </EmptyState>
    )
  else if (visible.length === 0) body = <EmptyState>「{filter.trim()}」没有匹配的商品</EmptyState>
  else body = <StashTable items={visible} onSearch={onSearch} onRemove={remove} />

  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b px-4 py-3">
        <SearchInput
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="搜标题、卖家或搜索词…"
          aria-label="在暂存里找"
          className="w-full sm:w-64"
        />
        <span className="text-xs text-muted-foreground tabular-nums">
          {query && all.length > 0
            ? `${visible.length} / ${all.length} 件`
            : "名下所有闲鱼账号共用 · 价格、想要是暂存时的"}
        </span>
      </div>
      {body}
    </>
  )
}
