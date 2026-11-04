import {
  ArrowRightLeftIcon,
  EllipsisIcon,
  PencilIcon,
  PlusIcon,
  QrCodeIcon,
  RefreshCwIcon,
  Trash2Icon,
} from "lucide-react"
import { useMemo, useState } from "react"
import { useNavigate } from "react-router"
import { toast } from "sonner"

import { AccountAvatar } from "@/components/account-bits"
import { CountFilter } from "@/components/count-filter"
import { LoginDialog } from "@/components/login-dialog"
import { PageHeader } from "@/components/page-header"
import { EmptyState, LoadError, RowsSkeleton, SearchInput } from "@/components/states"
import { StatusPill, type Tone } from "@/components/status-pill"
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
  TableFooter,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useAccount } from "@/hooks/use-account"
import { useUrlParam } from "@/hooks/use-url-param"
import { IM_LABEL, LOGIN_LABEL, accountName, loginHint, loginNote } from "@/lib/account"
import { errorMessage, type Account, type ImState, type LoginState } from "@/lib/api"
import { count } from "@/lib/format"
import { cn } from "@/lib/utils"

import { RemarkDialog, RemoveAccountDialog } from "./account-dialogs"
import { useAccountsLive, useCheckAccount } from "./queries"

type View = "all" | "attention" | "to_ship" | "unread"

const VIEWS: { value: View; label: string }[] = [
  { value: "all", label: "全部账号" },
  { value: "attention", label: "登录要处理" },
  { value: "to_ship", label: "有待发货" },
  { value: "unread", label: "有未读消息" },
]

/** 登录要人处理：失效、风控、没查出来，或者正在熔断（后端只给还没到期的熔断） */
function needsAttention(a: Account): boolean {
  return a.login.state !== "valid" || a.block != null
}

function inView(a: Account, view: View): boolean {
  switch (view) {
    case "all":
      return true
    case "attention":
      return needsAttention(a)
    case "to_ship":
      return a.stats.to_ship > 0
    case "unread":
      return a.stats.unread > 0
  }
}

const LOGIN_TONE: Record<LoginState, Tone> = {
  valid: "positive",
  expired: "negative",
  risk: "warning",
  error: "neutral",
}

const IM_DOT: Record<ImState, string> = {
  online: "bg-positive",
  connecting: "bg-muted-foreground",
  waiting: "bg-muted-foreground",
  // 断开用琥珀色：登录失效已经是红的，一行里只留一个红色
  stopped: "bg-warning-ink",
  off: "bg-border",
}

/** 标题行的说明：最要紧的写在最前面 */
function summary(accounts: Account[]): string {
  const attention = accounts.filter(needsAttention).length
  const toShip = accounts.reduce((sum, a) => sum + a.stats.to_ship, 0)
  const unread = accounts.reduce((sum, a) => sum + a.stats.unread, 0)
  const parts = [
    attention > 0 && `${attention} 个账号登录要处理`,
    toShip > 0 && `${toShip} 笔待发货`,
    unread > 0 && `${unread} 条未读消息`,
  ].filter(Boolean)
  const total = `共 ${accounts.length} 个账号`
  return parts.length ? `${parts.join(" · ")} · ${total}` : `${total}，登录都正常，没有待发货和未读`
}

type Action = { kind: "remark" | "remove"; account: Account }
type Go = (account: Account, path: string) => void

function LoginCell({ account }: { account: Account }) {
  const blocked = account.block != null
  const { state } = account.login
  return (
    <div className="flex flex-col items-start gap-1">
      {blocked ? (
        <StatusPill tone="warning" title={account.block?.reason}>
          熔断中
        </StatusPill>
      ) : (
        <StatusPill tone={LOGIN_TONE[state]} title={account.login.message || undefined}>
          {LOGIN_LABEL[state]}
        </StatusPill>
      )}
      <span className="text-xs text-muted-foreground">
        {blocked ? "风控暂停中" : loginNote(account.login)}
      </span>
    </div>
  )
}

function ImCell({ account }: { account: Account }) {
  const { state, message } = account.im
  return (
    <span
      className="inline-flex items-center gap-1.5 text-muted-foreground"
      title={state === "stopped" || state === "waiting" ? message : undefined}
    >
      <span className={cn("size-2 shrink-0 rounded-full", IM_DOT[state])} aria-hidden />
      {IM_LABEL[state]}
    </span>
  )
}

/** 数字格子：大于 0 时能点，切到这个账号、打开对应的页面 */
function StatLink({
  value,
  label,
  onOpen,
  strong = false,
}: {
  value: number
  label: string
  onOpen: () => void
  strong?: boolean
}) {
  if (value === 0) return <span className="text-muted-foreground tabular-nums">0</span>
  return (
    <button
      type="button"
      onClick={onOpen}
      aria-label={label}
      className={cn(
        "rounded-sm tabular-nums underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50",
        strong && "font-semibold",
      )}
    >
      {count(value)}
    </button>
  )
}

