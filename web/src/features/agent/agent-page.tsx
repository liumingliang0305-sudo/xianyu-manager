import {
  CopyIcon,
  EllipsisIcon,
  Loader2Icon,
  PlusIcon,
  SendHorizontalIcon,
  Trash2Icon,
} from "lucide-react"
import { useLayoutEffect, useRef, useState, type ReactNode } from "react"
import { Link, useSearchParams } from "react-router"
import { toast } from "sonner"

import { Callout } from "@/components/callout"
import { PageHeader } from "@/components/page-header"
import { EmptyState, LoadError, RowsSkeleton } from "@/components/states"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Skeleton } from "@/components/ui/skeleton"
import { Textarea } from "@/components/ui/textarea"
import { ItemThumb } from "@/features/items/item-bits"
import { PddThumb } from "@/features/pdd/pdd-bits"
import { priceRange } from "@/features/pdd/pdd-format"
import { errorMessage, type AgentChat, type AgentChatSummary, type AgentMessage } from "@/lib/api"
import { chatTime, timeAgo, yuan } from "@/lib/format"
import { cn } from "@/lib/utils"

import { NewChatDialog } from "./new-chat-dialog"
import { useAgentChat, useAgentChats, useAgentReply, useDeleteAgentChat, type PendingTurn } from "./queries"

/** 一条消息最多几个字，和后端（app/agent.py 的 MAX_PROMPT）一样 */
const MAX_PROMPT = 4000
/** 两条消息隔这么久（秒）就在中间标一下时间 */
const TIME_GAP = 10 * 60

/** 列表里、标题栏里说这个对话带了什么 */
function attachedLabel(chat: AgentChatSummary): string {
  const parts = []
  if (chat.pdd_goods_id) parts.push(chat.pdd_goods?.title ?? "货源已删除")
  if (chat.stash_item_id) parts.push(chat.stash_item?.title ?? "暂存已移出")
  return parts.join(" ＋ ")
}

/** 还没聊的对话，按带的东西给几句开场 */
function starters(chat: AgentChatSummary): string[] {
  const goods = chat.pdd_goods_id != null
  const peer = chat.stash_item_id != null
  if (goods && peer)
    return [
      "参考同行的写法，给这件货写一段闲鱼描述",
      "这件货在闲鱼上定多少钱合适？",
      "和同行比，这件货的卖点有什么不一样？",
    ]
  if (goods) return ["给这件货写一段闲鱼描述", "起 3 个闲鱼标题", "按拼单价算，闲鱼上定多少钱合适？"]
  return ["这件商品的文案好在哪里？", "照这个写法，写一段能套用的描述模板", "这个价格在闲鱼上算贵吗？"]
}

function ChatRow({ chat, active, onOpen }: { chat: AgentChatSummary; active: boolean; onOpen: () => void }) {
  const cover = chat.pdd_goods ? (
    <PddThumb url={chat.pdd_goods.cover} size={40} />
  ) : (
    <ItemThumb url={chat.stash_item?.pic_url ?? ""} size={40} />
  )
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-current={active || undefined}
      className={cn(
        "flex w-full items-start gap-3 px-4 py-3 text-left transition-colors outline-none focus-visible:bg-accent",
        active ? "bg-accent" : "hover:bg-accent/50",
      )}
    >
      {cover}
      <span className="min-w-0 flex-1">
        <span className="flex items-baseline gap-2">
          <span className={cn("min-w-0 flex-1 truncate text-sm font-medium", !chat.title && "text-muted-foreground")}>
            {chat.title || "还没开始聊"}
          </span>
          <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{timeAgo(chat.updated_at)}</span>
        </span>
        <span className="mt-0.5 block truncate text-[13px] text-muted-foreground">{attachedLabel(chat)}</span>
      </span>
    </button>
  )
}

