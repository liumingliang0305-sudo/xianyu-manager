import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  CheckIcon,
  ChevronsUpDownIcon,
  FishIcon,
  MessageSquareIcon,
  MoonIcon,
  PackageIcon,
  QrCodeIcon,
  RefreshCwIcon,
  ShoppingCartIcon,
  SunIcon,
} from "lucide-react"
import { useTheme } from "next-themes"
import { useState, type ComponentType } from "react"
import { NavLink, Outlet } from "react-router"
import { toast } from "sonner"

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
import { useAccount } from "@/hooks/use-account"
import { api, errorMessage, type Account, type LoginState, type LoginStatus } from "@/lib/api"
import { dateTime } from "@/lib/format"
import { cn } from "@/lib/utils"

interface NavEntry {
  to: string
  label: string
  icon: ComponentType<{ className?: string }>
}

const NAV: NavEntry[] = [
  { to: "/items", label: "商品", icon: PackageIcon },
  { to: "/orders", label: "订单", icon: ShoppingCartIcon },
  { to: "/messages", label: "消息", icon: MessageSquareIcon },
]

function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2 text-[15px] font-semibold">
      <span className="flex size-7 items-center justify-center rounded-lg bg-brand text-brand-foreground">
        <FishIcon className="size-4" />
      </span>
      <span className={cn(compact && "sr-only sm:not-sr-only")}>闲鱼管理</span>
    </div>
  )
}

const NAV_ITEM =
  "flex items-center gap-2 rounded-lg text-sm transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
const NAV_ACTIVE = "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
const NAV_IDLE = "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground"

/** 宽屏：侧栏里竖着排，带图标 */
function SideNav() {
  return (
    <nav aria-label="主导航" className="flex flex-col gap-0.5">
      {NAV.map(({ to, label, icon: Icon }) => (
        <NavLink
          key={to}
          to={to}
          className={({ isActive }) => cn(NAV_ITEM, "h-8 px-2.5", isActive ? NAV_ACTIVE : NAV_IDLE)}
        >
          <Icon className="size-4" />
          {label}
        </NavLink>
      ))}
    </nav>
  )
}

/** 窄屏：只有三个去处，直接横着放在顶栏里，不藏进菜单 */
function TopNav() {
  return (
    <nav aria-label="主导航" className="flex items-center gap-0.5">
      {NAV.map(({ to, label }) => (
        <NavLink
          key={to}
          to={to}
          className={({ isActive }) => cn(NAV_ITEM, "h-8 px-2.5", isActive ? NAV_ACTIVE : NAV_IDLE)}
        >
          {label}
        </NavLink>
      ))}
    </nav>
  )
}

function maskId(id: string) {
  return id.length > 6 ? `${id.slice(0, 3)}…${id.slice(-4)}` : id
}

const LOGIN_LABEL: Record<LoginState, string> = {
  valid: "有效",
  expired: "已失效",
  risk: "风控",
  error: "未知",
}

const LOGIN_DOT: Record<LoginState, string> = {
  valid: "bg-positive",
  expired: "bg-destructive",
  risk: "bg-warning-ink",
  error: "bg-muted-foreground",
}

function loginHint(login: LoginStatus): string {
  switch (login.state) {
    case "valid":
      return login.keep_login_until
        ? `保持登录到 ${dateTime(login.keep_login_until)}`
        : "没开保持登录，过期后要重新登录"
    case "expired":
      return "要重新登录"
    case "risk":
      return "到闲鱼 App 或网页完成验证后再检查"
    case "error":
      return "没查出来，稍后重新检查"
  }
}

function LoginDot({ state }: { state: LoginState }) {
  return <span className={cn("size-2 shrink-0 rounded-full", LOGIN_DOT[state])} aria-hidden />
}

