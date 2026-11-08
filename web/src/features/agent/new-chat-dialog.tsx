import { CheckIcon, Loader2Icon } from "lucide-react"
import { useState, type ReactNode } from "react"
import { toast } from "sonner"

import { EmptyNote, LoadError, RowsSkeleton, SearchInput } from "@/components/states"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { ItemThumb } from "@/features/items/item-bits"
import { PddThumb } from "@/features/pdd/pdd-bits"
import { priceRange } from "@/features/pdd/pdd-format"
import { usePddGoods } from "@/features/pdd/queries"
import { useStash } from "@/features/search/queries"
import { errorMessage } from "@/lib/api"
import { timeAgo, yuan } from "@/lib/format"
import { cn } from "@/lib/utils"

import { useCreateAgentChat } from "./queries"

interface Choice {
  id: string
  title: string
  thumb: ReactNode
  meta: string
}

/** 一列可选的东西（单选，可以都不选）：上面搜索，下面列表，选中的打勾 */
function ChoiceList({
  label,
  none,
  choices,
  value,
  onChange,
  loading,
  error,
  onRetry,
  empty,
}: {
  label: string
  none: string
  choices: Choice[]
  value: string | null
  onChange: (id: string | null) => void
  loading: boolean
  error: unknown
  onRetry: () => void
  empty: string
}) {
  const [search, setSearch] = useState("")
  const text = search.trim().toLowerCase()
  const shown = text ? choices.filter((c) => c.title.toLowerCase().includes(text)) : choices

  let body
  if (loading) body = <RowsSkeleton rows={4} height="h-11" />
  else if (error) body = <LoadError what={`读取${label}`} error={error} onRetry={onRetry} />
  else if (choices.length === 0) body = <div className="p-3"><EmptyNote>{empty}</EmptyNote></div>
  else
    body = (
      <div role="radiogroup" aria-label={label} className="flex flex-col p-1">
        {[{ id: "", title: none, thumb: null, meta: "" } as Choice, ...shown].map((c) => {
          const selected = (value ?? "") === c.id
          return (
            <button
              key={c.id || "none"}
              type="button"
              role="radio"
              aria-checked={selected}
              onClick={() => onChange(c.id || null)}
              className={cn(
                "flex items-center gap-2.5 rounded-md px-2 py-1.5 text-left transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50",
                selected ? "bg-accent" : "hover:bg-accent/60",
              )}
            >
              {c.thumb ?? <span className="w-10 shrink-0" aria-hidden />}
              <span className="min-w-0 flex-1">
                <span className={cn("line-clamp-2 text-sm", !c.id && "text-muted-foreground")}>{c.title}</span>
                {c.meta && <span className="block text-xs text-muted-foreground tabular-nums">{c.meta}</span>}
              </span>
              <CheckIcon className={cn("size-4 shrink-0", !selected && "invisible")} aria-hidden />
            </button>
          )
        })}
        {text && shown.length === 0 && (
          <p className="px-2 py-3 text-sm text-muted-foreground">没有标题里带「{search.trim()}」的</p>
        )}
      </div>
    )

  return (
    <section className="flex min-w-0 flex-col gap-2">
      <h3 className="text-sm font-medium">{label}</h3>
      <SearchInput
        value={search}
        onChange={(e) => setSearch(e.target.value)}
        placeholder="按标题筛…"
        aria-label={`筛选${label}`}
        disabled={choices.length === 0}
      />
      <div className="h-80 overflow-y-auto overscroll-contain rounded-lg border">{body}</div>
    </section>
  )
}

/** 新对话：选一个拼多多货源、一件暂存（至少一个），开了以后不能换 */
export function NewChatDialog({
  onClose,
  onCreated,
}: {
  onClose: () => void
  onCreated: (chatId: number) => void
}) {
  const goods = usePddGoods()
  const stash = useStash()
  const create = useCreateAgentChat()
  const [goodsId, setGoodsId] = useState<string | null>(null)
  const [itemId, setItemId] = useState<string | null>(null)

  const goodsChoices: Choice[] = (goods.data ?? []).map((g) => ({
    id: g.goods_id,
    title: g.title,
    thumb: <PddThumb url={g.cover} size={40} />,
    meta: [priceRange(g.min_price_cent, g.max_price_cent), g.mall_name].filter(Boolean).join(" · "),
  }))
  const stashChoices: Choice[] = (stash.data ?? []).map((it) => ({
    id: it.item_id,
    title: it.title,
    thumb: <ItemThumb url={it.images[0] ?? it.pic_url} size={40} />,
    meta: [yuan(it.price_cent), it.seller_nick, `${timeAgo(it.saved_at)}暂存`].filter(Boolean).join(" · "),
  }))

  const submit = () =>
    create.mutate(
      { pdd_goods_id: goodsId, stash_item_id: itemId },
      {
        onSuccess: (chat) => onCreated(chat.id),
        onError: (error) => toast.error(errorMessage(error)),
      },
    )

  return (
    <Dialog open onOpenChange={(open) => !open && !create.isPending && onClose()}>
      <DialogContent className="sm:max-w-3xl">
        <DialogHeader>
          <DialogTitle>新对话</DialogTitle>
          <DialogDescription>
            选要聊的拼多多货源和同行的商品（搜索页暂存的），至少选一个。模型只看得到它们的文字资料，开了以后不能换。
          </DialogDescription>
        </DialogHeader>
        <div className="grid grid-cols-2 gap-4">
          <ChoiceList
            label="拼多多货源"
            none="不带货源"
            choices={goodsChoices}
            value={goodsId}
            onChange={setGoodsId}
            loading={goods.isPending}
            error={goods.error}
            onRetry={() => void goods.refetch()}
            empty="还没有货源：在拼多多商品页上用插件导入"
          />
          <ChoiceList
            label="同行的商品"
            none="不带同行的商品"
            choices={stashChoices}
            value={itemId}
            onChange={setItemId}
            loading={stash.isPending}
            error={stash.error}
            onRetry={() => void stash.refetch()}
            empty="还没有暂存：在搜索页的结果里点「暂存」"
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={create.isPending}>
            取消
          </Button>
          <Button onClick={submit} disabled={(!goodsId && !itemId) || create.isPending}>
            {create.isPending && <Loader2Icon className="animate-spin" />}
            开始对话
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
