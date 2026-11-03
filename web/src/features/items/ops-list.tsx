import { EmptyNote } from "@/components/states"
import type { ItemOp } from "@/lib/api"
import { dateTime, yuan } from "@/lib/format"
import { cn } from "@/lib/utils"

const ACTION_LABEL: Record<ItemOp["action"], string> = {
  price: "改价",
  desc: "改描述",
  edit: "编辑",
  downshelf: "下架",
  delete: "删除",
  publish: "发布",
  relist: "重新上架",
}

function change(op: ItemOp): string {
  if (op.action === "price") {
    const text = `${yuan(Number(op.detail.from))} → ${yuan(Number(op.detail.to))}`
    return op.detail.orig_to != null ? `${text}，原价 ${yuan(op.detail.orig_to)}` : text
  }
  if (op.action === "publish") return yuan(op.detail.price)
  if (op.action === "relist") return `${yuan(op.detail.price)}，照 ${op.detail.from_item} 发的新商品`
  if (op.action === "edit") {
    const fields = (op.detail.fields ?? []).join("、")
    return op.detail.to != null
      ? `${fields}（${yuan(Number(op.detail.from))} → ${yuan(Number(op.detail.to))}）`
      : fields
  }
  return ""
}

/** 改动记录。`titles` 用来把商品 ID 换成标题，找不到时显示 ID。 */
export function OpsList({ ops, titles }: { ops: ItemOp[]; titles?: Map<string, string> }) {
  if (ops.length === 0) {
    return <EmptyNote>还没有改动记录。在这里改价、编辑、下架、删除或发布后，结果会记在这里</EmptyNote>
  }
  return (
    <ul className="divide-y divide-rule">
      {ops.map((op) => (
        <li key={op.id} className="flex items-start gap-3 py-2.5 text-sm">
          <span
            className={cn(
              "mt-1.5 size-2 shrink-0 rounded-full",
              op.ok ? "bg-positive" : "bg-destructive",
            )}
            aria-label={op.ok ? "成功" : "失败"}
          />
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-baseline gap-x-2">
              <span className="font-medium">{ACTION_LABEL[op.action] ?? op.action}</span>
              <span className="tabular-nums">{change(op)}</span>
              {titles && (
                <span className="truncate text-muted-foreground">
                  {op.item_id ? (titles.get(op.item_id) ?? op.item_id) : `草稿 #${op.detail.draft_id}`}
                </span>
              )}
            </div>
            {!op.ok && <p className="text-xs text-destructive">失败：{op.error}</p>}
          </div>
          <time className="shrink-0 text-xs text-muted-foreground tabular-nums">
            {dateTime(op.created_at)}
          </time>
        </li>
      ))}
    </ul>
  )
}
