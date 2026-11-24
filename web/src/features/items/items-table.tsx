import {
  ArchiveIcon,
  ArchiveRestoreIcon,
  EllipsisIcon,
  ExternalLinkIcon,
  EyeIcon,
  Loader2Icon,
  PencilIcon,
  SquarePenIcon,
  Trash2Icon,
} from "lucide-react"
import type { ReactNode } from "react"

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Table,
  TableBody,
  TableCell,
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import type { Item } from "@/lib/api"
import { count, itemState, itemUrl, yuan } from "@/lib/format"

import { ItemThumb, StateBadge } from "./item-bits"

/** edit 是去编辑页，relist 是照原样直接发一个新商品，relist-edit 是抄成草稿去发布页改好了再发，别的在商品页上弹窗 */
export type ItemActionKind =
  | "detail"
  | "price"
  | "edit"
  | "downshelf"
  | "delete"
  | "relist"
  | "relist-edit"

type OnAction = (kind: ItemActionKind, item: Item) => void

// 表头上悬停（或者 Tab 到）时说这几个数从哪来：浏览、收藏同步时拿不到，没拿过的是 -
const STAT_HINTS = {
  want: '同步时从主页列表的"N人想要"读；没人想要时列表里没有，插件拿浏览、收藏时一起补上',
  browse: "只在闲鱼商品页里有：浏览器插件在你的 Chrome 里打开商品页拿，没拿过的是 -。列表上面能刷新",
  collect: "和浏览一样，由浏览器插件在你的 Chrome 里打开商品页拿，没拿过的是 -",
}

function StatHead({ label, hint }: { label: string; hint: string }) {
  return (
    <TableHead className="text-right">
      <Tooltip>
        <TooltipTrigger asChild>
          <span
            tabIndex={0}
            className="cursor-help rounded-sm underline decoration-dotted underline-offset-4 outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
          >
            {label}
          </span>
        </TooltipTrigger>
        <TooltipContent side="top">{hint}</TooltipContent>
      </Tooltip>
    </TableHead>
  )
}

/** 浏览数那一格：插件正在拿这一件时前面转个圈 */
function BrowseCell({ value, loading }: { value: number | null; loading: boolean }) {
  return (
    <TableCell className="text-right tabular-nums">
      {loading && (
        <Loader2Icon
          className="mr-1 inline size-3.5 animate-spin align-[-2px] text-muted-foreground"
          aria-label="正在拿浏览、收藏"
        />
      )}
      {count(value)}
    </TableCell>
  )
}

/** 只加有数的；全是空的才显示"-"。没刷新过浏览、收藏的商品不能把合计变成空 */
function total(items: Item[], key: "want_count" | "browse_count" | "collect_count"): string {
  const values = items.map((it) => it[key]).filter((v): v is number => v != null)
  return values.length === 0 ? "-" : count(values.reduce((a, b) => a + b, 0))
}

/**
 * 闲鱼说网页端发不了的商品（图书这类分类）：在原来按钮的位置说一句去 App，
 * 悬停或 Tab 到这里看那次闲鱼给的原因
 */
function AppOnly({ reason, children }: { reason: string; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <span
          tabIndex={0}
          className="rounded-md px-2 py-1 text-xs whitespace-nowrap text-muted-foreground outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
        >
          {children}
        </span>
      </TooltipTrigger>
      <TooltipContent>{reason}</TooltipContent>
    </Tooltip>
  )
}

