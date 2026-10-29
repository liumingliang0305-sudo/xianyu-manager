import {
  HistoryIcon,
  Loader2Icon,
  PlusIcon,
  RefreshCwIcon,
  SearchIcon,
  ShoppingCartIcon,
} from "lucide-react"
import { useMemo, useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useCurrentAccount } from "@/hooks/use-account"
import { errorMessage, type Order, type OrderStatus } from "@/lib/api"
import { ORDER_STATUS_LABEL, timeAgo, yuan } from "@/lib/format"

import { AddOrderDialog } from "./add-order-dialog"
import { OrderOpsList } from "./order-bits"
import { OrderDetailSheet } from "./order-detail-sheet"
import { OrderPriceDialog } from "./order-price-dialog"
import { OrdersTable, type OrderActionKind } from "./orders-table"
import { useOrderOps, useOrders, useSyncOrders } from "./queries"
import { RemoveOrderDialog } from "./remove-order-dialog"

type Tab = OrderStatus | "all"

const TABS: Tab[] = ["all", "unpaid", "to_ship", "shipped", "refund", "done", "closed"]

// 算成交额时不算待付款和已关闭的
const SOLD: OrderStatus[] = ["to_ship", "shipped", "done", "refund"]

const DAY = 86_400

function SummaryCard({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <Card size="sm">
      <CardContent>
        <div className="text-xs text-muted-foreground">{label}</div>
        <div className="mt-1 font-heading text-2xl font-semibold tabular-nums">{value}</div>
        {hint && <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div>}
      </CardContent>
    </Card>
  )
}

/** 个人卖家没有卖出订单列表，"刷新"是逐个拉本地还没结束的订单；专业卖家会整页同步。 */
function RefreshButton({ account }: { account: string }) {
  const sync = useSyncOrders(account)
  const run = () =>
    sync.mutate(undefined, {
      onSuccess: (r) => {
        if (r.source === "list") {
          toast.success(
            `同步完成：新增 ${r.added.length}，更新 ${r.updated.length}，状态有变化 ${r.changed.length}`,
          )
          return
        }
        const total = r.updated.length + r.failed.length
        if (total === 0) toast.success("没有进行中的订单需要刷新")
        else
          toast.success(
            `刷新了 ${total} 笔进行中的订单：状态有变化 ${r.changed.length}` +
              (r.failed.length ? `，${r.failed.length} 笔拉取失败` : ""),
          )
      },
      onError: (error) => toast.error(errorMessage(error)),
    })
  return (
    <Button onClick={run} disabled={sync.isPending} variant="outline">
      {sync.isPending ? <Loader2Icon className="animate-spin" /> : <RefreshCwIcon />}
      {sync.isPending ? "刷新中…" : "刷新状态"}
    </Button>
  )
}

