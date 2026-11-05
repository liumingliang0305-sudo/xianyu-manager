import {
  EllipsisIcon,
  ExternalLinkIcon,
  EyeIcon,
  ImageDownIcon,
  Loader2Icon,
  SquarePenIcon,
  Trash2Icon,
} from "lucide-react"
import { useMemo, useState } from "react"
import { useNavigate, useSearchParams } from "react-router"
import { toast } from "sonner"

import { PageHeader } from "@/components/page-header"
import { EmptyState, LoadError, RowsSkeleton, SearchInput } from "@/components/states"
import { StatusPill } from "@/components/status-pill"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
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
import { useAccount } from "@/hooks/use-account"
import { errorMessage, type Account, type PddGoodsSummary } from "@/lib/api"
import { count, dateTime, timeAgo } from "@/lib/format"

import { DeletePddDialog } from "./delete-dialog"
import { PddDetailSheet } from "./pdd-detail-sheet"
import { PddThumb } from "./pdd-bits"
import { priceRange } from "./pdd-format"
import { useDraftFromPdd, useFetchPddImages, usePddGoods } from "./queries"

/** 账号在页面上的名字：昵称，资料还没拉到时是账号 ID */
const accountName = (account: Account) => account.profile.nick || account.id

/** 打开的货源放在网址里（?goods=…），插件弹窗里的「到管理后台查看」直接打开它 */
function useOpenGoods(): [string | null, (goodsId: string | null) => void] {
  const [params, setParams] = useSearchParams()
  const open = (goodsId: string | null) =>
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev)
        if (goodsId) p.set("goods", goodsId)
        else p.delete("goods")
        return p
      },
      { replace: true },
    )
  return [params.get("goods"), open]
}

