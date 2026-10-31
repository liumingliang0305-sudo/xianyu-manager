import {
  ArrowLeftIcon,
  ExternalLinkIcon,
  Loader2Icon,
  MessageSquareIcon,
  RefreshCwIcon,
  SearchIcon,
} from "lucide-react"
import { useEffect, useLayoutEffect, useRef, useState } from "react"
import { useSearchParams } from "react-router"
import { toast } from "sonner"

import { Badge } from "@/components/ui/badge"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { useCurrentAccount } from "@/hooks/use-account"
import {
  errorMessage,
  type ChatMessage,
  type Conversation,
  type ImState,
  type ImStatus,
} from "@/lib/api"
import { chatTime, itemUrl, orderUrl, thumb, timeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"

import {
  useConversations,
  useFetchMessages,
  useMessageStream,
  useReconnectIm,
  useThread,
} from "./queries"

/** 打开会话时先显示本地最新的几条，往前翻每次多显示这么多 */
const PAGE = 50
/** 两条消息隔这么久（秒）就在中间标一下时间 */
const TIME_GAP = 5 * 60

type Tab = "item" | "all"

const IM_LOOK: Record<ImState, { dot: string; label: string }> = {
  online: { dot: "bg-emerald-500", label: "实时接收中" },
  connecting: { dot: "bg-amber-500 animate-pulse", label: "正在连接…" },
  waiting: { dot: "bg-amber-500", label: "连接断了，稍后自动重连" },
  stopped: { dot: "bg-destructive", label: "已停止接收" },
  off: { dot: "bg-muted-foreground/50", label: "没有连接" },
}

function imHint(im: ImStatus): string {
  if (im.state === "stopped" && im.needs === "login") return "闲鱼登录失效了，重新登录后会自动连上"
  if (im.state === "stopped" && im.needs === "unblock")
    return "账号在风控暂停中，完成验证、解除熔断后会自动连上"
  return im.message
}

function ImStatusBar({ account, im }: { account: string; im: ImStatus | undefined }) {
  const reconnect = useReconnectIm(account)
  if (!im) return null
  const look = IM_LOOK[im.state]
  const hint = im.state === "online" || im.state === "connecting" ? "" : imHint(im)
  const canReconnect =
    im.state === "waiting" || (im.state === "stopped" && im.needs !== "login" && im.needs !== "unblock")
  return (
    <div className="flex min-w-0 items-center gap-2 text-sm">
      <span className={cn("size-2 shrink-0 rounded-full", look.dot)} />
      <span className="shrink-0">{look.label}</span>
      {hint && <span className="truncate text-muted-foreground" title={hint}>{hint}</span>}
      {canReconnect && (
        <Button
          variant="outline"
          size="sm"
          className="shrink-0"
          disabled={reconnect.isPending}
          onClick={() => reconnect.mutate(undefined, { onError: (e) => toast.error(errorMessage(e)) })}
        >
          <RefreshCwIcon className={cn(reconnect.isPending && "animate-spin")} />
          重新连接
        </Button>
      )}
    </div>
  )
}

function UnreadCount({ n }: { n: number }) {
  return <span className="text-xs text-muted-foreground tabular-nums">{n > 99 ? "99+" : n}</span>
}

function peerName(c: Conversation): string {
  return c.peer_name || c.peer_id || (c.biz_type === "1" ? "买家" : "系统消息")
}

function Avatar({ name, className }: { name: string; className?: string }) {
  return (
    <span
      className={cn(
        "flex size-9 shrink-0 items-center justify-center rounded-full bg-muted font-medium text-muted-foreground",
        className,
      )}
      aria-hidden
    >
      {Array.from(name)[0] ?? "?"}
    </span>
  )
}

function ConversationRow({
  account,
  conversation: c,
  active,
  onOpen,
}: {
  account: string
  conversation: Conversation
  active: boolean
  onOpen: () => void
}) {
  const preview = c.last_text
    ? `${c.last_sender_id === account ? "我：" : ""}${c.last_text}`
    : c.item_title || (c.stale ? "打开后拉取消息" : "")
  return (
    <button
      type="button"
      onClick={onOpen}
      className={cn(
        "flex w-full items-center gap-3 px-3 py-2.5 text-left transition-colors hover:bg-muted/60",
        active && "bg-muted hover:bg-muted",
      )}
    >
      <Avatar name={peerName(c)} />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="truncate font-medium">{peerName(c)}</span>
          <span className="ml-auto shrink-0 text-xs text-muted-foreground">
            {c.updated_at ? timeAgo(c.updated_at) : ""}
          </span>
        </div>
        <div className="mt-0.5 flex items-center gap-2">
          <span className="truncate text-xs text-muted-foreground">{preview}</span>
          {c.unread > 0 && (
            <Badge variant="destructive" className="ml-auto tabular-nums">
              {c.unread}
            </Badge>
          )}
        </div>
      </div>
      {c.item_pic && (
        <img src={thumb(c.item_pic, 80)} alt="" className="size-10 shrink-0 rounded-md object-cover" />
      )}
    </button>
  )
}

function Bubble({ message: m }: { message: ChatMessage }) {
  return (
    <div className={cn("flex", m.mine ? "justify-end" : "justify-start")}>
      <div
        className={cn(
          "max-w-[75%] rounded-2xl px-3 py-2 text-sm break-words whitespace-pre-wrap",
          m.mine ? "rounded-br-md bg-[#FFE60F] text-black" : "rounded-bl-md bg-muted",
          m.content_type !== 1 && "text-muted-foreground italic",
          m.mine && m.content_type !== 1 && "text-black/60",
        )}
      >
        {m.text || "（没有文字内容）"}
      </div>
    </div>
  )
}

function Thread({
  account,
  conversation,
  im,
  onBack,
}: {
  account: string
  conversation: Conversation
  im: ImStatus | undefined
  onBack: () => void
}) {
  const cid = conversation.cid
  const [limit, setLimit] = useState(PAGE)
  const thread = useThread(account, cid, limit)
  const { mutate: fetchMessages, isPending: fetchingNewest } = useFetchMessages(account)
  const older = useFetchMessages(account)
  const tried = useRef(false)
  const scroller = useRef<HTMLDivElement>(null)
  const atBottom = useRef(true)
  // 往前翻之前记下滚动位置，翻完以后让看到的内容不动
  const anchor = useRef<{ height: number; top: number } | null>(null)

  const online = im?.state === "online"
  // 打开时本地没跟上平台就补拉一次；没连上时先看本地的，连上以后再拉
  useEffect(() => {
    if (!conversation.stale || !online || tried.current) return
    tried.current = true
    fetchMessages({ cid, older: false }, { onError: (e) => toast.error(errorMessage(e)) })
  }, [conversation.stale, online, cid, fetchMessages])

  const messages = thread.data?.messages ?? []
  const fresh = thread.data?.conversation ?? conversation
  const firstId = messages[0]?.message_id
  const lastId = messages.at(-1)?.message_id

  useLayoutEffect(() => {
    const el = scroller.current
    if (el && atBottom.current) el.scrollTop = el.scrollHeight
  }, [lastId])

  useLayoutEffect(() => {
    const el = scroller.current
    if (!el || !anchor.current) return
    el.scrollTop = anchor.current.top + (el.scrollHeight - anchor.current.height)
    anchor.current = null
  }, [firstId])

  const localMore = thread.data?.local_more ?? false
  const platformMore = fresh.older_more !== false
  const loadOlder = () => {
    const el = scroller.current
    if (el) anchor.current = { height: el.scrollHeight, top: el.scrollTop }
    if (localMore) {
      setLimit((n) => n + PAGE)
      return
    }
    older.mutate(
      { cid, older: true },
      {
        onSuccess: (r) => {
          if (r.added > 0) setLimit((n) => n + r.added)
          else if (r.older_more) toast("这一页本地都有了，再点一次接着往前翻")
        },
        onError: (e) => toast.error(errorMessage(e)),
      },
    )
  }

  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex items-center gap-2 border-b px-3 py-2.5">
        <Button variant="ghost" size="icon-sm" className="md:hidden" onClick={onBack} aria-label="返回会话列表">
          <ArrowLeftIcon />
        </Button>
        <Avatar name={peerName(fresh)} className="size-8" />
        <div className="min-w-0 flex-1">
          <div className="truncate font-medium">{peerName(fresh)}</div>
          {fresh.item_title && (
            <div className="truncate text-xs text-muted-foreground">{fresh.item_title}</div>
          )}
        </div>
        {fetchingNewest && <Loader2Icon className="size-4 animate-spin text-muted-foreground" />}
        {fresh.order_id && (
          <Button variant="ghost" size="sm" asChild>
            <a href={orderUrl(fresh.order_id)} target="_blank" rel="noreferrer">
              订单
              <ExternalLinkIcon />
            </a>
          </Button>
        )}
        {fresh.item_id && (
          <Button variant="ghost" size="sm" asChild>
            <a href={itemUrl(fresh.item_id)} target="_blank" rel="noreferrer">
              商品
              <ExternalLinkIcon />
            </a>
          </Button>
        )}
      </div>

      <div
        ref={scroller}
        onScroll={(e) => {
          const el = e.currentTarget
          atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight < 80
        }}
        className="min-h-0 flex-1 overflow-y-auto px-4 py-3"
      >
        {thread.isPending ? (
          <div className="flex flex-col gap-3">
            <Skeleton className="h-9 w-48" />
            <Skeleton className="ml-auto h-9 w-40" />
            <Skeleton className="h-9 w-56" />
          </div>
        ) : thread.isError ? (
          <p className="py-10 text-center text-destructive">{errorMessage(thread.error)}</p>
        ) : messages.length === 0 ? (
          <p className="py-10 text-center text-sm text-muted-foreground">
            {fetchingNewest
              ? "正在从闲鱼拉取消息…"
              : fresh.stale && !online
                ? "本地还没有这个会话的消息，即时消息连上以后再打开会自动拉取"
                : "没有消息"}
          </p>
        ) : (
          <div className="flex flex-col gap-2">
            <div className="flex justify-center pb-1">
              {localMore || platformMore ? (
                <Button variant="ghost" size="sm" onClick={loadOlder} disabled={older.isPending}>
                  {older.isPending && <Loader2Icon className="animate-spin" />}
                  更早的消息
                </Button>
              ) : (
                <span className="text-xs text-muted-foreground">没有更早的消息了</span>
              )}
            </div>
            {messages.map((m, i) => {
              const prev = messages[i - 1]
              const gap = !prev || (m.sent_at ?? 0) - (prev.sent_at ?? 0) > TIME_GAP
              return (
                <div key={m.message_id} className="flex flex-col gap-2">
                  {gap && m.sent_at && (
                    <div className="pt-2 text-center text-xs text-muted-foreground">
                      {chatTime(m.sent_at)}
                    </div>
                  )}
                  <Bubble message={m} />
                </div>
              )
            })}
          </div>
        )}
      </div>

      <div className="border-t px-4 py-2.5 text-center text-xs text-muted-foreground">
        这里暂时只能看，回复请在闲鱼 App 里发
      </div>
    </div>
  )
}

