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

type OnAction = (kind: OrderActionKind, order: Order) => void

function orderTitle(order: Order): string {
  return order.title || `商品 ${order.item_id || "-"}`
}

function buyer(order: Order): string {
  return order.buyer_nick || order.buyer_id || "-"
}

function OrderMenu({ order, onAction }: { order: Order; onAction: OnAction }) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label={`${orderTitle(order)} 的更多操作`}>
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
        {order.status === "unpaid" && (
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
  )
}

function Quantity({ order }: { order: Order }) {
  if (order.quantity <= 1) return null
  return <span className="ml-1 font-normal text-muted-foreground">×{order.quantity}</span>
}

/** 宽屏是表格，下单时间日期在上、时分秒在下；768px 以下换成两行的列表 */
export function OrdersTable({ orders, onAction }: { orders: Order[]; onAction: OnAction }) {
  return (
    <>
      <div className="hidden md:block">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-full min-w-64">商品</TableHead>
              <TableHead className="text-right">金额</TableHead>
              <TableHead>状态</TableHead>
              <TableHead>买家</TableHead>
              <TableHead className="text-right">下单时间</TableHead>
              <TableHead>
                <span className="sr-only">操作</span>
              </TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {orders.map((order) => {
              const [day, time] = dateTime(order.created_at).split(" ")
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
                          {orderTitle(order)}
                          <Quantity order={order} />
                        </div>
                        <div className="mt-0.5 font-mono text-xs text-muted-foreground" translate="no">
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
                  <TableCell className="max-w-32 truncate text-muted-foreground" title={buyer(order)}>
                    {buyer(order)}
                  </TableCell>
                  <TableCell className="text-right text-muted-foreground tabular-nums">
                    <div>{day}</div>
                    {time && <div className="text-xs">{time}</div>}
                  </TableCell>
                  <TableCell>
                    <div className="flex items-center justify-end gap-1">
                      {order.status === "unpaid" && (
                        <Button variant="outline" size="sm" onClick={() => onAction("price", order)}>
                          <PencilIcon />
                          改价
                        </Button>
                      )}
                      <OrderMenu order={order} onAction={onAction} />
                    </div>
                  </TableCell>
                </TableRow>
              )
            })}
          </TableBody>
        </Table>
      </div>

      <ul className="divide-y divide-rule md:hidden">
        {orders.map((order) => (
          <li key={order.order_id} className="flex items-center gap-2 py-3 pr-2 pl-4">
            <button
              type="button"
              onClick={() => onAction("detail", order)}
              className="flex min-w-0 flex-1 items-center gap-3 rounded-md text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              <ItemThumb url={order.pic_url} />
              <div className="min-w-0 flex-1">
                <div className="line-clamp-2 font-medium">
                  {orderTitle(order)}
                  <Quantity order={order} />
                </div>
                <div className="mt-1 flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
                  <span className="text-sm font-medium text-foreground tabular-nums">
                    {yuan(order.price_cent)}
                  </span>
                  <OrderStatusBadge status={order.status} text={order.status_text} />
                  <span className="max-w-28 truncate">{buyer(order)}</span>
                  <span className="tabular-nums">{dateTime(order.created_at).split(" ")[0]}</span>
                </div>
              </div>
            </button>
            <OrderMenu order={order} onAction={onAction} />
          </li>
        ))}
      </ul>
    </>
  )
}
