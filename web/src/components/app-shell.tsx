import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  BotIcon,
  CheckIcon,
  ChevronsUpDownIcon,
  FileTextIcon,
  KeyRoundIcon,
  LayoutGridIcon,
  LinkIcon,
  LogOutIcon,
  MessageSquareIcon,
  MessageSquareWarningIcon,
  MoonIcon,
  PackageIcon,
  PlugIcon,
  PlusIcon,
  QrCodeIcon,
  RefreshCwIcon,
  SearchIcon,
  ShoppingCartIcon,
  SquarePenIcon,
  SunIcon,
  UserIcon,
  WarehouseIcon,
} from "lucide-react"
import { useTheme } from "next-themes"
import { useState, type ComponentType, type ReactNode } from "react"
import { NavLink, Outlet, useLocation, useMatch, useNavigate } from "react-router"
import { toast } from "sonner"

import { AccountAvatar, LoginDot } from "@/components/account-bits"
import { Brand } from "@/components/brand"
import { Callout } from "@/components/callout"
import { GuardBanner } from "@/components/guard-banner"
import { LoginDialog } from "@/components/login-dialog"
import { EmptyState } from "@/components/states"
import { Button } from "@/components/ui/button"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Skeleton } from "@/components/ui/skeleton"
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip"
import { PasswordDialog } from "@/features/auth/password-dialog"
import { PluginTokenDialog } from "@/features/auth/plugin-token-dialog"
import { FeedbackDialog } from "@/features/feedback/feedback-dialog"
import { useAccount } from "@/hooks/use-account"
import { useUser } from "@/hooks/use-auth"
import { LOGIN_LABEL, accountName, loginHint, maskId } from "@/lib/account"
import { api, errorMessage, type Account, type LoginStatus } from "@/lib/api"
import { signOut } from "@/lib/auth"
import { dateTime } from "@/lib/format"
import { cn } from "@/lib/utils"

interface NavEntry {
  to: string
  label: string
  icon: ComponentType<{ className?: string }>
}

// 当前账号的几页
const NAV: NavEntry[] = [
  { to: "/items", label: "商品", icon: PackageIcon },
  { to: "/publish", label: "发布", icon: SquarePenIcon },
  { to: "/orders", label: "订单", icon: ShoppingCartIcon },
  { to: "/messages", label: "消息", icon: MessageSquareIcon },
  { to: "/search", label: "搜索", icon: SearchIcon },
]
// 不分账号的（管所有账号的、名下账号共用的），和上面分开放
const SHARED_NAV: NavEntry[] = [
  { to: "/accounts", label: "账号矩阵", icon: LayoutGridIcon },
  { to: "/pdd", label: "拼多多货源", icon: WarehouseIcon },
  { to: "/sources", label: "第三方货源", icon: LinkIcon },
  { to: "/agent", label: "AI 助手", icon: BotIcon },
  { to: "/templates", label: "模板管理", icon: FileTextIcon },
  { to: "/feedback", label: "问题反馈", icon: MessageSquareWarningIcon },
]

const NAV_ITEM =
  "flex items-center gap-2 rounded-lg text-sm transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
const NAV_ACTIVE = "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
const NAV_IDLE = "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground"

/** 窄栏里只剩图标，悬停或键盘移过来时在右边显示名字 */
function RailTip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      {/* 悬停时把窗口拉宽到 1024px 以上，窄栏藏起来了，提示也跟着藏 */}
      <TooltipContent side="right" sideOffset={6} className="lg:hidden">
        {label}
      </TooltipContent>
    </Tooltip>
  )
}

function SideNavLink({ to, label, icon: Icon, compact }: NavEntry & { compact: boolean }) {
  // 选中状态自己算：套在提示里时 className 不能传函数（Radix 合并 className 会把函数拼成字符串）
  const active = !!useMatch({ path: to, end: false })
  const link = (
    <NavLink
      to={to}
      aria-label={compact ? label : undefined}
      className={cn(NAV_ITEM, compact ? "size-8 justify-center" : "h-8 px-2.5", active ? NAV_ACTIVE : NAV_IDLE)}
    >
      <Icon className="size-4" />
      {!compact && label}
    </NavLink>
  )
  return compact ? <RailTip label={label}>{link}</RailTip> : link
}

/**
 * 侧栏里竖着排。当前账号的几页在上，不分账号的隔开放在下面。
 * `compact`：1024px 以下的窄栏，只有图标，名字悬停时显示。
 */