function ItemMenu({ item, onAction }: { item: Item; onAction: OnAction }) {
  const editable = itemState(item) === "online"
  const blocked = !!item.web_blocked
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label={`${item.title} 的更多操作`}>
          <EllipsisIcon />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-40">
        <DropdownMenuItem onSelect={() => onAction("detail", item)}>
          <EyeIcon />
          查看详情
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <a href={itemUrl(item.item_id)} target="_blank" rel="noreferrer">
            <ExternalLinkIcon />
            在闲鱼打开
          </a>
        </DropdownMenuItem>
        {editable && (
          <>
            <DropdownMenuSeparator />
            {blocked && <DropdownMenuLabel className="font-normal">只能在 App 里编辑</DropdownMenuLabel>}
            <DropdownMenuItem disabled={blocked} onSelect={() => onAction("edit", item)}>
              <SquarePenIcon />
              编辑商品
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onAction("price", item)}>
              <PencilIcon />
              改价
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onAction("downshelf", item)}>
              <ArchiveIcon />
              下架
            </DropdownMenuItem>
          </>
        )}
        {!editable && (
          <>
            <DropdownMenuSeparator />
            {blocked && <DropdownMenuLabel className="font-normal">只能在 App 里上架</DropdownMenuLabel>}
            <DropdownMenuItem disabled={blocked} onSelect={() => onAction("relist", item)}>
              <ArchiveRestoreIcon />
              重新上架
            </DropdownMenuItem>
            <DropdownMenuItem disabled={blocked} onSelect={() => onAction("relist-edit", item)}>
              <SquarePenIcon />
              编辑后上架
            </DropdownMenuItem>
          </>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onSelect={() => onAction("delete", item)}>
          <Trash2Icon />
          删除
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** 商品名格子：缩略图、标题、商品 ID，点开看详情 */
function ItemCell({ item, onAction }: { item: Item; onAction: OnAction }) {
  return (
    <button
      type="button"
      onClick={() => onAction("detail", item)}
      className="group flex min-w-0 items-center gap-3 rounded-md text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
    >
      <ItemThumb url={item.pic_url} />
      <div className="min-w-0">
        <div className="line-clamp-2 font-medium group-hover:underline">{item.title}</div>
        <div className="mt-0.5 font-mono text-xs text-muted-foreground" translate="no">
          {item.item_id}
        </div>
      </div>
    </button>
  )
}

/**
 * 宽屏是表格，最后一行是合计；768px 以下换成两行的列表，字段挪到第二行，不丢。relisting 是正在重新上架的商品，
 * statsLoading 是浏览器插件正在拿浏览、收藏的商品（浏览、收藏从哪来在表头的提示和列表上面那行说）
 */
export function ItemsTable({
  items,
  onAction,
  relisting,
  statsLoading,
}: {
  items: Item[]
  onAction: OnAction
  relisting?: string | null
  statsLoading?: string | null
}) {
  return (
    <>
      <div className="hidden md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-full min-w-72">商品</TableHead>
              <TableHead className="text-right">售价</TableHead>
              <TableHead>状态</TableHead>
              <StatHead label="想要" hint={STAT_HINTS.want} />
              <StatHead label="浏览" hint={STAT_HINTS.browse} />
              <StatHead label="收藏" hint={STAT_HINTS.collect} />
              <TableHead>
                <span className="sr-only">操作</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.item_id}>
                <TableCell className="whitespace-normal">
                  <ItemCell item={item} onAction={onAction} />
                </TableCell>
                <TableCell className="text-right font-medium tabular-nums">
                  {yuan(item.price_cent)}
                </TableCell>
                <TableCell>
                  <StateBadge state={itemState(item)} />
                </TableCell>
                <TableCell className="text-right tabular-nums">{count(item.want_count)}</TableCell>
                <BrowseCell value={item.browse_count} loading={statsLoading === item.item_id} />
                <TableCell className="text-right tabular-nums">{count(item.collect_count)}</TableCell>
                <TableCell>
                  <div className="flex items-center justify-end gap-1">
                    {itemState(item) === "online" ? (
                      <>
                        <Button variant="outline" size="sm" onClick={() => onAction("price", item)}>
                          <PencilIcon />
                          改价
                        </Button>
                        {item.web_blocked ? (
                          <AppOnly reason={item.web_blocked}>只能在 App 里编辑</AppOnly>
                        ) : (
                          <Button variant="outline" size="sm" onClick={() => onAction("edit", item)}>
                            <SquarePenIcon />
                            编辑
                          </Button>
                        )}
                      </>
                    ) : item.web_blocked ? (
                      <AppOnly reason={item.web_blocked}>只能在 App 里上架</AppOnly>
                    ) : (
                      <>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={relisting === item.item_id}
                          onClick={() => onAction("relist", item)}
                        >
                          {relisting === item.item_id ? (
                            <Loader2Icon className="animate-spin" />
                          ) : (
                            <ArchiveRestoreIcon />
                          )}
                          {relisting === item.item_id ? "上架中…" : "重新上架"}
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => onAction("relist-edit", item)}
                        >
                          <SquarePenIcon />
                          编辑后上架
                        </Button>
                      </>
                    )}
                    <ItemMenu item={item} onAction={onAction} />
                  </div>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
          {items.length > 1 && (
            <TableFooter>
              <TableRow>
                <TableCell className="font-normal whitespace-normal text-muted-foreground">
                  合计 {items.length} 件
                </TableCell>
                <TableCell />
                <TableCell />
                <TableCell className="text-right tabular-nums">{total(items, "want_count")}</TableCell>
                <TableCell className="text-right tabular-nums">{total(items, "browse_count")}</TableCell>
                <TableCell className="text-right tabular-nums">{total(items, "collect_count")}</TableCell>
                <TableCell />
              </TableRow>
            </TableFooter>
          )}
        </Table>
      </div>

      <ul className="divide-y divide-rule md:hidden">
        {items.map((item) => (
          <li key={item.item_id} className="flex items-center gap-2 py-3 pr-2 pl-4">
            <button
              type="button"
              onClick={() => onAction("detail", item)}
              className="flex min-w-0 flex-1 items-center gap-3 rounded-md text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              <ItemThumb url={item.pic_url} />
              <div className="min-w-0 flex-1">
                <div className="line-clamp-2 font-medium">{item.title}</div>
                <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                  <span className="text-sm font-medium text-foreground tabular-nums">
                    {yuan(item.price_cent)}
                  </span>
                  <StateBadge state={itemState(item)} />
                  <span className="tabular-nums">想要 {count(item.want_count)}</span>
                  <span className="tabular-nums">浏览 {count(item.browse_count)}</span>
                </div>
              </div>
            </button>
            <ItemMenu item={item} onAction={onAction} />
          </li>
        ))}
      </ul>
    </>
  )
}
