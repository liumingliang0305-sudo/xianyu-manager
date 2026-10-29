import { HistoryIcon, Loader2Icon, PackageIcon, RefreshCwIcon, SearchIcon } from "lucide-react"
import { useMemo, useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useCurrentAccount } from "@/hooks/use-account"
import { errorMessage, type Item } from "@/lib/api"
import { count, itemState, timeAgo, type ItemState } from "@/lib/format"

import { ConfirmItemDialog } from "./confirm-dialog"
import { DescDialog } from "./desc-dialog"
import { ItemDetailSheet } from "./item-detail-sheet"
import { ItemsTable, type ItemActionKind } from "./items-table"
import { OpsList } from "./ops-list"
import { PriceDialog } from "./price-dialog"
import { useItemOps, useItems, useSyncItems } from "./queries"

type Tab = ItemState | "all"

const TABS: { value: Tab; label: string }[] = [
  { value: "online", label: "在售" },
  { value: "sold", label: "已卖出" },
  { value: "gone", label: "不在主页" },
  { value: "all", label: "全部" },
]

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

function sum(items: Item[], key: "want_count" | "browse_count" | "collect_count"): number | null {
  const values = items.map((it) => it[key])
  if (values.length === 0 || values.some((v) => v == null)) return null
  return values.reduce<number>((a, b) => a + (b ?? 0), 0)
}

function SyncControls({ account, hasItems }: { account: string; hasItems: boolean }) {
  const [withStats, setWithStats] = useState(true)
  const sync = useSyncItems(account)

  const run = () =>
    sync.mutate(withStats, {
      onSuccess: (r) =>
        toast.success(
          `同步完成：新增 ${r.added.length}，更新 ${r.updated.length}，不在主页 ${r.gone.length}`,
        ),
      onError: (error) => toast.error(errorMessage(error)),
    })

  return (
    <div className="flex items-center gap-3">
      <Tooltip>
        <TooltipTrigger asChild>
          <div className="flex items-center gap-2">
            <Switch id="with-stats" checked={withStats} onCheckedChange={setWithStats} />
            <Label htmlFor="with-stats" className="font-normal text-muted-foreground">
              含浏览/收藏
            </Label>
          </div>
        </TooltipTrigger>
        <TooltipContent>逐个拉在售商品的详情，商品多时会慢一些</TooltipContent>
      </Tooltip>
      <Button onClick={run} disabled={sync.isPending} variant={hasItems ? "outline" : "default"}>
        {sync.isPending ? <Loader2Icon className="animate-spin" /> : <RefreshCwIcon />}
        {sync.isPending ? "同步中…" : "从闲鱼同步"}
      </Button>
    </div>
  )
}

export function ItemsPage() {
  const account = useCurrentAccount()
  const items = useItems(account)
  const ops = useItemOps(account)
  const [tab, setTab] = useState<Tab>("online")
  const [search, setSearch] = useState("")
  const [action, setAction] = useState<{ kind: ItemActionKind; item: Item } | null>(null)

  const all = useMemo(() => items.data?.items ?? [], [items.data])
  const byState = useMemo(() => {
    const groups: Record<ItemState, Item[]> = { online: [], sold: [], gone: [] }
    for (const item of all) groups[itemState(item)].push(item)
    return groups
  }, [all])
  const titles = useMemo(() => new Map(all.map((it) => [it.item_id, it.title])), [all])

  const query = search.trim().toLowerCase()
  const visible = (tab === "all" ? all : byState[tab]).filter(
    (it) => !query || it.title.toLowerCase().includes(query) || it.item_id.includes(query),
  )
  const online = byState.online
  const close = () => setAction(null)

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-heading text-2xl font-semibold">商品</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            上次同步：{items.isPending ? "…" : timeAgo(items.data?.last_synced_at)}
          </p>
        </div>
        <SyncControls account={account} hasItems={all.length > 0} />
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <SummaryCard label="在售" value={items.isPending ? "…" : String(online.length)} />
        <SummaryCard label="已卖出" value={items.isPending ? "…" : String(byState.sold.length)} />
        <SummaryCard label="在售 · 想要" value={count(sum(online, "want_count"))} />
        <SummaryCard
          label="在售 · 浏览"
          value={count(sum(online, "browse_count"))}
          hint={online.length > 0 && sum(online, "browse_count") == null ? "同步时打开「含浏览/收藏」" : undefined}
        />
      </div>

      <Card className="gap-0 py-0">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b p-3">
          <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
            <TabsList>
              {TABS.map(({ value, label }) => (
                <TabsTrigger key={value} value={value}>
                  {label}
                  <span className="text-xs text-muted-foreground tabular-nums">
                    {value === "all" ? all.length : byState[value].length}
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
              placeholder="搜标题或商品 ID"
              className="pl-8"
            />
          </div>
        </div>

        {items.isPending ? (
          <div className="flex flex-col gap-3 p-4">
            {Array.from({ length: 4 }, (_, i) => (
              <Skeleton key={i} className="h-12 w-full" />
            ))}
          </div>
        ) : items.isError ? (
          <p className="p-6 text-center text-destructive">{errorMessage(items.error)}</p>
        ) : all.length === 0 ? (
          <div className="flex flex-col items-center gap-2 px-6 py-14 text-center">
            <PackageIcon className="size-8 text-muted-foreground" />
            <p className="font-medium">本地还没有商品</p>
            <p className="text-sm text-muted-foreground">点右上角「从闲鱼同步」把主页上的商品拉下来</p>
          </div>
        ) : visible.length === 0 ? (
          <p className="px-6 py-14 text-center text-sm text-muted-foreground">没有符合条件的商品</p>
        ) : (
          <div className="px-1">
            <ItemsTable items={visible} onAction={(kind, item) => setAction({ kind, item })} />
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
            <OpsList ops={ops.data.slice(0, 20)} titles={titles} />
          ) : (
            <Skeleton className="h-16 w-full" />
          )}
        </CardContent>
      </Card>

      {action?.kind === "detail" && (
        <ItemDetailSheet account={account} item={action.item} onClose={close} />
      )}
      {action?.kind === "price" && (
        <PriceDialog account={account} item={action.item} onClose={close} />
      )}
      {action?.kind === "desc" && <DescDialog account={account} item={action.item} onClose={close} />}
      {(action?.kind === "downshelf" || action?.kind === "delete") && (
        <ConfirmItemDialog account={account} item={action.item} kind={action.kind} onClose={close} />
      )}
    </div>
  )
}