function SideNav({ compact = false }: { compact?: boolean }) {
  const column = cn("flex flex-col gap-0.5", compact && "items-center")
  return (
    <nav aria-label="主导航" className={column}>
      {NAV.map((entry) => (
        <SideNavLink key={entry.to} {...entry} compact={compact} />
      ))}
      <div className={cn(column, "mt-3")}>
        {SHARED_NAV.map((entry) => (
          <SideNavLink key={entry.to} {...entry} compact={compact} />
        ))}
      </div>
    </nav>
  )
}

/**
 * 当前账号和登录态，放在侧栏顶上（这个后台管的是哪个账号）；窄栏里只剩头像，登录态是右下角的圆点。
 * 菜单里能切到别的账号、添加账号、去账号矩阵管所有账号。
 */
function AccountSwitcher({ onLogin, compact = false }: { onLogin: () => void; compact?: boolean }) {
  const { accounts, account, setAccount } = useAccount()
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  const check = useMutation({
    mutationFn: api.checkAccount,
    onSuccess: (checked) => {
      queryClient.setQueryData<Account[]>(["accounts"], (old) =>
        old?.map((a) => (a.id === checked.id ? checked : a)),
      )
      if (checked.login.state === "valid") {
        toast.success(checked.login.renewed ? "登录有效，刚做了免密续期" : "登录有效")
      } else {
        toast.error(`登录${LOGIN_LABEL[checked.login.state]}：${loginHint(checked.login)}`)
      }
    },
    onError: (error) => toast.error(errorMessage(error)),
  })
  const current = accounts.find((a) => a.id === account)
  if (!account || !current) return null
  const state = current.login.state
  const name = accountName(current)
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        {compact ? (
          <button
            type="button"
            aria-label={`闲鱼账号 ${name}，登录${LOGIN_LABEL[state]}，点开切换`}
            className="relative shrink-0 rounded-full outline-none focus-visible:ring-3 focus-visible:ring-ring/50 aria-expanded:ring-2 aria-expanded:ring-input"
          >
            <AccountAvatar account={current} size={32} />
            <span className="absolute right-0 bottom-0 flex rounded-full ring-2 ring-frame">
              <LoginDot state={state} />
            </span>
          </button>
        ) : (
          <button
            type="button"
            aria-label={`闲鱼账号 ${name}，登录${LOGIN_LABEL[state]}，点开切换`}
            className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors outline-none hover:bg-sidebar-accent/60 focus-visible:ring-3 focus-visible:ring-ring/50 aria-expanded:bg-sidebar-accent"
          >
            <AccountAvatar account={current} size={32} />
            <span className="min-w-0 flex-1">
              <span className="block truncate text-sm font-medium">{name}</span>
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <LoginDot state={state} />
                <span className="truncate">
                  登录{LOGIN_LABEL[state]}
                  {current.remark && ` · ${current.remark}`}
                </span>
              </span>
            </span>
            <ChevronsUpDownIcon className="size-4 shrink-0 text-muted-foreground" />
          </button>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent side={compact ? "right" : "bottom"} align="start" className="w-72">
        <DropdownMenuLabel>切换账号</DropdownMenuLabel>
        {accounts.map((a) => (
          <DropdownMenuItem key={a.id} onSelect={() => setAccount(a.id)} className="gap-2.5">
            <AccountAvatar account={a} size={28} />
            <div className="flex min-w-0 flex-1 flex-col">
              <span className="flex min-w-0 items-center gap-1.5">
                <span className="truncate">{accountName(a)}</span>
                {a.remark && (
                  <span className="shrink-0 text-xs text-muted-foreground">{a.remark}</span>
                )}
              </span>
              <span className="flex items-center gap-1.5 text-xs text-muted-foreground">
                <LoginDot state={a.login.state} />
                <span>{LOGIN_LABEL[a.login.state]}</span>
                {a.profile.nick && (
                  <span className="font-mono" translate="no">
                    {maskId(a.id)}
                  </span>
                )}
              </span>
            </div>
            {a.id === account && <CheckIcon className="ml-auto" />}
          </DropdownMenuItem>
        ))}
        <DropdownMenuSeparator />
        <DropdownMenuItem
          disabled={check.isPending}
          onSelect={(event) => {
            // 菜单不关，检查完直接在上面看到结果
            event.preventDefault()
            check.mutate(account)
          }}
        >
          <RefreshCwIcon className={cn(check.isPending && "animate-spin")} />
          重新检查当前账号的登录态
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={onLogin}>
          {state === "expired" ? <QrCodeIcon /> : <PlusIcon />}
          {state === "expired" ? "重新登录" : "添加账号"}
        </DropdownMenuItem>
        <DropdownMenuItem onSelect={() => navigate("/accounts")}>
          <LayoutGridIcon />
          管理全部账号
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

/** 登录的用户：改密码、插件令牌、反馈问题、退出登录。放在侧栏底下，窄栏里是一个图标。 */
function UserMenu({ compact = false }: { compact?: boolean }) {
  const { username, is_admin: isAdmin } = useUser()
  const [dialog, setDialog] = useState<"password" | "plugin" | "feedback" | null>(null)
  // 在哪个页面点的反馈，一起交上去；在问题反馈页上点的不用记
  const [feedbackPage, setFeedbackPage] = useState("")
  const { pathname } = useLocation()
  const close = () => setDialog(null)
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          {compact ? (
            <Button
              variant="ghost"
              size="icon"
              aria-label={`用户 ${username}`}
              className="text-muted-foreground aria-expanded:bg-sidebar-accent"
            >
              <UserIcon />
            </Button>
          ) : (
            <button
              type="button"
              className={cn(NAV_ITEM, NAV_IDLE, "h-8 w-full px-2.5 aria-expanded:bg-sidebar-accent")}
            >
              <UserIcon className="size-4" />
              <span className="min-w-0 flex-1 truncate text-left">{username}</span>
              <ChevronsUpDownIcon className="size-4 shrink-0" />
            </button>
          )}
        </DropdownMenuTrigger>
        <DropdownMenuContent
          side={compact ? "right" : "top"}
          align={compact ? "end" : "start"}
          className="w-56"
        >
          <DropdownMenuLabel>
            <span className="block text-xs font-normal text-muted-foreground">
              {isAdmin ? "已登录 · 管理员" : "已登录"}
            </span>
            <span className="block truncate">{username}</span>
          </DropdownMenuLabel>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => setDialog("password")}>
            <KeyRoundIcon />
            修改密码
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => setDialog("plugin")}>
            <PlugIcon />
            浏览器插件令牌
          </DropdownMenuItem>
          <DropdownMenuItem
            onSelect={() => {
              setFeedbackPage(pathname === "/feedback" ? "" : pathname)
              setDialog("feedback")
            }}
          >
            <MessageSquareWarningIcon />
            反馈问题…
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => void signOut()}>
            <LogOutIcon />
            退出登录
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {dialog === "password" && <PasswordDialog onClose={close} />}
      {dialog === "plugin" && <PluginTokenDialog onClose={close} />}
      {dialog === "feedback" && <FeedbackDialog page={feedbackPage} onClose={close} />}
    </>
  )
}