/** 标题栏里带的一件：缩略图、哪一种、价格、标题；点了去看详情。删掉了的只剩一句话 */
function Attachment({
  kind,
  title,
  price,
  thumb,
  to,
}: {
  kind: string
  title: string | null
  price: string
  thumb: ReactNode
  to: string
}) {
  if (title == null) {
    return (
      <div className="flex min-w-0 items-center gap-2.5 rounded-lg border border-dashed px-2.5 py-1.5 text-sm text-muted-foreground">
        {kind}已经删掉了，前面聊过的还在
      </div>
    )
  }
  return (
    <Link
      to={to}
      className="flex min-w-0 max-w-[24rem] items-center gap-2.5 rounded-lg px-1.5 py-1 transition-colors outline-none hover:bg-accent/60 focus-visible:ring-3 focus-visible:ring-ring/50"
    >
      {thumb}
      <span className="min-w-0">
        <span className="block text-xs text-muted-foreground tabular-nums">
          {kind} · {price}
        </span>
        <span className="block truncate text-sm">{title}</span>
      </span>
    </Link>
  )
}

function Bubble({ message, live }: { message: Pick<AgentMessage, "role" | "text">; live?: boolean }) {
  const mine = message.role === "user"
  const copy = () =>
    navigator.clipboard.writeText(message.text).then(
      () => toast.success("已复制"),
      () => toast.error("复制失败，手动选中文字复制"),
    )
  return (
    <div className={cn("group flex items-end gap-1.5", mine ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "rounded-2xl px-3 py-2 text-sm leading-6 break-words whitespace-pre-wrap",
          mine ? "max-w-[75%] rounded-br-md bg-brand text-brand-foreground" : "max-w-[85%] rounded-bl-md bg-muted",
        )}
      >
        {message.text}
        {live && <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse bg-foreground/60 align-[-2px]" aria-hidden />}
      </div>
      {!mine && !live && (
        <Button
          variant="ghost"
          size="icon-xs"
          className="opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100"
          onClick={() => void copy()}
          aria-label="复制这条回复"
          title="复制"
        >
          <CopyIcon />
        </Button>
      )}
    </div>
  )
}

function Composer({
  disabledReason,
  busy,
  draft,
  onDraft,
  onSend,
}: {
  disabledReason: string
  busy: boolean
  draft: string
  onDraft: (text: string) => void
  onSend: (text: string) => void
}) {
  const text = draft.trim()
  const tooLong = draft.length > MAX_PROMPT
  const canSend = !disabledReason && !busy && text.length > 0 && !tooLong
  const submit = () => {
    if (!canSend) return
    onSend(text)
    onDraft("")
  }
  return (
    <div className="flex items-end gap-2 border-t p-3">
      <div className="relative min-w-0 flex-1">
        <Textarea
          value={draft}
          onChange={(e) => onDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing || e.keyCode === 229) return
            e.preventDefault()
            submit()
          }}
          rows={2}
          disabled={!!disabledReason}
          placeholder={disabledReason || "问点什么，Enter 发送，Shift+Enter 换行…"}
          aria-label="发给 AI 助手的话"
          aria-invalid={tooLong || undefined}
          aria-describedby={tooLong ? "agent-too-long" : undefined}
          className={cn("max-h-48 min-h-14 resize-none", tooLong && "pr-20")}
        />
        {tooLong && (
          <span
            id="agent-too-long"
            role="alert"
            className="pointer-events-none absolute right-2 bottom-1.5 text-xs text-destructive tabular-nums"
          >
            {draft.length}/{MAX_PROMPT}
          </span>
        )}
      </div>
      <Button size="icon-lg" onClick={submit} disabled={!canSend} aria-label="发送">
        {busy ? <Loader2Icon className="animate-spin" /> : <SendHorizontalIcon />}
      </Button>
    </div>
  )
}