function AccountCell({ account, current }: { account: Account; current: boolean }) {
  const { profile } = account
  const facts = [
    profile.ip_location && `IP 属地${profile.ip_location}`,
    profile.seller_credit,
    profile.followers != null && `粉丝 ${count(profile.followers)}`,
  ].filter(Boolean)
  return (
    <div className="flex min-w-0 items-center gap-3">
      <AccountAvatar account={account} size={40} />
      <div className="min-w-0">
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          {profile.nick ? (
            <span className="truncate font-medium">{profile.nick}</span>
          ) : (
            <span className="font-mono font-medium" translate="no">
              {account.id}
            </span>
          )}
          {account.remark && (
            <span className="rounded-md border px-1.5 text-xs leading-5 text-muted-foreground">
              {account.remark}
            </span>
          )}
          {current && <StatusPill tone="action">当前</StatusPill>}
        </div>
        <div className="mt-0.5 flex flex-wrap gap-x-2 text-xs text-muted-foreground">
          {profile.nick ? (
            <>
              <span className="font-mono" translate="no">
                {account.id}
              </span>
              {facts.map((fact) => (
                <span key={fact as string}>· {fact}</span>
              ))}
            </>
          ) : (
            <span>登录有效时会拉昵称和头像</span>
          )}
        </div>
      </div>
    </div>
  )
}

