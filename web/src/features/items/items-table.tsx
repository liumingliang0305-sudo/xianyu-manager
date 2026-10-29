import {
  ArchiveIcon,
  EllipsisIcon,
  ExternalLinkIcon,
  EyeIcon,
  FileTextIcon,
  PencilIcon,
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
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import type { Item } from "@/lib/api"
import { count, itemState, itemUrl, yuan } from "@/lib/format"

import { ItemThumb, StateBadge } from "./item-bits"

export type ItemActionKind = "detail" | "price" | "desc" | "downshelf" | "delete"

export function ItemsTable({
  items,
  onAction,
}: {
  items: Item[]
  onAction: (kind: ItemActionKind, item: Item) => void
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="min-w-72">商品</TableHead>
          <TableHead className="text-right">售价</TableHead>
          <TableHead>状态</TableHead>
          <TableHead className="text-right">想要</TableHead>
          <TableHead className="text-right">浏览</TableHead>
          <TableHead className="text-right">收藏</TableHead>
          <TableHead className="w-0">
            <span className="sr-only">操作</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((item) => {
          const state = itemState(item)
          const editable = state === "online"
          return (
            <TableRow key={item.item_id}>
              <TableCell className="whitespace-normal">
                <button
                  type="button"
                  onClick={() => onAction("detail", item)}
                  className="group flex items-center gap-3 rounded-md text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  <ItemThumb url={item.pic_url} />
                  <div className="min-w-0">
                    <div className="line-clamp-2 font-medium group-hover:underline">
                      {item.title}
                    </div>
                    <div className="mt-0.5 font-mono text-xs text-muted-foreground">
                      {item.item_id}
                    </div>
                  </div>
                </button>
              </TableCell>
              <TableCell className="text-right font-medium tabular-nums">
                {yuan(item.price_cent)}
              </TableCell>
              <TableCell>
                <StateBadge state={state} />
              </TableCell>
              <TableCell className="text-right tabular-nums">{count(item.want_count)}</TableCell>
              <TableCell className="text-right tabular-nums">{count(item.browse_count)}</TableCell>
              <TableCell className="text-right tabular-nums">{count(item.collect_count)}</TableCell>
              <TableCell>
                <div className="flex items-center justify-end gap-1">
                  {editable && (
                    <Button variant="outline" size="sm" onClick={() => onAction("price", item)}>
                      <PencilIcon />
                      改价
                    </Button>
                  )}
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon-sm" aria-label="更多操作">
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
                          <DropdownMenuItem onSelect={() => onAction("price", item)}>
                            <PencilIcon />
                            改价
                          </DropdownMenuItem>
                          <DropdownMenuItem onSelect={() => onAction("desc", item)}>
                            <FileTextIcon />
                            改描述
                          </DropdownMenuItem>
                          <DropdownMenuItem onSelect={() => onAction("downshelf", item)}>
                            <ArchiveIcon />
                            下架
                          </DropdownMenuItem>
                        </>
                      )}
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        variant="destructive"
                        onSelect={() => onAction("delete", item)}
                      >
                        <Trash2Icon />
                        删除
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
    </Table>
  )
}