function Messages({
  chat,
  pending,
  onStarter,
  canSend,
}: {
  chat: AgentChat
  pending: PendingTurn | null
  onStarter: (text: string) => void
  canSend: boolean
}) {
  const scroller = useRef<HTMLDivElement>(null)
  const atBottom = useRef(true)
  const messages = chat.messages

  // 新消息、回复一段段出来时跟到底；往上翻着看的时候不打扰
  useLayoutEffect(() => {
    const el = scroller.current
    if (el && atBottom.current) el.scrollTop = el.scrollHeight
  }, [messages.length, pending?.prompt, pending?.reply])

  if (messages.length === 0 && !pending) {
    return (
      <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-3 px-6 text-center">
        <p className="text-sm text-muted-foreground">还没开始聊。可以这样问：</p>
        <div className="flex max-w-lg flex-col gap-2">
          {starters(chat).map((s) => (
            <Button key={s} variant="outline" disabled={!canSend} onClick={() => onStarter(s)}>
              {s}
            </Button>
          ))}
        </div>
      </div>
    )
  }

  return (
    <div
      ref={scroller}
      onScroll={(e) => {
        const el = e.currentTarget
        atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
      }}
      className="min-h-0 flex-1 overflow-y-auto overscroll-contain px-4 py-3"
      aria-live="polite"
    >
      <div className="flex flex-col gap-2.5">
        {messages.map((m, i) => {
          const prev = messages[i - 1]
          const gap = !prev || m.at - prev.at > TIME_GAP
          return (
            <div key={`${i}-${m.at}`} className="flex flex-col gap-2.5">
              {gap && <div className="pt-2 text-center text-xs text-muted-foreground">{chatTime(m.at)}</div>}
              <Bubble message={m} />
            </div>
          )
        })}
        {pending && (
          <>
            <Bubble message={{ role: "user", text: pending.prompt }} />
            {pending.reply ? (
              <Bubble message={{ role: "assistant", text: pending.reply }} live />
            ) : (
              <div className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2Icon className="size-4 animate-spin" />
                正在想…
              </div>
            )}
          </>
        )}
      </div>
    </div>
  )
}

