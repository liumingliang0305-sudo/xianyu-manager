import { ArrowLeftIcon, ExternalLinkIcon } from "lucide-react"
import { useNavigate, useParams } from "react-router"

import { PageHeader } from "@/components/page-header"
import { LoadError, RowsSkeleton } from "@/components/states"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { useCurrentAccount } from "@/hooks/use-account"
import { itemUrl } from "@/lib/format"
import { DraftEditor } from "@/features/publish/draft-editor"
import { useEditDraft } from "@/features/publish/queries"

import { useItems } from "./queries"

/**
 * 编辑一个已经在闲鱼上的商品（/items/:itemId/edit）。表单和发布页一样：打开时从闲鱼拉一次
 * 编辑页的初值存成草稿，改动自动暂存在后台，点"保存到闲鱼"才提交。
 */
export function ItemEditPage() {
  const account = useCurrentAccount()
  const { itemId = "" } = useParams()
  const navigate = useNavigate()
  const draft = useEditDraft(account, itemId)
  const local = useItems(account).data?.items.find((it) => it.item_id === itemId)
  const back = () => navigate("/items")

  let body
  if (draft.isPending) body = <RowsSkeleton rows={6} height="h-16" />
  else if (draft.isError)
    body = <LoadError what="从闲鱼拉取商品" error={draft.error} onRetry={() => void draft.refetch()} />
  else
    body = (
      <DraftEditor
        key={draft.data.id}
        account={account}
        draft={draft.data}
        onDeleted={back}
        onPublished={back}
      />
    )

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="编辑商品"
        description={local?.title ?? itemId}
        actions={
          <>
            <Button variant="ghost" asChild>
              <a href={itemUrl(itemId)} target="_blank" rel="noreferrer">
                在闲鱼打开
                <ExternalLinkIcon />
              </a>
            </Button>
            <Button variant="outline" onClick={back}>
              <ArrowLeftIcon />
              回到商品
            </Button>
          </>
        }
      />
      <Card className="gap-0 overflow-hidden py-0">{body}</Card>
    </div>
  )
}
