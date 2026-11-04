import { Loader2Icon, MessageSquareIcon, PlusIcon, RefreshCwIcon } from "lucide-react"
import { useMemo, useState } from "react"
import { toast } from "sonner"

import { CountFilter } from "@/components/count-filter"
import { PageHeader, Section } from "@/components/page-header"
import { EmptyState, LoadError, RowsSkeleton, SearchInput } from "@/components/states"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { AutoSyncButton } from "@/features/autosync/auto-sync-dialog"
import { useAutoSyncVisit, useSyncJob } from "@/features/autosync/queries"
import { useCurrentAccount } from "@/hooks/use-account"
import { useUrlParam } from "@/hooks/use-url-param"
import { errorMessage, type Order, type OrderSide, type OrderStatus } from "@/lib/api"
import { orderStatusLabel, timeAgo, yuan } from "@/lib/format"

import { AddOrderDialog } from "./add-order-dialog"
import { OrderOpsList } from "./order-bits"
import { OrderDetailSheet } from "./order-detail-sheet"
import { OrderPriceDialog } from "./order-price-dialog"
import { OrdersTable, type OrderActionKind } from "./orders-table"
import { useOrderOps, useOrderStream, useOrders, useScanOrders, useSyncOrders } from "./queries"
import { RemoveOrderDialog } from "./remove-order-dialog"

type Tab = OrderStatus | "all"

const TABS: Tab[] = ["all", "unpaid", "to_ship", "shipped", "refund", "done", "closed"]
const SIDES: OrderSide[] = ["sold", "bought"]
const SIDE_LABEL: Record<OrderSide, string> = { sold: "卖出的", bought: "买到的" }
const PEER_LABEL: Record<OrderSide, string> = { sold: "买家", bought: "卖家" }

// 算成交额（买到的算花了多少）时不算待付款和已关闭的
const PAID: OrderStatus[] = ["to_ship", "shipped", "done", "refund"]

const DAY = 86_400

/**
 * 买到的整页同步"我买到的"；卖出的个人卖家没有列表，是逐个拉本地还没结束的订单，专业卖家整页同步。
 * `primary`：这一边还没有订单时，同步就是第一步，用主按钮。`running`：后台正在同步这一边，先不让点。
 */