function ThemeToggle({ labelled = false }: { labelled?: boolean }) {
  const { resolvedTheme, setTheme } = useTheme()
  const dark = resolvedTheme === "dark"
  const label = dark ? "切换到浅色" : "切换到深色"
  const Icon = dark ? SunIcon : MoonIcon
  if (labelled) {
    return (
      <button
        type="button"
        onClick={() => setTheme(dark ? "light" : "dark")}
        className={cn(NAV_ITEM, NAV_IDLE, "h-8 w-full px-2.5")}
      >
        <Icon className="size-4" />
        {label}
      </button>
    )
  }
  return (
    <RailTip label={label}>
      <Button
        variant="ghost"
        size="icon"
        onClick={() => setTheme(dark ? "light" : "dark")}
        aria-label={label}
        className="text-muted-foreground"
      >
        <Icon />
      </Button>
    </RailTip>
  )
}

/** 保持登录还剩几天时开始提醒 */
const KEEP_LOGIN_WARN_DAYS = 3

/** 保持登录快到期或已经到期时的提醒；会话已失效、风控时有别的提示，不用这个 */
function keepLoginWarning(login: LoginStatus): string | null {
  const until = login.keep_login_until
  if (!until || login.state === "expired" || login.state === "risk") return null
  const left = until * 1000 - Date.now()
  if (left > KEEP_LOGIN_WARN_DAYS * 24 * 3600 * 1000) return null
  const when = left > 0 ? `保持登录 ${dateTime(until)} 到期` : "保持登录已经到期"
  return `${when}，之后会话一过期就续不上了。在 Chrome 里重新登录闲鱼、打开“保存登录信息”，插件会自动同步过来。`
}

