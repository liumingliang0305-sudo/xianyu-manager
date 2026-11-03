import { Loader2Icon, PlusIcon } from "lucide-react"
import { useSearchParams } from "react-router"
import { toast } from "sonner"

import { PageHeader } from "@/components/page-header"
import { EmptyState, LoadError, RowsSkeleton } from "@/components/states"
import { StatusPill } from "@/components/status-pill"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { useCurrentAccount } from "@/hooks/use-account"
import { errorMessage, type Draft } from "@/lib/api"
import { timeAgo, yuan } from "@/lib/format"
import { cn } from "@/lib/utils"

import { DraftEditor } from "./draft-editor"
import { DraftImage } from "./draft-image"
import { useCreateDraft, useDrafts } from "./queries"

/** 选中的草稿放在网址里（?draft=12），刷新、复制链接还是这个草稿 */
function useSelectedDraft(): [number | null, (id: number | null) => void] {
  const [params, setParams] = useSearchParams()
  const id = Number(params.get("draft")) || null
  const select = (next: number | null) =>
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev)
        if (next) p.set("draft", String(next))
        else p.delete("draft")
        return p
      },
      { replace: true },
    )
  return [id, select]
}

function firstLine(text: string): string {
  return text.trim().split("\n")[0] ?? ""
}

function DraftRow({
  account,
  draft,
  selected,
  onSelect,
}: {
  account: string
  draft: Draft
  selected: boolean
  onSelect: () => void
}) {
  const title = firstLine(draft.description)
  let status
  if (draft.item_id) status = <StatusPill tone="positive">已发布</StatusPill>
  else if (draft.error) status = <StatusPill tone="negative">发布失败</StatusPill>
  else status = <StatusPill tone="neutral">草稿</StatusPill>
  return (
    <li>
      <button
        type="button"
        onClick={onSelect}
        aria-current={selected || undefined}
        className={cn(
          "flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:ring-inset",
          selected ? "bg-secondary" : "hover:bg-muted/60",
        )}
      >
        <DraftImage account={account} draftId={draft.id} sha={draft.images[0]?.sha} size={40} />
        <span className="min-w-0 flex-1">
          <span className={cn("block truncate text-sm", title ? "font-medium" : "text-muted-foreground")}>
            {title || "还没写描述"}
          </span>
          <span className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
            {status}
            <span className="tabular-nums">{yuan(draft.price_cent)}</span>
            <span className="ml-auto shrink-0">{timeAgo(draft.published_at ?? draft.updated_at)}</span>
          </span>
        </span>
      </button>
    </li>
  )
}

function DraftGroup({
  title,
  drafts,
  account,
  selectedId,
  onSelect,
}: {
  title: string
  drafts: Draft[]
  account: string
  selectedId: number | undefined
  onSelect: (id: number) => void
}) {
  if (drafts.length === 0) return null
  return (
    <div>
      <h2 className="px-3 pt-3 pb-1 text-xs text-muted-foreground">
        {title} <span className="tabular-nums">{drafts.length}</span>
      </h2>
      <ul>
        {drafts.map((d) => (
          <DraftRow
            key={d.id}
            account={account}
            draft={d}
            selected={d.id === selectedId}
            onSelect={() => onSelect(d.id)}
          />
        ))}
      </ul>
    </div>
  )
}

export function PublishPage() {
  const account = useCurrentAccount()
  const drafts = useDrafts(account)
  const create = useCreateDraft(account)
  const [selectedId, select] = useSelectedDraft()

  const all = drafts.data ?? []
  const pending = all.filter((d) => !d.item_id)
  const published = all.filter((d) => d.item_id)
  const selected = all.find((d) => d.id === selectedId) ?? pending[0] ?? published[0]

  const newDraft = () =>
    create.mutate(undefined, {
      onSuccess: (draft) => select(draft.id),
      onError: (error) => toast.error(errorMessage(error)),
    })

  const createButton = (
    <Button onClick={newDraft} disabled={create.isPending}>
      {create.isPending ? <Loader2Icon className="animate-spin" /> : <PlusIcon />}
      新建草稿
    </Button>
  )

  let body
  if (drafts.isPending) body = <RowsSkeleton height="h-14" />
  else if (drafts.isError)
    body = <LoadError what="读取草稿" error={drafts.error} onRetry={() => void drafts.refetch()} />
  else if (!selected)
    body = (
      <EmptyState title="还没有草稿" action={createButton}>
        先在这里写好图片、描述和价格，存成草稿，检查没问题再发到闲鱼
      </EmptyState>
    )
  else
    body = (
      <div className="grid min-h-[36rem] grid-cols-[18rem_1fr]">
        <nav aria-label="草稿" className="border-r pb-3">
          <DraftGroup
            title="没发的"
            drafts={pending}
            account={account}
            selectedId={selected.id}
            onSelect={select}
          />
          <DraftGroup
            title="已发布"
            drafts={published}
            account={account}
            selectedId={selected.id}
            onSelect={select}
          />
        </nav>
        <div className="min-w-0">
          {/* 换草稿时表单整个重来，上一个草稿没存的改动在卸载时存掉 */}
          <DraftEditor
            key={selected.id}
            account={account}
            draft={selected}
            onDeleted={() => select(null)}
          />
        </div>
      </div>
    )

  const summary = drafts.isPending
    ? "…"
    : `${pending.length} 个草稿没发 · 通过这里发布了 ${published.length} 件`

  return (
    <div className="flex flex-col gap-6">
      <PageHeader title="发布" description={summary} actions={all.length > 0 && createButton} />
      <Card className="gap-0 overflow-hidden py-0">{body}</Card>
    </div>
  )
}