/** 当前账号和登录态。宽屏放在侧栏顶上（这个后台管的是哪个账号），窄屏放在顶栏右边。 */
function AccountSwitcher({ onLogin, compact = false }: { onLogin: () => void; compact?: boolean }) {
  const { accounts, account, setAccount } = useAccount()
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
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        {compact ? (
          <Button variant="ghost" size="sm" aria-label={`闲鱼账号 ${account}，登录${LOGIN_LABEL[state]}`}>
            <span className="font-mono" translate="no">
              {maskId(account)}
            </span>
            <LoginDot state={state} />
          </Button>
        ) : (
          <button
            type="button"
            className="flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-left transition-colors outline-none hover:bg-sidebar-accent/60 focus-visible:ring-3 focus-visible:ring-ring/50 aria-expanded:bg-sidebar-accent"
          >
            <span className="min-w-0 flex-1">
              <span className="block text-xs text-muted-foreground">闲鱼账号</span>
              <span className="flex items-center gap-1.5 text-sm">
                <span className="font-mono" translate="no">
                  {maskId(account)}
                </span>
                <LoginDot state={state} />
                <span className="text-xs text-muted-foreground">登录{LOGIN_LABEL[state]}</span>
              </span>
            </span>
            <ChevronsUpDownIcon className="size-4 shrink-0 text-muted-foreground" />
          </button>
        )}
      </DropdownMenuTrigger>
      <DropdownMenuContent align={compact ? "end" : "start"} className="w-64">
        <DropdownMenuLabel>闲鱼账号</DropdownMenuLabel>
        {accounts.map((a) => (
          <DropdownMenuItem key={a.id} onSelect={() => setAccount(a.id)}>
            <div className="flex min-w-0 flex-col">
              <span className="flex items-center gap-1.5">
                <span className="font-mono" translate="no">
                  {a.id}
                </span>
                <LoginDot state={a.login.state} />
                <span className="text-xs text-muted-foreground">{LOGIN_LABEL[a.login.state]}</span>
              </span>
              <span className="text-xs text-muted-foreground">{loginHint(a.login)}</span>
            </div>
            {a.id === account && <CheckIcon className="ml-auto" />}
          </DropdownMenuItem>
        ))}
        <DropdownMenuItem
          disabled={check.isPending}
          onSelect={(event) => {
            // 菜单不关，检查完直接在上面看到结果
            event.preventDefault()
            check.mutate(account)
          }}
        >
          <RefreshCwIcon className={cn(check.isPending && "animate-spin")} />
          重新检查登录态
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem onSelect={onLogin}>
          <QrCodeIcon />
          重新登录
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
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
    <Button variant="ghost" size="icon-sm" onClick={() => setTheme(dark ? "light" : "dark")} aria-label={label}>
      <Icon />
    </Button>
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
 * 整体框架：侧栏和窄屏顶栏在一块浅石色的外框上，内容放在外框里的一张白纸上（右、下各留 8px，16px 圆角，1px 边，没有阴影）。
 * 1024px 以下外框只剩一条 48px 的顶栏，白纸铺满。
 */
export function AppShell() {
  const [loginOpen, setLoginOpen] = useState(false)
  const openLogin = () => setLoginOpen(true)
  return (
    <div className="min-h-svh bg-frame lg:flex">
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
        <div className="mt-auto">
          <ThemeToggle labelled />
        </div>
      </aside>
      <header className="sticky top-0 z-20 flex h-12 items-center gap-2 bg-frame px-4 lg:hidden">
        <Brand compact />
        <TopNav />
        <div className="flex-1" />
        <AccountSwitcher onLogin={openLogin} compact />
        <ThemeToggle />
      </header>
      <main id="main" tabIndex={-1} className="min-h-[calc(100svh-3rem)] outline-none min-w-0 flex-1 bg-background lg:my-2 lg:mr-2 lg:min-h-[calc(100svh-1rem)] lg:rounded-2xl lg:border">
        <div className="mx-auto max-w-[1200px] px-4 pt-5 pb-10 sm:px-6 lg:pt-6">
          <Content onLogin={openLogin} />
        </div>
      </main>
      <LoginDialog open={loginOpen} onOpenChange={setLoginOpen} />
    </div>
  )
}