function LoginBanner({ login, onLogin }: { login: LoginStatus; onLogin: () => void }) {
  const keepLogin = keepLoginWarning(login)
  if (login.state === "expired") {
    return (
      <Callout
        tone="negative"
        title="闲鱼登录已失效，免密续期也没成功，要重新登录"
        action={
          <Button variant="outline" size="sm" onClick={onLogin}>
            <QrCodeIcon />
            重新登录
          </Button>
        }
      />
    )
  }
  if (login.state === "risk") {
    return (
      <Callout tone="warning" title="触发了闲鱼风控，先暂停操作">
        <p>到闲鱼 App 或网页完成验证后，再在账号菜单里点「重新检查登录态」。</p>
      </Callout>
    )
  }
  if (!keepLogin) return null
  return <Callout tone="warning">{keepLogin}</Callout>
}

function Content({ onLogin }: { onLogin: () => void }) {
  const { accounts, account, isLoading, error } = useAccount()
  // 账号矩阵管的是所有账号、货源、AI 助手和模板是名下账号共用的，问题反馈跟着后台用户走：
  // 没有账号时也能进，也不放当前账号的登录、熔断提示
  const onAccounts = useMatch("/accounts")
  const onPdd = useMatch("/pdd")
  const onSources = useMatch("/sources")
  const onAgent = useMatch("/agent")
  const onTemplates = useMatch("/templates")
  const onFeedback = useMatch("/feedback")
  if (onAccounts || onPdd || onSources || onAgent || onTemplates || onFeedback) return <Outlet />
  if (isLoading) {
    return (
      <div className="flex flex-col gap-6">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }
  if (error) {
    return (
      <EmptyState title="连不上后台" className="py-24">
        {errorMessage(error)}。先确认 <code className="font-mono">uv run xym serve</code> 在运行。
      </EmptyState>
    )
  }
  if (!account) {
    return (
      <EmptyState
        title="还没有闲鱼账号"
        className="py-24"
        action={
          <Button onClick={onLogin}>
            <QrCodeIcon />
            登录闲鱼
          </Button>
        }
      >
        用闲鱼 App 扫码，或者导入浏览器里的登录，就能在这里管理商品、订单和消息。
      </EmptyState>
    )
  }
  const current = accounts.find((a) => a.id === account)
  // 熔断期间顶部的熔断提示已经说了风控和下一步，登录态提示不再重复说风控
  const banner =
    current && !(current.block && current.login.state === "risk") ? (
      <LoginBanner login={current.login} onLogin={onLogin} />
    ) : null
  // 换账号时整页重新挂载，各页面的筛选和弹窗状态不会串到别的账号
  return (
    <div className="flex flex-col gap-6">
      <GuardBanner />
      {banner}
      <Outlet key={account} />
    </div>
  )
}

/**
 * 整体框架：侧栏在一块浅石色的外框上，内容放在外框里的一张白纸上（上、右、下各留 8px，16px 圆角，1px 边，没有阴影）。
 * 1024px 以下侧栏收成 56px 宽、只有图标的窄栏，名字悬停时显示；导航的字不会被挤成竖排或挤出窗口。
 */
export function AppShell() {
  const [loginOpen, setLoginOpen] = useState(false)
  const openLogin = () => setLoginOpen(true)
  return (
    <div className="flex min-h-svh bg-frame">
      <a
        href="#main"
        className="sr-only z-50 rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground focus:not-sr-only focus:fixed focus:top-2 focus:left-2"
      >
        跳到主要内容
      </a>
      <aside className="sticky top-0 hidden h-svh w-[228px] shrink-0 flex-col gap-4 px-3 pt-4 pb-3 lg:flex">
        <div className="px-2.5">
          <Brand />
        </div>
        <AccountSwitcher onLogin={openLogin} />
        <SideNav />
        <div className="mt-auto flex flex-col gap-0.5">
          <UserMenu />
          <ThemeToggle labelled />
        </div>
      </aside>
      <aside className="sticky top-0 flex h-svh w-14 shrink-0 flex-col items-center gap-4 overflow-y-auto pt-4 pb-3 lg:hidden">
        <Brand compact />
        <AccountSwitcher onLogin={openLogin} compact />
        <SideNav compact />
        <div className="mt-auto flex flex-col items-center gap-0.5">
          <UserMenu compact />
          <ThemeToggle />
        </div>
      </aside>
      <main
        id="main"
        tabIndex={-1}
        className="my-2 mr-2 min-h-[calc(100svh-1rem)] min-w-0 flex-1 rounded-2xl border bg-background outline-none"
      >
        <div className="mx-auto max-w-[1200px] px-4 pt-6 pb-10 sm:px-6">
          <Content onLogin={openLogin} />
        </div>
      </main>
      <LoginDialog open={loginOpen} onOpenChange={setLoginOpen} />
    </div>
  )
}
