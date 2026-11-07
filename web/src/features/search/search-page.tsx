import { CheckIcon, Loader2Icon, SearchIcon } from "lucide-react"
import { useMemo, useState, type ComponentProps } from "react"
import { useSearchParams } from "react-router"
import { toast } from "sonner"

import { PageHeader } from "@/components/page-header"
import { EmptyState, LoadError, RowsSkeleton, SearchInput } from "@/components/states"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useCurrentAccount } from "@/hooks/use-account"
import { useUrlParam } from "@/hooks/use-url-param"
import { useExtension } from "@/lib/extension"
import {
  errorMessage,
  type SearchConditions,
  type SearchFilter,
  type SearchItem,
  type SearchSort,
} from "@/lib/api"
import { count, parseYuan, timeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"

import {
  useFetchStashDetail,
  useSearch,
  useStash,
  useStashItem,
  useUnstashItem,
} from "./queries"
import { ResultsTable } from "./results-table"
import { StashPanel } from "./stash-panel"

const SORTS: { value: SearchSort; label: string }[] = [
  { value: "", label: "综合" },
  { value: "new", label: "最新发布" },
  { value: "price_asc", label: "价格从低到高" },
  { value: "price_desc", label: "价格从高到低" },
  { value: "reduce", label: "新降价" },
  { value: "active", label: "最近活跃" },
  { value: "credit", label: "信用排序" },
]

const DAYS = [1, 3, 7, 14]

const FILTERS: { value: SearchFilter; label: string }[] = [
  { value: "personal", label: "个人闲置" },
  { value: "free_ship", label: "包邮" },
  { value: "brand_new", label: "全新" },
  { value: "appraise", label: "验货宝" },
]

const BLANK: SearchConditions = {
  keyword: "",
  sort: "",
  publish_days: null,
  min_price: "",
  max_price: "",
  filters: [],
}

/**
 * 搜索条件放在网址里（?q=机械键盘&sort=new&f=personal,free_ship），刷新、前进后退还是这次搜索。
 * 和列表页"搜索框的字不放进网址"不一样：这里的搜索词是这一页要看的东西，只在点搜索时写进网址。
 * 网址里没有搜索词时是 null。
 */
function readConditions(params: URLSearchParams): SearchConditions | null {
  const keyword = params.get("q")?.trim() ?? ""
  if (!keyword) return null
  const price = (key: string) => {
    const value = params.get(key)?.trim() ?? ""
    return parseYuan(value) != null ? value : ""
  }
  const days = Number(params.get("days"))
  const picked = new Set((params.get("f") ?? "").split(","))
  return {
    keyword,
    sort: SORTS.find((s) => s.value === params.get("sort"))?.value ?? "",
    publish_days: DAYS.includes(days) ? days : null,
    min_price: price("min"),
    max_price: price("max"),
    filters: FILTERS.filter((f) => picked.has(f.value)).map((f) => f.value),
  }
}

function writeConditions(c: SearchConditions): URLSearchParams {
  const params = new URLSearchParams({ q: c.keyword })
  if (c.sort) params.set("sort", c.sort)
  if (c.publish_days) params.set("days", String(c.publish_days))
  if (c.min_price) params.set("min", c.min_price)
  if (c.max_price) params.set("max", c.max_price)
  if (c.filters.length) params.set("f", c.filters.join(","))
  return params
}

function priceError(min: string, max: string): string | null {
  const low = min.trim() ? parseYuan(min) : 0
  const high = max.trim() ? parseYuan(max) : 0
  if (low == null || high == null) return "价格填数字，最多两位小数"
  if (low && high && low > high) return "最低价不能高于最高价"
  return null
}

function NativeSelect({ className, ...props }: ComponentProps<"select">) {
  return (
    <select
      className={cn(
        "h-8 rounded-lg border border-input bg-transparent px-2 text-sm transition-colors outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 dark:bg-input/30",
        className,
      )}
      {...props}
    />
  )
}

/**
 * 搜索条件。搜索词和价格按回车或"搜索"才生效；已经搜过时，排序、发布时间、勾选项一改就重新搜。
 * 网址变了（前进后退）由外面换 key 重新挂载，草稿跟着网址走。
 */
function SearchForm({
  initial,
  pending,
  onSearch,
}: {
  initial: SearchConditions | null
  pending: boolean
  onSearch: (conditions: SearchConditions) => void
}) {
  const [draft, setDraft] = useState<SearchConditions>(initial ?? BLANK)
  const [errors, setErrors] = useState<{ keyword?: string; price?: string }>({})

  const submit = (next: SearchConditions) => {
    const keyword = next.keyword.trim()
    const price = priceError(next.min_price, next.max_price)
    setErrors({ keyword: keyword ? undefined : "先填要搜的词", price: price ?? undefined })
    if (!keyword || price) return
    onSearch({
      ...next,
      keyword,
      min_price: next.min_price.trim(),
      max_price: next.max_price.trim(),
    })
  }

  const change = (patch: Partial<SearchConditions>) => {
    const next = { ...draft, ...patch }
    setDraft(next)
    if (initial) submit(next)
  }

  const toggle = (filter: SearchFilter) =>
    change({
      filters: FILTERS.map((f) => f.value).filter(
        (v) => (v === filter) !== draft.filters.includes(v),
      ),
    })

  return (
    <form
      role="search"
      noValidate
      onSubmit={(e) => {
        e.preventDefault()
        submit(draft)
      }}
      className="flex flex-col gap-3 border-b px-4 py-4"
    >
      <div className="flex items-start gap-2">
        <div className="min-w-0 flex-1">
          <SearchInput
            value={draft.keyword}
            onChange={(e) => setDraft({ ...draft, keyword: e.target.value })}
            placeholder="搜闲鱼上的商品，比如 机械键盘…"
            aria-label="搜索词"
            aria-invalid={errors.keyword ? true : undefined}
            aria-describedby={errors.keyword ? "search-keyword-error" : undefined}
            autoFocus={!initial}
          />
          {errors.keyword && (
            <p id="search-keyword-error" role="alert" className="mt-1 text-xs text-destructive">
              {errors.keyword}
            </p>
          )}
        </div>
        <Button type="submit" disabled={pending}>
          {pending ? <Loader2Icon className="animate-spin" /> : <SearchIcon />}
          搜索
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-sm">
        <div className="flex items-center gap-2">
          <label htmlFor="search-sort" className="text-muted-foreground">
            排序
          </label>
          <NativeSelect
            id="search-sort"
            value={draft.sort}
            onChange={(e) => change({ sort: e.target.value as SearchSort })}
          >
            {SORTS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </NativeSelect>
        </div>

        <div className="flex items-center gap-2">
          <label htmlFor="search-days" className="text-muted-foreground">
            发布
          </label>
          <NativeSelect
            id="search-days"
            value={draft.publish_days ?? ""}
            onChange={(e) => change({ publish_days: e.target.value ? Number(e.target.value) : null })}
          >
            <option value="">不限</option>
            {DAYS.map((d) => (
              <option key={d} value={d}>
                {d} 天内
              </option>
            ))}
          </NativeSelect>
        </div>

        <div role="group" aria-labelledby="search-price-label" className="flex items-center gap-2">
          <span id="search-price-label" className="text-muted-foreground">
            价格
          </span>
          <Input
            value={draft.min_price}
            onChange={(e) => setDraft({ ...draft, min_price: e.target.value })}
            inputMode="decimal"
            autoComplete="off"
            placeholder="最低…"
            aria-label="最低价（元）"
            aria-invalid={errors.price ? true : undefined}
            aria-describedby={errors.price ? "search-price-error" : undefined}
            className="w-20 tabular-nums"
          />
          <span className="text-muted-foreground" aria-hidden>
            –
          </span>
          <Input
            value={draft.max_price}
            onChange={(e) => setDraft({ ...draft, max_price: e.target.value })}
            inputMode="decimal"
            autoComplete="off"
            placeholder="最高…"
            aria-label="最高价（元）"
            aria-invalid={errors.price ? true : undefined}
            aria-describedby={errors.price ? "search-price-error" : undefined}
            className="w-20 tabular-nums"
          />
        </div>

        <div role="group" aria-label="只看" className="flex items-center gap-1.5">
          {FILTERS.map((f) => {
            const on = draft.filters.includes(f.value)
            return (
              <Button
                key={f.value}
                type="button"
                variant="outline"
                size="sm"
                aria-pressed={on}
                onClick={() => toggle(f.value)}
                className="aria-pressed:border-input aria-pressed:bg-secondary aria-pressed:text-foreground"
              >
                {on && <CheckIcon />}
                {f.label}
              </Button>
            )
          })}
        </div>
      </div>

      {errors.price && (
        <p id="search-price-error" role="alert" className="text-xs text-destructive">
          {errors.price}
        </p>
      )}
    </form>
  )
}

// 不加筛选时平台报的是最多能翻到的件数（50 页 × 30），实际不止
const REACHABLE = 1500

function totalText(total: number | null | undefined): string {
  if (total == null) return "闲鱼上…"
  return total === REACHABLE ? `闲鱼上 ${count(REACHABLE)} 件以上` : `闲鱼上约 ${count(total)} 件`
}

/** 翻过的几页合在一起；同一件商品在后一页又出现时只留第一次 */
function merge(pages: { items: SearchItem[] }[] | undefined): SearchItem[] {
  const seen = new Set<string>()
  const items: SearchItem[] = []
  for (const page of pages ?? []) {
    for (const item of page.items) {
      if (!seen.has(item.item_id)) {
        seen.add(item.item_id)
        items.push(item)
      }
    }
  }
  return items
}

type View = "results" | "stash"
const VIEWS: readonly View[] = ["results", "stash"]

/**
 * 结果里的「暂存」开关：没存的存下（记上这次的搜索词），存了的移出。
 * 装了浏览器插件的，存好以后接着请插件从 Chrome 里拿详情（完整描述、全部图片）
 */
function useStashToggle(keyword: string) {
  const stash = useStash()
  const extension = useExtension()
  const fetchDetail = useFetchStashDetail()
  const save = useStashItem((saved) => {
    if (extension) fetchDetail.mutate({ itemId: saved.item_id, title: saved.title })
  })
  const unstash = useUnstashItem()
  const stashed = useMemo(
    () => (stash.data ? new Set(stash.data.map((it) => it.item_id)) : null),
    [stash.data],
  )
  const toggle = (item: SearchItem) => {
    const saved = stash.data?.find((it) => it.item_id === item.item_id)
    if (saved)
      unstash.mutate(saved, {
        onError: (error) => toast.error(`移出暂存失败：${errorMessage(error)}`),
      })
    else
      save.mutate({ item, keyword })
  }
  return { stash, stashed, toggle }
}

export function SearchPage() {
  const account = useCurrentAccount()
  const [params, setParams] = useSearchParams()
  const [view, setView] = useUrlParam<View>("view", "results", VIEWS)
  const conditions = useMemo(() => readConditions(params), [params])
  // 看暂存的时候不搜：直接打开 ?q=…&view=stash 不该请求闲鱼
  const search = useSearch(account, view === "results" ? conditions : null)
  const items = useMemo(() => merge(search.data?.pages), [search.data])
  const total = search.data?.pages[0]?.total
  const { stash, stashed, toggle } = useStashToggle(conditions?.keyword ?? "")
  const stashCount = stash.data?.length

  let body
  if (view === "stash")
    body = <StashPanel onSearch={(keyword) => setParams(writeConditions({ ...BLANK, keyword }))} />
  else if (!conditions)
    body = (
      <EmptyState title="输入要搜的词">
        搜的是闲鱼上所有卖家的在售商品，每翻一页用当前账号请求一次闲鱼。想留着以后用的点「暂存」，
        装了浏览器插件会顺带从你的 Chrome 里拿完整描述和全部图片
      </EmptyState>
    )
  else if (search.isPending) body = <RowsSkeleton rows={6} height="h-14" />
  else if (search.isError && items.length === 0)
    body = <LoadError what="搜索" error={search.error} onRetry={() => void search.refetch()} />
  else if (items.length === 0) {
    const narrowed = conditions.publish_days || conditions.min_price || conditions.max_price
    body = (
      <EmptyState title={`「${conditions.keyword}」没有搜到商品`}>
        {narrowed || conditions.filters.length ? "放宽价格、发布时间或去掉勾选的条件再试试" : "换个说法再试试"}
      </EmptyState>
    )
  } else
    body = (
      <>
        <ResultsTable items={items} stashed={stashed} onToggleStash={toggle} />
        {search.isFetchNextPageError && (
          <LoadError
            what="加载下一页"
            error={search.error}
            onRetry={() => void search.fetchNextPage()}
            className="border-t py-6"
          />
        )}
        <div className="flex items-center justify-between gap-4 border-t px-4 py-3">
          <span className="text-xs text-muted-foreground tabular-nums">
            已显示 {count(items.length)} 件
          </span>
          {search.hasNextPage ? (
            <Button
              variant="outline"
              size="sm"
              onClick={() => void search.fetchNextPage()}
              disabled={search.isFetchingNextPage}
            >
              {search.isFetchingNextPage && <Loader2Icon className="animate-spin" />}
              {search.isFetchingNextPage ? "加载中…" : "再加载 30 件"}
            </Button>
          ) : (
            <span className="text-xs text-muted-foreground">没有更多了</span>
          )}
        </div>
      </>
    )

  let description
  if (view === "stash")
    description = stash.data?.length
      ? `暂存了 ${count(stash.data.length)} 件 · 最近暂存 ${timeAgo(stash.data[0].saved_at)}`
      : "搜索时点「暂存」留下的商品，名下所有闲鱼账号共用"
  else
    description = conditions
      ? `「${conditions.keyword}」${totalText(total)}`
      : "闲鱼上所有卖家的在售商品"

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="搜索" description={description} />
      <div className="flex flex-col gap-3">
        <Tabs value={view} onValueChange={(v) => setView(v as View)}>
          <TabsList variant="line" aria-label="搜索结果还是暂存" className="h-9 gap-6 p-0">
            <TabsTrigger
              value="results"
              className="flex-none px-0 group-data-horizontal/tabs:after:bottom-0"
            >
              搜索结果
            </TabsTrigger>
            <TabsTrigger
              value="stash"
              className="flex-none px-0 group-data-horizontal/tabs:after:bottom-0"
            >
              暂存
              {stashCount != null && (
                <span className="text-muted-foreground tabular-nums">{count(stashCount)}</span>
              )}
            </TabsTrigger>
          </TabsList>
        </Tabs>
        <Card className="gap-0 py-0">
          {view === "results" && (
            <SearchForm
              key={params.toString()}
              initial={conditions}
              pending={search.isFetching && !search.isFetchingNextPage}
              onSearch={(next) => setParams(writeConditions(next))}
            />
          )}
          {body}
        </Card>
      </div>
    </div>
  )
}
