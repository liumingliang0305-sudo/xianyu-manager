import { BookmarkXIcon, EllipsisIcon, ExternalLinkIcon, EyeIcon, SearchIcon } from "lucide-react"
import { useState } from "react"
import { useSearchParams } from "react-router"
import { toast } from "sonner"

import { EmptyState, LoadError, RowsSkeleton, SearchInput } from "@/components/states"
import { StatusPill } from "@/components/status-pill"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table"
import { ItemThumb } from "@/features/items/item-bits"
import { errorMessage, type StashItem } from "@/lib/api"
import { useExtension } from "@/lib/extension"
import { count, dateTime, timeAgo, yuan } from "@/lib/format"

import {
  useFetchingDetails,
  useFetchStashDetail,
  useStash,
  useStashItem,
  useUnstashItem,
} from "./queries"
import { StashDetailSheet } from "./stash-detail-sheet"

/** 打开的暂存商品放在网址里（?view=stash&item=…），刷新还开着 */
function useOpenItem(): [string | null, (itemId: string | null) => void] {
  const [params, setParams] = useSearchParams()
  const open = (itemId: string | null) =>
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev)
        if (itemId) p.set("item", itemId)
        else p.delete("item")
        return p
      },
      { replace: true },
    )
  return [params.get("item"), open]
}

/**
 * 商品格子：缩略图、标题，下面一行是包邮、成色这些标签；有详情时前面加上几张图、描述多少字，
 * 没有的标出来（正在拿的标"拿详情中"）。点开看详情
 */
function StashItemCell({
  item,
  fetching,
  onOpen,
}: {
  item: StashItem
  fetching: boolean
  onOpen: () => void
}) {
  const tags = [item.free_ship ? "包邮" : "", ...item.tags].filter(Boolean).join(" · ")
  const detail =
    item.detail_at != null
      ? [`${item.images.length} 张图`, item.description ? `描述 ${count(item.description.length)} 字` : ""]
          .filter(Boolean)
          .join(" · ")
      : ""
  return (
    <button
      type="button"
      onClick={onOpen}
      className="group flex min-w-0 items-center gap-3 rounded-md text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
    >
      <ItemThumb url={item.images[0] ?? item.pic_url} />
      <div className="min-w-0">
        <div className="line-clamp-2 font-medium group-hover:underline">{item.title}</div>
        <div className="mt-0.5 flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
          {fetching ? (
            <StatusPill tone="neutral" className="shrink-0">
              拿详情中…
            </StatusPill>
          ) : (
            item.detail_at == null && (
              <StatusPill tone="outline" className="shrink-0">
                没有详情
              </StatusPill>
            )
          )}
          <span className="line-clamp-1">{[detail, tags].filter(Boolean).join(" · ")}</span>
        </div>
      </div>
    </button>
  )
}

function StashMenu({
  item,
  onOpen,
  onSearch,
  onRemove,
}: {
  item: StashItem
  onOpen: () => void
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
        <DropdownMenuItem onSelect={onOpen}>
          <EyeIcon />
          看详情
        </DropdownMenuItem>
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
  fetching,
  onOpen,
  onSearch,
  onRemove,
}: {
  items: StashItem[]
  fetching: ReadonlySet<string>
  onOpen: (item: StashItem) => void
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
                <StashItemCell
                  item={item}
                  fetching={fetching.has(item.item_id)}
                  onOpen={() => onOpen(item)}
                />
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
                  onOpen={() => onOpen(item)}
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
 * 这里看的都是本地库里存的样子（暂存那一刻搜到的、插件拿来的详情），不请求闲鱼；价格、想要人数可能已经变了。
 * 拉详情（和撤销移出以后重新拿）请浏览器插件在 Chrome 里打开那件商品的闲鱼页面拿。
 */
export function StashPanel({ onSearch }: { onSearch: (keyword: string) => void }) {
  const stash = useStash()
  const extension = useExtension()
  const fetchDetail = useFetchStashDetail()
  const fetching = useFetchingDetails()
  const pull = (item: StashItem) => fetchDetail.mutate({ itemId: item.item_id, title: item.title })
  const restash = useStashItem((saved) => {
    if (extension) pull(saved)
  })
  const unstash = useUnstashItem()
  const [filter, setFilter] = useState("")
  const [openId, setOpenId] = useOpenItem()

  const remove = (item: StashItem) => {
    if (openId === item.item_id) setOpenId(null)
    unstash.mutate(item, {
      onSuccess: () =>
        toast.success("已移出暂存", {
          action: {
            label: "撤销",
            onClick: () => restash.mutate({ item, keyword: item.keyword }),
          },
        }),
      onError: (error) => toast.error(`移出暂存失败：${errorMessage(error)}`),
    })
  }

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
        在搜索结果里点「暂存」，商品会留在这里，换了搜索词、换了账号都还在。存的是搜到那一刻的样子；
        装了浏览器插件的，会顺带从你的 Chrome 里拿完整描述和全部图片
      </EmptyState>
    )
  else if (visible.length === 0) body = <EmptyState>「{filter.trim()}」没有匹配的商品</EmptyState>
  else
    body = (
      <StashTable
        items={visible}
        fetching={fetching}
        onOpen={(item) => setOpenId(item.item_id)}
        onSearch={onSearch}
        onRemove={remove}
      />
    )
  const opened = openId ? all.find((it) => it.item_id === openId) : undefined

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
      {opened && (
        <StashDetailSheet
          item={opened}
          extension={extension != null}
          fetching={fetching.has(opened.item_id)}
          onFetch={() => pull(opened)}
          onClose={() => setOpenId(null)}
          onRemove={() => remove(opened)}
        />
      )}
    </>
  )
}
