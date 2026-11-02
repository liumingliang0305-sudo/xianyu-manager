import { Loader2Icon, MessageSquareIcon, PlusIcon, RefreshCwIcon } from "lucide-react"
import { useMemo, useState } from "react"
import { toast } from "sonner"

import { CountFilter } from "@/components/count-filter"
import { PageHeader, Section } from "@/components/page-header"
import { EmptyState, LoadError, RowsSkeleton, SearchInput } from "@/components/states"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { useCurrentAccount } from "@/hooks/use-account"
import { useUrlParam } from "@/hooks/use-url-param"
import { errorMessage, type Order, type OrderStatus } from "@/lib/api"
import { ORDER_STATUS_LABEL, timeAgo, yuan } from "@/lib/format"

import { AddOrderDialog } from "./add-order-dialog"
import { OrderOpsList } from "./order-bits"
import { OrderDetailSheet } from "./order-detail-sheet"
import { OrderPriceDialog } from "./order-price-dialog"
import { OrdersTable, type OrderActionKind } from "./orders-table"
import { useOrderOps, useOrderStream, useOrders, useScanOrders, useSyncOrders } from "./queries"
import { RemoveOrderDialog } from "./remove-order-dialog"

type Tab = OrderStatus | "all"

const TABS: Tab[] = ["all", "unpaid", "to_ship", "shipped", "refund", "done", "closed"]

// 算成交额时不算待付款和已关闭的
const SOLD: OrderStatus[] = ["to_ship", "shipped", "done", "refund"]

const DAY = 86_400

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

/** 以前存下的消息里出现过的卖出订单补进来。新来的交易消息后台会自动记，这个补以前的 */
function ScanButton({ account }: { account: string }) {
  const scan = useScanOrders(account)
  const run = () =>
    scan.mutate(undefined, {
      onSuccess: (r) => {
        if (r.found === 0) {
          toast.success("本地存的消息里没有找到订单")
          return
        }
        const groups: [string, string[]][] = [
          ["新记下", r.added],
          ["刷新了", r.updated],
          ["本地已结束", r.skipped],
          ["是买到的或查不到", r.not_mine],
          ["拉取失败", r.failed],
        ]
        const parts = groups.filter(([, ids]) => ids.length).map(([label, ids]) => `${label} ${ids.length}`)
        toast.success(
          `消息里有 ${r.found} 笔订单` +
            (r.bought ? `（${r.bought} 笔是买到的，没拉）` : "") +
            (parts.length ? `：${parts.join("，")}` : ""),
        )
      },
      onError: (error) => toast.error(errorMessage(error)),
    })
  return (
    <Button onClick={run} disabled={scan.isPending} variant="outline">
      {scan.isPending ? <Loader2Icon className="animate-spin" /> : <MessageSquareIcon />}
      {scan.isPending ? "查找中…" : "从消息找订单"}
    </Button>
  )
}

export function OrdersPage() {
  const account = useCurrentAccount()
  useOrderStream(account)
  const orders = useOrders(account)
  const ops = useOrderOps(account)
  const [tab, setTab] = useUrlParam<Tab>("status", "all", TABS)
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
  const inTab = tab === "all" ? all : (byStatus.get(tab) ?? [])
  const visible = inTab.filter(
    (o) =>
      !query ||
      o.title.toLowerCase().includes(query) ||
      o.order_id.includes(query) ||
      o.buyer_nick.toLowerCase().includes(query),
  )
  const pending = orders.isPending
  const toShip = counted("to_ship")
  const close = () => setAction(null)
  const add = (
    <Button onClick={() => setAdding(true)}>
      <PlusIcon />
      添加订单
    </Button>
  )

  let body
  if (pending) body = <RowsSkeleton height="h-14" />
  else if (orders.isError)
    body = <LoadError what="读取本地订单" error={orders.error} onRetry={() => void orders.refetch()} />
  else if (all.length === 0)
    body = (
      <EmptyState
        title="还没有订单"
        action={
          <div className="flex items-center gap-2">
            <ScanButton account={account} />
            {add}
          </div>
        }
      >
        {"收到交易消息（买家拍下、付款）时会自动记下来。以前的订单点「从消息找订单」从存下的消息里补，" +
          "消息里也没有的在闲鱼 App「我卖出的」里复制订单编号，点「添加订单」粘贴进来。"}
      </EmptyState>
    )
  else if (visible.length === 0)
    body = (
      <EmptyState>
        {query
          ? `「${search.trim()}」没有匹配的订单`
          : `没有${tab === "all" ? "" : ORDER_STATUS_LABEL[tab]}的订单`}
      </EmptyState>
    )
  else body = <OrdersTable orders={visible} onAction={(kind, order) => setAction({ kind, order })} />

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="订单"
        description={
          pending ? (
            "卖出的订单"
          ) : (
            <>
              {toShip > 0 ? (
                <span className="text-foreground">{toShip} 笔待发货，要到闲鱼 App 里发货</span>
              ) : (
                "卖出的订单"
              )}
              <span title="不含待付款和已关闭的订单">
                {" "}
                · 近 30 天成交 <span className="tabular-nums">{yuan(recentSales)}</span>
              </span>
              {" "}· 上次更新 {timeAgo(orders.data?.last_synced_at)}
            </>
          )
        }
        actions={
          <>
            {all.length > 0 && <ScanButton account={account} />}
            {all.length > 0 && <RefreshButton account={account} />}
            {all.length > 0 && add}
          </>
        }
      />

      <div className="flex flex-col gap-3">
        <CountFilter
          label="订单状态"
          value={tab}
          onChange={setTab}
          options={TABS.map((value) => ({
            value,
            label: value === "all" ? "全部" : ORDER_STATUS_LABEL[value],
            count: pending ? null : value === "all" ? all.length : counted(value),
          }))}
        />
        <Card className="gap-0 py-0">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b px-4 py-3">
            <SearchInput
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜商品、订单号或买家…"
              aria-label="搜索订单"
              className="w-full sm:w-64"
            />
            {query && inTab.length > 0 && (
              <span className="text-xs text-muted-foreground tabular-nums">
                {visible.length} / {inTab.length} 笔
              </span>
            )}
          </div>
          {body}
        </Card>
      </div>

      <Section title="最近的改动" aside="通过本系统做的，成功失败都记">
        {!ops.data ? (
          <Skeleton className="h-16 w-full" />
        ) : ops.data.length === 0 ? (
          <OrderOpsList ops={[]} />
        ) : (
          <Card className="py-0">
            <div className="px-4">
              <OrderOpsList ops={ops.data.slice(0, 20)} titles={titles} />
            </div>
          </Card>
        )}
      </Section>

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