function GoodsMenu({
  goods,
  onOpen,
  onFetchImages,
  onDelete,
}: {
  goods: PddGoodsSummary
  onOpen: () => void
  onFetchImages: () => void
  onDelete: () => void
}) {
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label={`${goods.title} 的更多操作`}>
          <EllipsisIcon />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-40">
        <DropdownMenuItem onSelect={onOpen}>
          <EyeIcon />
          查看详情
        </DropdownMenuItem>
        <DropdownMenuItem asChild>
          <a href={goods.url} target="_blank" rel="noreferrer">
            <ExternalLinkIcon />
            在拼多多打开
          </a>
        </DropdownMenuItem>
        {goods.missing_images > 0 && (
          <DropdownMenuItem onSelect={onFetchImages}>
            <ImageDownIcon />
            重新下载图片
          </DropdownMenuItem>
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onSelect={onDelete}>
          <Trash2Icon />
          删除
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

export function PddPage() {
  const goods = usePddGoods()
  const [search, setSearch] = useState("")
  const [openId, setOpenId] = useOpenGoods()
  const [removing, setRemoving] = useState<PddGoodsSummary | null>(null)
  const { accounts, account } = useAccount()
  const draft = useDraftFromPdd(account)
  const fetchImages = useFetchPddImages()
  const navigate = useNavigate()

  const current = accounts.find((a) => a.id === account)
  const makeDraft = (goodsId: string) => {
    if (!account || draft.isPending) return
    draft.mutate(goodsId, {
      onSuccess: (d) => {
        toast.success(`已在「${current ? accountName(current) : account}」建好草稿，填上价格就能发`)
        navigate(`/publish?draft=${d.id}`)
      },
      onError: (error) => toast.error(`建草稿失败：${errorMessage(error)}`),
    })
  }
  const refetchImages = (goodsId: string) =>
    fetchImages.mutate(goodsId, {
      onSuccess: (r) =>
        r.saved === r.total
          ? toast.success(`图片都下载好了（${r.total} 张）`)
          : toast.error(`还有 ${r.total - r.saved} 张没下载下来，过一会儿再试`),
      onError: (error) => toast.error(`下载图片失败：${errorMessage(error)}`),
    })
  const drafting = draft.isPending ? draft.variables : null
  const fetching = fetchImages.isPending ? fetchImages.variables : null

  const all = useMemo(() => goods.data ?? [], [goods.data])
  const query = search.trim().toLowerCase()
  const visible = all.filter(
    (g) =>
      !query ||
      g.title.toLowerCase().includes(query) ||
      g.mall_name.toLowerCase().includes(query) ||
      g.goods_id.includes(query),
  )
  const incomplete = all.filter((g) => g.missing_images > 0).length

  let description = "浏览器插件从拼多多商品页导入的商品，名下所有闲鱼账号共用"
  if (all.length > 0) {
    description = [
      incomplete > 0 ? `${incomplete} 件图片没下全` : null,
      `共 ${all.length} 件`,
      `最近导入 ${timeAgo(all[0].updated_at)}`,
    ]
      .filter(Boolean)
      .join(" · ")
  }

  let body
  if (goods.isPending) body = <RowsSkeleton height="h-14" />
  else if (goods.isError)
    body = <LoadError what="读取货源" error={goods.error} onRetry={() => void goods.refetch()} />
  else if (all.length === 0)
    body = (
      <EmptyState title="还没有导入过拼多多商品">
        在装了插件的 Chrome 里登录拼多多、打开一个商品页（mobile.yangkeduo.com），点插件图标里的「导入这个商品」。
        标题、图片、规格、参数会存到这里，图片下载到后台
      </EmptyState>
    )
  else if (visible.length === 0) body = <EmptyState>「{search.trim()}」没有匹配的商品</EmptyState>
  else
    body = (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-full min-w-72">商品</TableHead>
            <TableHead className="text-right">拼单价</TableHead>
            <TableHead className="text-right">规格</TableHead>
            <TableHead>图片</TableHead>
            <TableHead>导入</TableHead>
            <TableHead>
              <span className="sr-only">操作</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {visible.map((g) => (
            <TableRow key={g.goods_id}>
              <TableCell className="whitespace-normal">
                <button
                  type="button"
                  onClick={() => setOpenId(g.goods_id)}
                  className="group flex min-w-0 items-center gap-3 rounded-md text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  <PddThumb url={g.cover} />
                  <div className="min-w-0">
                    <div className="line-clamp-2 font-medium group-hover:underline">{g.title}</div>
                    <div className="mt-0.5 truncate text-xs text-muted-foreground">
                      <span className="font-mono" translate="no">
                        {g.goods_id}
                      </span>
                      {g.mall_name && ` · ${g.mall_name}`}
                    </div>
                  </div>
                </button>
              </TableCell>
              <TableCell className="text-right font-medium tabular-nums">
                {priceRange(g.min_price_cent, g.max_price_cent)}
              </TableCell>
              <TableCell className="text-right tabular-nums">{count(g.sku_count)}</TableCell>
              <TableCell className="text-xs text-muted-foreground">
                <div className="tabular-nums">
                  主图 {g.image_count} · 详情 {g.detail_count}
                </div>
                {g.missing_images > 0 && (
                  <StatusPill tone="warning" className="mt-1">
                    {g.missing_images} 张没下载
                  </StatusPill>
                )}
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                <span title={dateTime(g.updated_at)}>{timeAgo(g.updated_at)}</span>
              </TableCell>
              <TableCell>
                <div className="flex items-center justify-end gap-1">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!account || drafting === g.goods_id}
                    onClick={() => makeDraft(g.goods_id)}
                  >
                    {drafting === g.goods_id ? (
                      <Loader2Icon className="animate-spin" />
                    ) : (
                      <SquarePenIcon />
                    )}
                    建草稿
                  </Button>
                  <GoodsMenu
                    goods={g}
                    onOpen={() => setOpenId(g.goods_id)}
                    onFetchImages={() => refetchImages(g.goods_id)}
                    onDelete={() => setRemoving(g)}
                  />
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    )

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="拼多多货源" description={description} />

      <Card className="gap-0 py-0">
        <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b px-4 py-3">
          <SearchInput
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="搜标题、店铺或商品 ID…"
            aria-label="搜索货源"
            className="w-full sm:w-64"
          />
          <span className="text-xs text-muted-foreground tabular-nums">
            {query && all.length > 0
              ? `${visible.length} / ${all.length} 件`
              : current
                ? `草稿建在当前账号「${accountName(current)}」`
                : "还没有闲鱼账号，建不了草稿"}
          </span>
        </div>
        {body}
      </Card>

      {openId && (
        <PddDetailSheet
          goodsId={openId}
          summary={all.find((g) => g.goods_id === openId)}
          onClose={() => setOpenId(null)}
          onDraft={() => makeDraft(openId)}
          drafting={drafting === openId}
          canDraft={!!account}
          onFetchImages={() => refetchImages(openId)}
          fetching={fetching === openId}
        />
      )}
      {removing && (
        <DeletePddDialog
          goods={removing}
          onClose={() => setRemoving(null)}
          onDeleted={() => openId === removing.goods_id && setOpenId(null)}
        />
      )}
    </div>
  )
}