export function MessagesPage() {
  const account = useCurrentAccount()
  useMessageStream(account)
  const query = useConversations(account)
  const [params, setParams] = useSearchParams()
  const [tab, setTab] = useState<Tab>("item")
  const [search, setSearch] = useState("")

  const selected = params.get("c")
  const conversations = query.data?.conversations ?? []
  const im = query.data?.im
  const current = conversations.find((c) => c.cid === selected)
  // 系统通知的未读很多，商品会话的单独数
  const itemUnread = conversations.reduce((sum, c) => sum + (c.biz_type === "1" ? c.unread : 0), 0)
  const allUnread = conversations.reduce((sum, c) => sum + c.unread, 0)
  const text = search.trim().toLowerCase()
  const visible = conversations.filter(
    (c) =>
      (tab === "all" || c.biz_type === "1") &&
      (!text ||
        peerName(c).toLowerCase().includes(text) ||
        c.item_title.toLowerCase().includes(text) ||
        c.last_text.toLowerCase().includes(text)),
  )
  const open = (cid: string | null) => setParams(cid ? { c: cid } : {})

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="font-heading text-2xl font-semibold">消息</h1>
          <p className="mt-1 text-sm text-muted-foreground">
            买家发来的消息实时收到这里{itemUnread > 0 ? ` · ${itemUnread} 条未读` : ""}
          </p>
        </div>
        <ImStatusBar account={account} im={im} />
      </div>

      <Card className="grid h-[calc(100svh-13rem)] min-h-[28rem] grid-cols-1 gap-0 py-0 md:grid-cols-[20rem_1fr]">
        <div className={cn("flex min-h-0 flex-col border-r", selected && "hidden md:flex")}>
          <div className="flex flex-col gap-2 border-b p-3">
            <Tabs value={tab} onValueChange={(v) => setTab(v as Tab)}>
              <TabsList className="w-full">
                <TabsTrigger value="item">
                  商品会话
                  {itemUnread > 0 && <UnreadCount n={itemUnread} />}
                </TabsTrigger>
                <TabsTrigger value="all">
                  全部
                  {allUnread > 0 && <UnreadCount n={allUnread} />}
                </TabsTrigger>
              </TabsList>
            </Tabs>
            <div className="relative">
              <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={search}
                onChange={(e) => setSearch(e.target.value)}
                placeholder="搜买家、商品或消息"
                className="pl-8"
              />
            </div>
          </div>
          <div className="min-h-0 flex-1 overflow-y-auto">
            {query.isPending ? (
              <div className="flex flex-col gap-3 p-3">
                {Array.from({ length: 5 }, (_, i) => (
                  <Skeleton key={i} className="h-12 w-full" />
                ))}
              </div>
            ) : query.isError ? (
              <p className="p-6 text-center text-destructive">{errorMessage(query.error)}</p>
            ) : conversations.length === 0 ? (
              <div className="flex flex-col items-center gap-2 px-6 py-14 text-center">
                <MessageSquareIcon className="size-8 text-muted-foreground" />
                <p className="font-medium">还没有会话</p>
                <p className="text-sm text-muted-foreground">
                  {im?.state === "online" || im?.state === "connecting"
                    ? "正在从闲鱼拉取会话列表…"
                    : "即时消息连上以后会自动拉取会话列表"}
                </p>
              </div>
            ) : visible.length === 0 ? (
              <p className="px-6 py-14 text-center text-sm text-muted-foreground">没有符合条件的会话</p>
            ) : (
              <div className="divide-y">
                {visible.map((c) => (
                  <ConversationRow
                    key={c.cid}
                    account={account}
                    conversation={c}
                    active={c.cid === selected}
                    onOpen={() => open(c.cid)}
                  />
                ))}
              </div>
            )}
          </div>
        </div>

        <div className={cn("min-h-0", !selected && "hidden md:block")}>
          {current ? (
            <Thread
              key={current.cid}
              account={account}
              conversation={current}
              im={im}
              onBack={() => open(null)}
            />
          ) : (
            <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-sm text-muted-foreground">
              <MessageSquareIcon className="size-8" />
              {selected && !query.isPending ? "找不到这个会话" : "选一个会话看消息"}
            </div>
          )}
        </div>
      </Card>
    </div>
  )
}
