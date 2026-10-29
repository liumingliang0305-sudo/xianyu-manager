import { EllipsisIcon, ExternalLinkIcon, EyeIcon, ListXIcon, PencilIcon } from "lucide-react"

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
import { ItemThumb } from "@/features/items/item-bits"
import type { Order } from "@/lib/api"
import { dateTime, orderUrl, yuan } from "@/lib/format"

import { OrderStatusBadge } from "./order-bits"

export type OrderActionKind = "detail" | "price" | "remove"

export function OrdersTable({
  orders,
  onAction,
}: {
  orders: Order[]
  onAction: (kind: OrderActionKind, order: Order) => void
}) {
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="min-w-72">商品</TableHead>
          <TableHead className="text-right">金额</TableHead>
          <TableHead>状态</TableHead>
          <TableHead>买家</TableHead>
          <TableHead>下单时间</TableHead>
          <TableHead className="w-0">
            <span className="sr-only">操作</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {orders.map((order) => {
          const unpaid = order.status === "unpaid"
          return (
            <TableRow key={order.order_id}>
              <TableCell className="whitespace-normal">
                <button
                  type="button"
                  onClick={() => onAction("detail", order)}
                  className="group flex items-center gap-3 rounded-md text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  <ItemThumb url={order.pic_url} />
                  <div className="min-w-0">
                    <div className="line-clamp-2 font-medium group-hover:underline">
                      {order.title || `商品 ${order.item_id || "-"}`}
                      {order.quantity > 1 && (
                        <span className="ml-1 font-normal text-muted-foreground">
                          ×{order.quantity}
                        </span>
                      )}
                    </div>
                    <div className="mt-0.5 font-mono text-xs text-muted-foreground">
                      {order.order_id}
                    </div>
                  </div>
                </button>
              </TableCell>
              <TableCell className="text-right font-medium tabular-nums">
                {yuan(order.price_cent)}
              </TableCell>
              <TableCell>
                <OrderStatusBadge status={order.status} text={order.status_text} />
              </TableCell>
              <TableCell className="max-w-32 truncate text-muted-foreground">
                {order.buyer_nick || order.buyer_id || "-"}
              </TableCell>
              <TableCell className="text-muted-foreground tabular-nums">
                {dateTime(order.created_at)}
              </TableCell>
              <TableCell>
                <div className="flex items-center justify-end gap-1">
                  {unpaid && (
                    <Button variant="outline" size="sm" onClick={() => onAction("price", order)}>
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
                      <DropdownMenuItem onSelect={() => onAction("detail", order)}>
                        <EyeIcon />
                        查看详情
                      </DropdownMenuItem>
                      <DropdownMenuItem asChild>
                        <a href={orderUrl(order.order_id)} target="_blank" rel="noreferrer">
                          <ExternalLinkIcon />
                          在闲鱼打开
                        </a>
                      </DropdownMenuItem>
                      {unpaid && (
                        <DropdownMenuItem onSelect={() => onAction("price", order)}>
                          <PencilIcon />
                          改价
                        </DropdownMenuItem>
                      )}
                      <DropdownMenuSeparator />
                      <DropdownMenuItem onSelect={() => onAction("remove", order)}>
                        <ListXIcon />
                        从列表移除
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