function AccountMenu({
  account,
  onAction,
  onLogin,
}: {
  account: Account
  onAction: (action: Action) => void
  onLogin: () => void
}) {
  const check = useCheckAccount()
  const name = accountName(account)
  const recheck = () =>
    check.mutate(account.id, {
      onSuccess: (checked) => {
        if (checked.login.state === "valid") {
          toast.success(`「${name}」登录有效${checked.login.renewed ? "，刚做了免密续期" : ""}`)
        } else {
          toast.error(`「${name}」登录${LOGIN_LABEL[checked.login.state]}：${loginHint(checked.login)}`)
        }
      },
      onError: (error) => toast.error(errorMessage(error)),
    })
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="icon-sm" aria-label={`${name} 的更多操作`}>
          {check.isPending ? <RefreshCwIcon className="animate-spin" /> : <EllipsisIcon />}
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-44">
        <DropdownMenuItem onSelect={() => onAction({ kind: "remark", account })}>
          <PencilIcon />
          改备注
        </DropdownMenuItem>
        <DropdownMenuItem disabled={check.isPending} onSelect={recheck}>
          <RefreshCwIcon />
          重新检查登录态
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={onLogin}>
          <QrCodeIcon />
          重新登录
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem variant="destructive" onSelect={() => onAction({ kind: "remove", account })}>
          <Trash2Icon />
          移除账号
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function AccountsTable({
  accounts,
  current,
  go,
  onAction,
  onLogin,
}: {
  accounts: Account[]
  current: string | null
  go: Go
  onAction: (action: Action) => void
  onLogin: () => void
}) {
  const sum = (key: keyof Account["stats"]) => accounts.reduce((n, a) => n + a.stats[key], 0)
  return (
    <Table>
      <TableHeader>
        <TableRow>
          <TableHead className="w-full min-w-72">账号</TableHead>
          <TableHead>登录</TableHead>
          <TableHead>消息连接</TableHead>
          <TableHead className="text-right">在售</TableHead>
          <TableHead className="text-right">待发货</TableHead>
          <TableHead className="text-right">未读</TableHead>
          <TableHead>
            <span className="sr-only">操作</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {accounts.map((a) => {
          const isCurrent = a.id === current
          const name = accountName(a)
          return (
            <TableRow key={a.id}>
              <TableCell className="whitespace-normal">
                <AccountCell account={a} current={isCurrent} />
              </TableCell>
              <TableCell>
                <LoginCell account={a} />
              </TableCell>
              <TableCell>
                <ImCell account={a} />
              </TableCell>
              <TableCell className="text-right">
                <StatLink
                  value={a.stats.on_sale}
                  label={`看「${name}」的 ${a.stats.on_sale} 件在售商品`}
                  onOpen={() => go(a, "/items")}
                />
              </TableCell>
              <TableCell className="text-right">
                <StatLink
                  value={a.stats.to_ship}
                  label={`看「${name}」的 ${a.stats.to_ship} 笔待发货订单`}
                  onOpen={() => go(a, "/orders?status=to_ship")}
                  strong
                />
              </TableCell>
              <TableCell className="text-right">
                <StatLink
                  value={a.stats.unread}
                  label={`看「${name}」的未读消息`}
                  onOpen={() => go(a, "/messages?view=unread")}
                  strong
                />
              </TableCell>
              <TableCell>
                <div className="flex items-center justify-end gap-1">
                  {a.login.state === "expired" && (
                    <Button variant="outline" size="sm" onClick={onLogin}>
                      <QrCodeIcon />
                      重新登录
                    </Button>
                  )}
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={isCurrent}
                    onClick={() => go(a, "/items")}
                    aria-label={isCurrent ? `${name} 是当前账号` : `切换到 ${name}`}
                  >
                    <ArrowRightLeftIcon />
                    {isCurrent ? "使用中" : "切换"}
                  </Button>
                  <AccountMenu account={a} onAction={onAction} onLogin={onLogin} />
                </div>
              </TableCell>
            </TableRow>
          )
        })}
      </TableBody>
      {accounts.length > 1 && (
        <TableFooter>
          <TableRow>
            <TableCell className="font-normal text-muted-foreground">
              合计 {accounts.length} 个账号
            </TableCell>
            <TableCell />
            <TableCell />
            <TableCell className="text-right tabular-nums">{count(sum("on_sale"))}</TableCell>
            <TableCell className="text-right tabular-nums">{count(sum("to_ship"))}</TableCell>
            <TableCell className="text-right tabular-nums">{count(sum("unread"))}</TableCell>
            <TableCell />
          </TableRow>
        </TableFooter>
      )}
    </Table>
  )
}

/**
 * 账号矩阵：名下所有闲鱼账号放在一张表里，看每个号的登录、消息连接和要处理的事；
 * 在这里加号、改备注、移除，点"切换"或者点数字切过去干活。
 */
export function AccountsPage() {
  const query = useAccountsLive()
  const { account: current, setAccount } = useAccount()
  const navigate = useNavigate()
  const [view, setView] = useUrlParam<View>("view", "all", VIEWS.map((v) => v.value))
  const [search, setSearch] = useState("")
  const [action, setAction] = useState<Action | null>(null)
  const [loginOpen, setLoginOpen] = useState(false)
  const accounts = useMemo(() => query.data ?? [], [query.data])

  const go: Go = (a, path) => {
    if (a.id !== current) {
      setAccount(a.id)
      toast.success(`已切换到「${accountName(a)}」`)
    }
    navigate(path)
  }
  const openLogin = () => setLoginOpen(true)

  const words = search.trim().toLowerCase()
  const inTab = accounts.filter((a) => inView(a, view))
  const visible = words
    ? inTab.filter((a) =>
        [a.id, a.profile.nick, a.remark].some((text) => text.toLowerCase().includes(words)),
      )
    : inTab

  let body
  if (query.isPending) body = <RowsSkeleton rows={3} height="h-14" />
  else if (query.error) body = <LoadError what="读取账号" error={query.error} onRetry={() => void query.refetch()} />
  else if (accounts.length === 0)
    body = (
      <EmptyState
        title="还没有闲鱼账号"
        action={
          <Button onClick={openLogin}>
            <QrCodeIcon />
            登录闲鱼
          </Button>
        }
      >
        用闲鱼 App 扫码，或者导入浏览器里的登录。登录几个号，这里就有几行。
      </EmptyState>
    )
  else if (visible.length === 0)
    body = (
      <EmptyState>
        {words
          ? `「${search.trim()}」没有匹配的账号`
          : `没有${VIEWS.find((v) => v.value === view)?.label}的账号`}
      </EmptyState>
    )
  else
    body = (
      <AccountsTable
        accounts={visible}
        current={current}
        go={go}
        onAction={setAction}
        onLogin={openLogin}
      />
    )

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="账号矩阵"
        description={query.data ? summary(accounts) : "名下的闲鱼账号"}
        actions={
          <Button onClick={openLogin}>
            <PlusIcon />
            添加账号
          </Button>
        }
      />

      <div className="flex flex-col gap-3">
        <CountFilter
          label="账号筛选"
          value={view}
          onChange={setView}
          options={VIEWS.map(({ value, label }) => ({
            value,
            label,
            count: query.data ? accounts.filter((a) => inView(a, value)).length : null,
          }))}
        />
        <Card className="gap-0 py-0">
          <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-b px-4 py-3">
            <SearchInput
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="搜昵称、备注或账号 ID…"
              aria-label="搜索账号"
              className="w-64"
            />
            {words && inTab.length > 0 && (
              <span className="text-xs text-muted-foreground tabular-nums">
                {visible.length} / {inTab.length} 个
              </span>
            )}
          </div>
          {body}
        </Card>
      </div>

      {action?.kind === "remark" && (
        <RemarkDialog account={action.account} onClose={() => setAction(null)} />
      )}
      {action?.kind === "remove" && (
        <RemoveAccountDialog account={action.account} onClose={() => setAction(null)} />
      )}
      <LoginDialog open={loginOpen} onOpenChange={setLoginOpen} />
    </div>
  )
}
