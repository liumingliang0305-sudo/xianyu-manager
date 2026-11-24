import { CheckIcon, Loader2Icon } from "lucide-react"
import { useId, useState } from "react"
import { Link, useNavigate } from "react-router"
import { toast } from "sonner"

import { NativeSelect } from "@/components/native-select"
import { EmptyNote, LoadError, RowsSkeleton } from "@/components/states"
import { StatusPill } from "@/components/status-pill"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { PddThumb } from "@/features/pdd/pdd-bits"
import { DraftImage } from "@/features/publish/draft-image"
import { usePendingDrafts, useReplaceDescription } from "@/features/publish/queries"
import { SourcePhoto } from "@/features/sources/source-photo"
import { useAccount } from "@/hooks/use-account"
import { accountName } from "@/lib/account"
import { errorMessage, type AgentChatSummary, type Draft } from "@/lib/api"
import { count, timeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"

import { useAgentChatImages, useDraftFromChat } from "./queries"

/** 闲鱼商品描述最多几个字，和 xianyu_core/publish.py 的 MAX_DESC 一样，按 Unicode 字符数（和 Python 的 len 一样） */
const MAX_DESC = 1500

type Target = "new" | "replace"

const TARGETS: { value: Target; label: string; hint: string }[] = [
  { value: "new", label: "新建草稿", hint: "建一个新链接，带上货源的图" },
  { value: "replace", label: "替换已有草稿的描述", hint: "只换描述，图片、价格这些不动" },
]

function draftKind(draft: Draft): string {
  if (draft.edit_item_id) return "编辑商品"
  if (draft.relist_item_id) return "重新上架"
  return "新商品"
}

/** 草稿在哪一页改：编辑已有商品的在编辑商品页，别的在发布页 */
function draftPath(draft: Draft): string {
  return draft.edit_item_id
    ? `/items/${encodeURIComponent(draft.edit_item_id)}/edit`
    : `/publish?draft=${draft.id}`
}

/**
 * 新草稿带哪些图：对话带的拼多多、第三方货源里下载好的，默认和照货源建草稿一样（主图，最多 9 张）。
 * 暂存的同行商品是别人在闲鱼上卖的，图不带。以后挑图的弹窗接在这一块里，挑好的作为 images 发给后台。
 */
function NewDraftImages({ chat }: { chat: AgentChatSummary }) {
  const hasGoods = chat.pdd_goods_id != null || chat.source_id != null
  const found = useAgentChatImages(chat.id, hasGoods)
  const peerNote = chat.stash_item_id ? "同行的商品是别人在闲鱼上卖的，图片不带过去。" : ""

  let body
  if (!hasGoods) {
    body = (
      <EmptyNote>
        {peerNote || "这个对话没带货源。"}草稿先不带图，建好后在发布页加自己的图。
      </EmptyNote>
    )
  } else if (found.isPending) {
    body = <Skeleton className="h-12 w-full" />
  } else if (found.isError) {
    body = <LoadError what="读取货源的图片" error={found.error} onRetry={() => void found.refetch()} />
  } else {
    const byId = new Map(found.data.images.map((img) => [img.sha, img]))
    const chosen = found.data.selected.flatMap((sha) => byId.get(sha) ?? [])
    if (chosen.length === 0) {
      const gone = chat.pdd_goods == null && chat.source == null
      body = (
        <EmptyNote>
          {gone ? "带的货源已经删掉了" : "货源的主图还没下载下来，可以先到货源页重新下载"}
          。草稿先不带图，建好后在发布页加。
        </EmptyNote>
      )
    } else {
      const from = chosen[0].origin === "pdd" ? "拼多多货源的主图" : "第三方货源的图"
      body = (
        <div className="flex flex-col gap-2">
          <ul className="flex flex-wrap gap-1.5" aria-label="草稿带的图">
            {chosen.map((img) => (
              <li key={img.sha}>
                {img.origin === "pdd" ? (
                  <PddThumb url={img.url} size={48} />
                ) : (
                  <SourcePhoto sourceId={chat.source_id ?? ""} sha={img.sha} size={48} />
                )}
              </li>
            ))}
          </ul>
          <p className="text-xs text-muted-foreground">
            带上{from}，共 {chosen.length} 张，建好后在发布页可以增删、调顺序。{peerNote}
          </p>
        </div>
      )
    }
  }

  return (
    <section className="grid gap-2">
      <h3 className="text-sm font-medium">图片</h3>
      {body}
    </section>
  )
}

/** 选一个还没发的草稿（编辑商品的也算），单选 */
function DraftChoices({
  account,
  value,
  onChange,
}: {
  account: string
  value: number | null
  onChange: (id: number) => void
}) {
  const drafts = usePendingDrafts(account)
  const groupId = useId()

  let body
  if (drafts.isPending) body = <RowsSkeleton rows={3} height="h-11" />
  else if (drafts.isError)
    body = <LoadError what="读取草稿" error={drafts.error} onRetry={() => void drafts.refetch()} />
  else if (drafts.data.length === 0)
    body = (
      <div className="p-3">
        <EmptyNote>这个账号没有还没发的草稿，换个账号，或者新建草稿。</EmptyNote>
      </div>
    )
  else
    body = (
      <div role="radiogroup" aria-label="要替换描述的草稿" className="flex flex-col p-1">
        {drafts.data.map((draft) => {
          const selected = draft.id === value
          const title = draft.description.trim().split("\n")[0] ?? ""
          return (
            <label
              key={draft.id}
              className={cn(
                "flex cursor-pointer items-center gap-2.5 rounded-md px-2 py-1.5 transition-colors focus-within:ring-3 focus-within:ring-ring/50",
                selected ? "bg-accent" : "hover:bg-accent/60",
              )}
            >
              <input
                type="radio"
                name={groupId}
                value={draft.id}
                checked={selected}
                onChange={() => onChange(draft.id)}
                className="sr-only"
              />
              <DraftImage
                account={account}
                draftId={draft.id}
                sha={draft.images[0]?.sha}
                url={draft.images[0]?.url}
                size={40}
              />
              <span className="min-w-0 flex-1">
                <span className={cn("block truncate text-sm", !title && "text-muted-foreground")}>
                  {title || "还没写描述"}
                </span>
                <span className="mt-0.5 flex items-center gap-2 text-xs text-muted-foreground">
                  <StatusPill tone="neutral">{draftKind(draft)}</StatusPill>
                  {draft.error && <span className="text-destructive">上次发布失败</span>}
                  <span className="ml-auto shrink-0">{timeAgo(draft.updated_at)}改过</span>
                </span>
              </span>
              <CheckIcon className={cn("size-4 shrink-0", !selected && "invisible")} aria-hidden />
            </label>
          )
        })}
      </div>
    )

  return (
    <section className="grid gap-2">
      <h3 className="text-sm font-medium">替换哪个草稿</h3>
      <div className="max-h-60 overflow-y-auto overscroll-contain rounded-lg border">{body}</div>
      <p className="text-xs text-muted-foreground">草稿原来的描述会被换掉，图片、价格、分类这些不变。</p>
    </section>
  )
}

/**
 * 把 AI 的一条回复用到发布草稿：先在框里删改，再新建一个草稿（建链接），或者替换已有草稿的描述，
 * 然后打开那个草稿。超过闲鱼字数上限的照样放进去，标红提醒，不截断。
 */
export function ApplyToDraftDialog({
  chat,
  reply,
  onClose,
}: {
  chat: AgentChatSummary
  reply: string
  onClose: () => void
}) {
  const navigate = useNavigate()
  const { accounts, account: current, setAccount } = useAccount()
  const [text, setText] = useState(reply)
  const [target, setTarget] = useState<Target>("new")
  const [picked, setPicked] = useState<string | null>(null)
  const [draftId, setDraftId] = useState<number | null>(null)
  const create = useDraftFromChat(chat.id)
  const replace = useReplaceDescription()
  const busy = create.isPending || replace.isPending

  // 默认放到当前账号；账号列表还没拉下来时等它
  const account = accounts.find((a) => a.id === (picked ?? current)) ?? null
  // 和后端一样去掉首尾空白，按 Unicode 字符数（和 Python 的 len 一样）
  const length = [...text.trim()].length
  const over = length > MAX_DESC
  const ready = account != null && length > 0 && (target === "new" || draftId != null)

  const opened = (draft: Draft, message: string) => {
    // 草稿在别的账号就先切过去，发布页、编辑商品页看的是当前账号的草稿
    if (account && account.id !== current) setAccount(account.id)
    toast.success(message)
    onClose()
    navigate(draftPath(draft))
  }

  const submit = () => {
    if (!ready || busy || !account) return
    if (target === "new") {
      create.mutate(
        { account: account.id, description: text },
        {
          onSuccess: (draft) => opened(draft, `已在「${accountName(account)}」建好草稿，填上价格就能发`),
          onError: (error) => toast.error(`建草稿失败：${errorMessage(error)}`),
        },
      )
    } else if (draftId != null) {
      replace.mutate(
        { account: account.id, draftId, description: text },
        {
          onSuccess: (draft) => opened(draft, "已替换草稿的描述"),
          onError: (error) => toast.error(`替换失败：${errorMessage(error)}`),
        },
      )
    }
  }

  let lengthNote: string
  if (length === 0) lengthNote = "要放进草稿的文字是空的"
  else if (over)
    lengthNote = `超出闲鱼的 ${count(MAX_DESC)} 字上限 ${count(length - MAX_DESC)} 字，照样能放进草稿，发布前要删到 ${count(MAX_DESC)} 字以内`
  else lengthNote = "可以先删掉开场白、多余的说明，只留要放进商品描述的部分。表情符号会去掉"

  return (
    <Dialog open onOpenChange={(open) => !open && !busy && onClose()}>
      <DialogContent className="sm:max-w-2xl" showCloseButton={!busy}>
        <DialogHeader>
          <DialogTitle>用到草稿</DialogTitle>
          <DialogDescription>
            把这条回复放进发布草稿当商品描述，放进去之后在发布页接着改价格、分类和发货地。
          </DialogDescription>
        </DialogHeader>

        <div className="grid gap-5">
          <div className="grid gap-2">
            <div className="flex items-center justify-between gap-4">
              <Label htmlFor="apply-text">描述</Label>
              <span
                className={cn("text-xs tabular-nums", over ? "text-destructive" : "text-muted-foreground")}
              >
                {count(length)} / {count(MAX_DESC)} 字
              </span>
            </div>
            <Textarea
              id="apply-text"
              autoComplete="off"
              rows={8}
              disabled={busy}
              value={text}
              onChange={(e) => setText(e.target.value)}
              aria-invalid={over || length === 0 || undefined}
              aria-describedby="apply-text-hint"
              className="max-h-[40svh] min-h-40 resize-y leading-6"
            />
            <p
              id="apply-text-hint"
              aria-live="polite"
              className={cn("text-xs", over || length === 0 ? "text-destructive" : "text-muted-foreground")}
            >
              {lengthNote}
            </p>
          </div>

          <div className="grid grid-cols-[7rem_1fr] items-center gap-x-4">
            <Label htmlFor="apply-account">放到哪个账号</Label>
            {accounts.length > 0 ? (
              <NativeSelect
                id="apply-account"
                value={account?.id ?? ""}
                disabled={busy}
                onChange={(e) => {
                  setPicked(e.target.value)
                  setDraftId(null)
                }}
                className="max-w-xs"
              >
                {accounts.map((a) => (
                  <option key={a.id} value={a.id}>
                    {accountName(a)}
                    {a.id === current ? "（当前账号）" : ""}
                  </option>
                ))}
              </NativeSelect>
            ) : (
              <p className="text-sm text-muted-foreground">
                还没有闲鱼账号，先到
                <Link to="/accounts" className="mx-0.5 text-foreground underline underline-offset-4">
                  账号矩阵
                </Link>
                登录一个
              </p>
            )}
          </div>

          <fieldset className="grid gap-2" disabled={busy}>
            <legend className="mb-2 text-sm leading-none font-medium">怎么用</legend>
            <div role="radiogroup" aria-label="怎么用这条回复" className="grid grid-cols-2 gap-2">
              {TARGETS.map((option) => (
                <button
                  key={option.value}
                  type="button"
                  role="radio"
                  aria-checked={target === option.value}
                  onClick={() => setTarget(option.value)}
                  className={cn(
                    "grid gap-0.5 rounded-lg border px-3 py-2 text-left transition-colors outline-none",
                    "hover:border-input focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50",
                    target === option.value && "border-primary ring-1 ring-primary",
                  )}
                >
                  <span className="text-sm font-medium">{option.label}</span>
                  <span className="text-xs text-muted-foreground">{option.hint}</span>
                </button>
              ))}
            </div>
          </fieldset>

          {target === "new" ? (
            <NewDraftImages chat={chat} />
          ) : account ? (
            <DraftChoices account={account.id} value={draftId} onChange={setDraftId} />
          ) : null}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onClose} disabled={busy}>
            取消
          </Button>
          <Button onClick={submit} disabled={!ready || busy}>
            {busy && <Loader2Icon className="animate-spin" aria-hidden />}
            {target === "new" ? (busy ? "建草稿中…" : "建草稿并打开") : busy ? "替换中…" : "替换并打开"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