function RefreshButton({
  account,
  side,
  primary = false,
  running = false,
}: {
  account: string
  side: OrderSide
  primary?: boolean
  running?: boolean
}) {
  const sync = useSyncOrders(account)
  const busy = sync.isPending || running
  const run = () =>
    sync.mutate(side, {
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
  const label = side === "bought" ? "从闲鱼同步" : "刷新状态"
  return (
    <Button onClick={run} disabled={busy} variant={primary ? "default" : "outline"}>
      {busy ? <Loader2Icon className="animate-spin" /> : <RefreshCwIcon />}
      {busy ? "同步中…" : label}
    </Button>
  )
}

/** 以前存下的消息里出现过的订单（卖出的、买到的）补进来。新来的交易消息后台会自动记，这个补以前的 */
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
          ["平台查不到", r.missing],
          ["拉取失败", r.failed],
        ]
        const parts = groups.filter(([, ids]) => ids.length).map(([label, ids]) => `${label} ${ids.length}`)
        toast.success(`消息里有 ${r.found} 笔订单：${parts.join("，")}`)
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
  const [side, setSide] = useUrlParam<OrderSide>("side", "sold", SIDES)
  useAutoSyncVisit(account, [side])
  const syncing = useSyncJob(account, side)?.running ?? false
  const [tab, setTab] = useUrlParam<Tab>("status", "all", TABS)
  const [search, setSearch] = useState("")
  const [action, setAction] = useState<{ kind: OrderActionKind; order: Order } | null>(null)
  const [adding, setAdding] = useState(false)
  // 打开页面时定下"近 30 天"的起点，不随重新渲染变
  const [since] = useState(() => Date.now() / 1000 - 30 * DAY)

  const everything = useMemo(() => orders.data?.orders ?? [], [orders.data])
  const all = useMemo(() => everything.filter((o) => o.side === side), [everything, side])
  const byStatus = useMemo(() => {
    const groups = new Map<OrderStatus, Order[]>()
    for (const order of all) groups.set(order.status, [...(groups.get(order.status) ?? []), order])
    return groups
  }, [all])
  const titles = useMemo(
    () => new Map(everything.map((o) => [o.order_id, o.title || o.order_id])),
    [everything],
  )
  const recentPaid = useMemo(
    () =>
      all
        .filter((o) => PAID.includes(o.status) && (o.created_at ?? o.first_seen_at) >= since)
        .reduce((sum, o) => sum + (o.price_cent ?? 0), 0),
    [all, since],
  )
  const lastSynced = all.reduce<number | null>((max, o) => Math.max(max ?? 0, o.synced_at), null)

  const counted = (status: OrderStatus) => byStatus.get(status)?.length ?? 0
  const sideCount = (s: OrderSide) => everything.filter((o) => o.side === s).length
  const query = search.trim().toLowerCase()
  const inTab = tab === "all" ? all : (byStatus.get(tab) ?? [])
  const visible = inTab.filter(
    (o) =>
      !query ||
      o.title.toLowerCase().includes(query) ||
      o.order_id.includes(query) ||
      o.peer_nick.toLowerCase().includes(query),
  )
  const pending = orders.isPending
  // 最要紧的那件事：卖出的是待发货，买到的是待付款
  const urgent =
    side === "sold"
      ? counted("to_ship") > 0 && `${counted("to_ship")} 笔待发货，要到闲鱼 App 里发货`
      : counted("unpaid") > 0 && `${counted("unpaid")} 笔待付款`
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
  else if (all.length === 0 && side === "sold")
    body = (
      <EmptyState
        title="还没有卖出的订单"
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
  else if (all.length === 0)
    body = (
      <EmptyState
        title="还没有买到的订单"
        action={<RefreshButton account={account} side={side} primary running={syncing} />}
      >
        {"点「从闲鱼同步」把闲鱼「我买到的」整页拉下来；之后交易消息里的买到的订单会自动记，" +
          "后台也会定时同步最新的几页。"}
      </EmptyState>
    )
  else if (visible.length === 0)
    body = (
      <EmptyState>
        {query
          ? `「${search.trim()}」没有匹配的订单`
          : `没有${tab === "all" ? "" : orderStatusLabel(tab, side)}的订单`}
      </EmptyState>
    )
  else
    body = (
      <OrdersTable
        orders={visible}
        side={side}
        onAction={(kind, order) => setAction({ kind, order })}
      />
    )

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="订单"
        description={
          pending ? (
            `${SIDE_LABEL[side]}订单`
          ) : (
            <>
              {urgent ? (
                <span className="text-foreground">{urgent}</span>
              ) : (
                `${SIDE_LABEL[side]}订单`
              )}
              <span title="不含待付款和已关闭的订单">
                {" "}
                · 近 30 天{side === "sold" ? "成交" : "花了"}{" "}
                <span className="tabular-nums">{yuan(recentPaid)}</span>
              </span>
              {" "}· 上次更新 {timeAgo(lastSynced)}
            </>
          )
        }
        actions={
          <>
            <AutoSyncButton account={account} job={side} />
            {all.length > 0 && (
              <>
                <ScanButton account={account} />
                <RefreshButton account={account} side={side} running={syncing} />
                {add}
              </>
            )}
          </>
        }
      />

      <div className="flex flex-col gap-3">
        <Tabs value={side} onValueChange={(v) => setSide(v as OrderSide)}>
          <TabsList variant="line" aria-label="卖出的还是买到的" className="h-9 gap-6 p-0">
            {SIDES.map((s) => (
              <TabsTrigger
                key={s}
                value={s}
                className="flex-none px-0 group-data-horizontal/tabs:after:bottom-0"
              >
                {SIDE_LABEL[s]}
                {!pending && (
                  <span className="text-muted-foreground tabular-nums">{sideCount(s)}</span>
                )}
              </TabsTrigger>
            ))}
          </TabsList>
        </Tabs>
        <CountFilter
          label="订单状态"
          value={tab}
          onChange={setTab}
          options={TABS.map((value) => ({
            value,
            label: value === "all" ? "全部" : orderStatusLabel(value, side),
            count: pending ? null : value === "all" ? all.length : counted(value),
          }))}
        />
        <Card className="gap-0 py-0">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b px-4 py-3">
            <SearchInput
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder={`搜商品、订单号或${PEER_LABEL[side]}…`}
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
