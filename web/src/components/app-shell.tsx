import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  CheckIcon,
  ChevronsUpDownIcon,
  KeyRoundIcon,
  LayoutGridIcon,
  LogOutIcon,
  MessageSquareIcon,
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
import { useState, type ComponentType } from "react"
import { NavLink, Outlet, useMatch, useNavigate } from "react-router"
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
import { PasswordDialog } from "@/features/auth/password-dialog"
import { PluginTokenDialog } from "@/features/auth/plugin-token-dialog"
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
]

const NAV_ITEM =
  "flex items-center gap-2 rounded-lg text-sm transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
const NAV_ACTIVE = "bg-sidebar-accent font-medium text-sidebar-accent-foreground"
const NAV_IDLE = "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground"

function SideNavLink({ to, label, icon: Icon }: NavEntry) {
  return (
    <NavLink
      to={to}
      className={({ isActive }) => cn(NAV_ITEM, "h-8 px-2.5", isActive ? NAV_ACTIVE : NAV_IDLE)}
    >
      <Icon className="size-4" />
      {label}
    </NavLink>
  )
}

/** 宽屏：侧栏里竖着排，带图标。当前账号的几页在上，账号矩阵、拼多多货源隔开放在下面 */
function SideNav() {
  return (
    <nav aria-label="主导航" className="flex flex-col gap-0.5">
      {NAV.map((entry) => (
        <SideNavLink key={entry.to} {...entry} />
      ))}
      <div className="mt-3 flex flex-col gap-0.5">
        {SHARED_NAV.map((entry) => (
          <SideNavLink key={entry.to} {...entry} />
        ))}
      </div>
    </nav>
  )
}

/** 窄屏：去处不多，直接横着放在顶栏里，不藏进菜单 */
function TopNav() {
  return (
    <nav aria-label="主导航" className="flex items-center gap-0.5">
      {[...NAV, ...SHARED_NAV].map(({ to, label }) => (
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

/**
 * 当前账号和登录态。宽屏放在侧栏顶上（这个后台管的是哪个账号），窄屏放在顶栏右边。
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
          <Button variant="ghost" size="sm" aria-label={`闲鱼账号 ${name}，登录${LOGIN_LABEL[state]}`}>
            <span className="max-w-32 truncate">{name}</span>
            <LoginDot state={state} />
          </Button>
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
      <DropdownMenuContent align={compact ? "end" : "start"} className="w-72">
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

/** 登录的用户：改密码、插件令牌、退出登录。宽屏放在侧栏底下，窄屏是顶栏右边一个图标。 */
function UserMenu({ compact = false }: { compact?: boolean }) {
  const { username } = useUser()
  const [dialog, setDialog] = useState<"password" | "plugin" | null>(null)
  const close = () => setDialog(null)
  return (
    <>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          {compact ? (
            <Button variant="ghost" size="icon-sm" aria-label={`用户 ${username}`}>
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
        <DropdownMenuContent align={compact ? "end" : "start"} side={compact ? "bottom" : "top"} className="w-56">
          <DropdownMenuLabel>
            <span className="block text-xs font-normal text-muted-foreground">已登录</span>
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
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => void signOut()}>
            <LogOutIcon />
            退出登录
          </DropdownMenuItem>
        </DropdownMenuContent>
      </DropdownMenu>
      {dialog === "password" && <PasswordDialog onClose={close} />}
      {dialog === "plugin" && <PluginTokenDialog onClose={close} />}
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
  // 账号矩阵管的是所有账号、拼多多货源是名下账号共用的：没有账号时也能进，也不放当前账号的登录、熔断提示
  const onAccounts = useMatch("/accounts")
  const onPdd = useMatch("/pdd")
  if (onAccounts || onPdd) return <Outlet />
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
        <div className="mt-auto flex flex-col gap-0.5">
          <UserMenu />
          <ThemeToggle labelled />
        </div>
      </aside>
      <header className="sticky top-0 z-20 flex h-12 items-center gap-2 bg-frame px-4 lg:hidden">
        <Brand compact />
        <TopNav />
        <div className="flex-1" />
        <AccountSwitcher onLogin={openLogin} compact />
        <ThemeToggle />
        <UserMenu compact />
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
