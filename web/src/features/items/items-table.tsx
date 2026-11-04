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

import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
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

/** 只加有数的；全是空的才显示"-"。已卖出的商品不拉浏览、收藏，不能让它们把合计变成空 */
function total(items: Item[], key: "want_count" | "browse_count" | "collect_count"): string {
  const values = items.map((it) => it[key]).filter((v): v is number => v != null)
  return values.length === 0 ? "-" : count(values.reduce((a, b) => a + b, 0))
}

function ItemMenu({ item, onAction }: { item: Item; onAction: OnAction }) {
  const editable = itemState(item) === "online"
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
            <DropdownMenuItem onSelect={() => onAction("edit", item)}>
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
            <DropdownMenuItem onSelect={() => onAction("relist", item)}>
              <ArchiveRestoreIcon />
              重新上架
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={() => onAction("relist-edit", item)}>
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

/** 宽屏是表格，最后一行是合计；768px 以下换成两行的列表，字段挪到第二行，不丢。relisting 是正在重新上架的商品 */
export function ItemsTable({
  items,
  onAction,
  relisting,
}: {
  items: Item[]
  onAction: OnAction
  relisting?: string | null
}) {
  // 在售商品没拉浏览、收藏时提示一句；已卖出的本来就不拉
  const missingStats = items.some((it) => itemState(it) === "online" && it.browse_count == null)
  return (
    <>
      <div className="hidden md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-full min-w-72">商品</TableHead>
              <TableHead className="text-right">售价</TableHead>
              <TableHead>状态</TableHead>
              <TableHead className="text-right">想要</TableHead>
              <TableHead className="text-right">浏览</TableHead>
              <TableHead className="text-right">收藏</TableHead>
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
                <TableCell className="text-right tabular-nums">{count(item.browse_count)}</TableCell>
                <TableCell className="text-right tabular-nums">{count(item.collect_count)}</TableCell>
                <TableCell>
                  <div className="flex items-center justify-end gap-1">
                    {itemState(item) === "online" ? (
                      <>
                        <Button variant="outline" size="sm" onClick={() => onAction("price", item)}>
                          <PencilIcon />
                          改价
                        </Button>
                        <Button variant="outline" size="sm" onClick={() => onAction("edit", item)}>
                          <SquarePenIcon />
                          编辑
                        </Button>
                      </>
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
                  {missingStats && (
                    <span className="ml-2 text-xs">浏览、收藏要在同步时打开「含浏览/收藏」才有</span>
                  )}
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