export function OrdersPage() {
  const account = useCurrentAccount()
  const orders = useOrders(account)
  const ops = useOrderOps(account)
  const [tab, setTab] = useState<Tab>("all")
  const [search, setSearch] = useState("")
  const [action, setAction] = useState<{ kind: OrderActionKind; order: Order } | null>(null)
  const [adding, setAdding] = useState(false)
  // 打开页面时定下"近 30 天"的起点，不随重新渲染变
  const [since] = useState(() => Date.now() / 1000 - 30 * DAY)

  const all = useMemo(() => orders.data?.orders ?? [], [orders.data])
  const byStatus = useMemo(() => {
    const groups = new Map<OrderStatus, Order[]>()
    for (const order of all) groups.set(order.status, [...(groups.get(order.status) ?? []), order])
    return groups
  }, [all])
  const titles = useMemo(
    () => new Map(all.map((o) => [o.order_id, o.title || o.order_id])),
    [all],
  )
  const recentSales = useMemo(
    () =>
      all
        .filter((o) => SOLD.includes(o.status) && (o.created_at ?? o.first_seen_at) >= since)
        .reduce((sum, o) => sum + (o.price_cent ?? 0), 0),
    [all, since],
  )

  const counted = (status: OrderStatus) => byStatus.get(status)?.length ?? 0
  const query = search.trim().toLowerCase()
  const visible = (tab === "all" ? all : (byStatus.get(tab) ?? [])).filter(
    (o) =>
      !query ||
      o.title.toLowerCase().includes(query) ||
      o.order_id.includes(query) ||
      o.buyer_nick.toLowerCase().includes(query),
  )
  const pending = orders.isPending
  const close = () => setAction(null)

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-heading text-2xl font-semibold">订单</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            卖出的订单 · 上次更新：{pending ? "…" : timeAgo(orders.data?.last_synced_at)}
          </p>
        </div>
        <div className="flex gap-2">
          {all.length > 0 && <RefreshButton account={account} />}
          <Button onClick={() => setAdding(true)}>
            <PlusIcon />
            添加订单
          </Button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <SummaryCard
          label="待发货"
          value={pending ? "…" : String(counted("to_ship"))}
          hint={counted("to_ship") > 0 ? "去闲鱼 App 发货" : undefined}
        />
        <SummaryCard label="待付款" value={pending ? "…" : String(counted("unpaid"))} />
        <SummaryCard label="退款中" value={pending ? "…" : String(counted("refund"))} />
        <SummaryCard
          label="近 30 天成交"
          value={pending ? "…" : yuan(recentSales)}
          hint="不含待付款和已关闭"
        />
      </div>

      <Card className="gap-0 py-0">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b p-3">
          {/* 状态多，窄屏时标签横向滚动 */}
          <Tabs
            value={tab}
            onValueChange={(v) => setTab(v as Tab)}
            className="max-w-full overflow-x-auto"
          >
            <TabsList>
              {TABS.map((value) => (
                <TabsTrigger key={value} value={value}>
                  {value === "all" ? "全部" : ORDER_STATUS_LABEL[value]}
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {value === "all" ? all.length : counted(value)}
                  </span>
                </TabsTrigger>
              ))}
            </TabsList>
          </Tabs>
          <div className="relative w-full sm:w-60">
            <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜商品、订单号或买家"
              className="pl-8"
            />
          </div>
        </div>

        {pending ? (
          <div className="flex flex-col gap-3 p-4">
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : orders.isError ? (
          <p className="p-6 text-center text-destructive">{errorMessage(orders.error)}</p>
        ) : all.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-6 py-14 text-center">
            <ShoppingCartIcon className="size-8 text-muted-foreground" />
            <p className="font-medium">还没有订单</p>
            <p className="max-w-sm text-sm text-muted-foreground">
              网页端拿不到个人卖家的卖出订单列表。在闲鱼 App「我卖出的」里复制订单编号，点「添加订单」粘贴进来。
            </p>
            <Button className="mt-2" onClick={() => setAdding(true)}>
              <PlusIcon />
              添加订单
            </Button>
          </div>
        ) : visible.length === 0 ? (
          <p className="px-6 py-14 text-center text-sm text-muted-foreground">没有符合条件的订单</p>
        ) : (
          <div className="px-1">
            <OrdersTable orders={visible} onAction={(kind, order) => setAction({ kind, order })} />
          </div>
        )}
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <HistoryIcon className="size-4 text-muted-foreground" />
            最近的改动
          </CardTitle>
        </CardHeader>
        <CardContent>
          {ops.data ? (
            <OrderOpsList ops={ops.data.slice(0, 20)} titles={titles} />
          ) : (
            <Skeleton className="h-16 w-full" />
          )}
        </CardContent>
      </Card>

      {action?.kind === "detail" && (
        <OrderDetailSheet
          account={account}
          order={action.order}
          onClose={close}
          onChangePrice={() => setAction({ kind: "price", order: action.order })}
        />
      )}
      {action?.kind === "price" && (
        <OrderPriceDialog account={account} order={action.order} onClose={close} />
      )}
      {action?.kind === "remove" && (
        <RemoveOrderDialog account={account} order={action.order} onClose={close} />
      )}
      {adding && (
        <AddOrderDialog
          account={account}
          onClose={() => setAdding(false)}
          onAdded={(order) => {
            setAdding(false)
            setAction({ kind: "detail", order })
          }}
        />
      )}
    </div>
  )
}
