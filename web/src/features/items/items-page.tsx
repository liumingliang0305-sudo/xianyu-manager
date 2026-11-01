import { Loader2Icon, RefreshCwIcon } from "lucide-react"
import { useMemo, useState } from "react"
import { toast } from "sonner"

import { CountFilter } from "@/components/count-filter"
import { PageHeader, Section } from "@/components/page-header"
import { EmptyState, LoadError, RowsSkeleton, SearchInput } from "@/components/states"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { useCurrentAccount } from "@/hooks/use-account"
import { useUrlParam } from "@/hooks/use-url-param"
import { errorMessage, type Item } from "@/lib/api"
import { itemState, timeAgo, type ItemState } from "@/lib/format"

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
  const [tab, setTab] = useUrlParam<Tab>("status", "online", TABS.map((t) => t.value))
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
  const inTab = tab === "all" ? all : byState[tab]
  const visible = inTab.filter(
    (it) => !query || it.title.toLowerCase().includes(query) || it.item_id.includes(query),
  )
  const close = () => setAction(null)

  let body
  if (items.isPending) body = <RowsSkeleton height="h-14" />
  else if (items.isError)
    body = <LoadError what="读取本地商品" error={items.error} onRetry={() => void items.refetch()} />
  else if (all.length === 0)
    body = (
      <EmptyState title="本地还没有商品">
        点右上角「从闲鱼同步」，把主页上的商品拉下来
      </EmptyState>
    )
  else if (visible.length === 0)
    body = (
      <EmptyState>
        {query ? `「${search.trim()}」没有匹配的商品` : `没有${TABS.find((t) => t.value === tab)?.label}的商品`}
      </EmptyState>
    )
  else body = <ItemsTable items={visible} onAction={(kind, item) => setAction({ kind, item })} />

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="商品"
        description={`主页上的商品 · 上次同步 ${items.isPending ? "…" : timeAgo(items.data?.last_synced_at)}`}
        actions={<SyncControls account={account} hasItems={all.length > 0} />}
      />

      <div className="flex flex-col gap-3">
        <CountFilter
          label="商品状态"
          value={tab}
          onChange={setTab}
          options={TABS.map(({ value, label }) => ({
            value,
            label,
            count: items.isPending ? null : value === "all" ? all.length : byState[value].length,
          }))}
        />
        <Card className="gap-0 py-0">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b px-4 py-3">
            <SearchInput
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜标题或商品 ID…"
              aria-label="搜索商品"
              className="w-full sm:w-64"
            />
            {query && inTab.length > 0 && (
              <span className="text-xs text-muted-foreground tabular-nums">
                {visible.length} / {inTab.length} 件
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
          <OpsList ops={[]} />
        ) : (
          <Card className="py-0">
            <div className="px-4">
              <OpsList ops={ops.data.slice(0, 20)} titles={titles} />
            </div>
          </Card>
        )}
      </Section>

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