function DeleteChatDialog({ chat, onClose, onDeleted }: { chat: AgentChatSummary; onClose: () => void; onDeleted: () => void }) {
  const remove = useDeleteAgentChat()
  return (
    <AlertDialog open onOpenChange={(open) => !open && !remove.isPending && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除这个对话？</AlertDialogTitle>
          <AlertDialogDescription>
            {chat.title && <span className="mb-2 block font-medium text-foreground">{chat.title}</span>}
            聊天记录删掉就找不回来了。带的拼多多货源和暂存不受影响。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={remove.isPending}>取消</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={remove.isPending}
            onClick={(e) => {
              e.preventDefault()
              remove.mutate(chat.id, {
                onSuccess: () => {
                  toast.success("对话已删除")
                  onDeleted()
                },
                onError: (error) => toast.error(errorMessage(error)),
              })
            }}
          >
            {remove.isPending && <Loader2Icon className="animate-spin" />}
            删除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

function ChatPanel({
  summary,
  unavailable,
  onDeleted,
}: {
  summary: AgentChatSummary
  unavailable: string
  onDeleted: () => void
}) {
  const detail = useAgentChat(summary.id)
  const reply = useAgentReply(summary.id)
  const [draft, setDraft] = useState("")
  const [deleting, setDeleting] = useState(false)
  const chat = detail.data

  const send = async (text: string) => {
    const failure = await reply.send(text)
    if (failure) {
      toast.error(failure)
      // 没发成的话放回输入框，改一改再发
      setDraft((current) => current || text)
    }
  }

  let body
  if (detail.isPending) {
    body = (
      <div className="flex flex-1 flex-col gap-3 px-4 py-3">
        <Skeleton className="ml-auto h-9 w-48" />
        <Skeleton className="h-24 w-3/4" />
      </div>
    )
  } else if (detail.isError || !chat) {
    body = <LoadError what="读取对话" error={detail.error} onRetry={() => void detail.refetch()} className="flex-1" />
  } else {
    body = (
      <Messages
        chat={chat}
        pending={reply.pending}
        canSend={!unavailable && !reply.busy}
        onStarter={(text) => void send(text)}
      />
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b px-3 py-2">
        <div className="flex min-w-0 flex-1 items-center gap-2">
          {summary.pdd_goods_id && (
            <Attachment
              kind="拼多多货源"
              title={summary.pdd_goods?.title ?? null}
              price={summary.pdd_goods ? priceRange(summary.pdd_goods.min_price_cent, summary.pdd_goods.max_price_cent) : ""}
              thumb={<PddThumb url={summary.pdd_goods?.cover ?? ""} size={36} />}
              to={`/pdd?goods=${encodeURIComponent(summary.pdd_goods_id)}`}
            />
          )}
          {summary.stash_item_id && (
            <Attachment
              kind="同行的商品"
              title={summary.stash_item?.title ?? null}
              price={summary.stash_item ? yuan(summary.stash_item.price_cent) : ""}
              thumb={<ItemThumb url={summary.stash_item?.pic_url ?? ""} size={36} />}
              to={`/search?view=stash&item=${encodeURIComponent(summary.stash_item_id)}`}
            />
          )}
        </div>
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon-sm" aria-label="对话的更多操作">
              <EllipsisIcon />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem variant="destructive" disabled={reply.busy} onSelect={() => setDeleting(true)}>
              <Trash2Icon />
              删除对话
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {body}
      <Composer
        disabledReason={unavailable ? "模型没配好，现在发不了消息" : ""}
        busy={reply.busy}
        draft={draft}
        onDraft={setDraft}
        onSend={(text) => void send(text)}
      />
      {deleting && <DeleteChatDialog chat={summary} onClose={() => setDeleting(false)} onDeleted={onDeleted} />}
    </div>
  )
}

export function AgentPage() {
  const query = useAgentChats()
  const [params, setParams] = useSearchParams()
  const [creating, setCreating] = useState(false)

  const selected = Number(params.get("c")) || null
  const chats = query.data?.chats ?? []
  const current = chats.find((c) => c.id === selected)
  const unavailable = query.data?.unavailable ?? ""
  const open = (chatId: number | null) =>
    setParams((prev) => {
      const p = new URLSearchParams(prev)
      if (chatId) p.set("c", String(chatId))
      else p.delete("c")
      return p
    })

  let description = "带着拼多多货源、同行的商品和大模型聊：写文案、起标题、商量定价"
  if (query.data) {
    const model = query.data.model ? ` · 模型 ${query.data.model}` : ""
    description = `${chats.length} 个对话${model}`
  }

  let list
  if (query.isPending) list = <RowsSkeleton rows={6} height="h-14" />
  else if (query.isError)
    list = <LoadError what="读取对话" error={query.error} onRetry={() => void query.refetch()} />
  else if (chats.length === 0)
    list = (
      <EmptyState title="还没有对话">选一个拼多多货源或者暂存的同行商品，和 AI 聊聊怎么写文案、怎么定价。</EmptyState>
    )
  else
    list = (
      <div className="divide-y divide-rule">
        {chats.map((chat) => (
          <ChatRow key={chat.id} chat={chat} active={chat.id === selected} onOpen={() => open(chat.id)} />
        ))}
      </div>
    )

  let panel
  if (current) {
    panel = <ChatPanel key={current.id} summary={current} unavailable={unavailable} onDeleted={() => open(null)} />
  } else {
    panel = (
      <EmptyState
        className="h-full justify-center"
        action={
          chats.length === 0 && !query.isPending ? (
            <Button variant="outline" onClick={() => setCreating(true)}>
              <PlusIcon />
              新对话
            </Button>
          ) : null
        }
      >
        {selected && !query.isPending ? "找不到这个对话，可能已经删掉了" : "选一个对话，或者开一个新的"}
      </EmptyState>
    )
  }

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="AI 助手"
        description={description}
        actions={
          <Button onClick={() => setCreating(true)}>
            <PlusIcon />
            新对话
          </Button>
        }
      />

      {unavailable && (
        <Callout tone="warning" title="模型没配好，现在发不了消息，以前的对话照样能看">
          <p>{unavailable}</p>
          <p>
            在服务器的 <code className="font-mono">/etc/xym.env</code> 里设{" "}
            <code className="font-mono">OPENAI_API_KEY</code>、{" "}
            <code className="font-mono">OPENAI_BASE_URL</code>（魔搭的接口地址），模型设{" "}
            <code className="font-mono">XYM_AGENT_MODEL</code>，然后重启 xym。
          </p>
        </Callout>
      )}

      {/* 高度撑满一屏：对话列表和聊天记录各自滚动 */}
      <Card className="grid h-[calc(100svh-10rem)] min-h-[32rem] grid-cols-[20rem_1fr] gap-0 py-0">
        <div className="flex min-h-0 flex-col border-r">
          <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">{list}</div>
        </div>
        <div className="min-h-0 min-w-0">{panel}</div>
      </Card>

      {creating && (
        <NewChatDialog
          onClose={() => setCreating(false)}
          onCreated={(chatId) => {
            setCreating(false)
            open(chatId)
          }}
        />
      )}
    </div>
  )
}
