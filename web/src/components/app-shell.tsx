import { useMutation, useQueryClient } from "@tanstack/react-query"
import {
  CheckIcon,
  ChevronDownIcon,
  FishIcon,
  MessageSquareIcon,
  MoonIcon,
  PackageIcon,
  RefreshCwIcon,
  ShoppingCartIcon,
  SunIcon,
  TriangleAlertIcon,
  UserIcon,
} from "lucide-react"
import { useTheme } from "next-themes"
import type { ComponentType, ReactNode } from "react"
import { NavLink, Outlet } from "react-router"
import { toast } from "sonner"

import { GuardBanner } from "@/components/guard-banner"
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
  soon?: boolean
}

const NAV: NavEntry[] = [
  { to: "/items", label: "商品", icon: PackageIcon },
  { to: "/orders", label: "订单", icon: ShoppingCartIcon },
  { to: "/messages", label: "消息", icon: MessageSquareIcon, soon: true },
]

function Brand() {
  return (
    <div className="flex items-center gap-2 font-heading text-base font-semibold">
      <span className="flex size-7 items-center justify-center rounded-lg bg-[#FFE60F] text-black">
        <FishIcon className="size-4" />
      </span>
      闲鱼管理
    </div>
  )
}

function Nav() {
  return (
    <nav className="flex flex-col gap-0.5">
      {NAV.map(({ to, label, icon: Icon, soon }) =>
        soon ? (
          <span
            key={to}
            className="flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm text-muted-foreground/70"
          >
            <Icon className="size-4" />
            {label}
            <span className="ml-auto text-xs">即将推出</span>
          </span>
        ) : (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cn(
                "flex items-center gap-2 rounded-lg px-2.5 py-1.5 text-sm font-medium transition-colors",
                isActive
                  ? "bg-sidebar-accent text-sidebar-accent-foreground"
                  : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
              )
            }
          >
            <Icon className="size-4" />
            {label}
          </NavLink>
        ),
      )}
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
  valid: "bg-emerald-500",
  expired: "bg-destructive",
  risk: "bg-amber-500",
  error: "bg-muted-foreground/50",
}

function loginHint(login: LoginStatus): string {
  switch (login.state) {
    case "valid":
      return login.keep_login_until
        ? `保持登录到 ${dateTime(login.keep_login_until)}`
        : "没开保持登录，过期后要重新扫码"
    case "expired":
      return "终端运行 uv run xianyu login 重新扫码"
    case "risk":
      return "到闲鱼 App 或网页完成验证后再检查"
    case "error":
      return "没查出来，稍后重新检查"
  }
}

function LoginDot({ state }: { state: LoginState }) {
  return (
    <span
      className={cn("size-2 shrink-0 rounded-full", LOGIN_DOT[state])}
      aria-label={`登录${LOGIN_LABEL[state]}`}
    />
  )
}

function AccountSwitcher() {
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
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-1.5">
          <UserIcon />
          <span className="font-mono">{maskId(account)}</span>
          <LoginDot state={current.login.state} />
          <ChevronDownIcon className="text-muted-foreground" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-64">
        <DropdownMenuLabel>闲鱼账号</DropdownMenuLabel>
        {accounts.map((a) => (
          <DropdownMenuItem key={a.id} onSelect={() => setAccount(a.id)}>
            <div className="flex min-w-0 flex-col">
              <span className="flex items-center gap-1.5">
                <span className="font-mono">{a.id}</span>
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
        <DropdownMenuLabel className="text-xs font-normal text-muted-foreground">
          添加账号：终端运行 uv run xianyu login
        </DropdownMenuLabel>
      </DropdownMenuContent>
    </DropdownMenu>
  )
}

function ThemeToggle() {
  const { resolvedTheme, setTheme } = useTheme()
  const dark = resolvedTheme === "dark"
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      onClick={() => setTheme(dark ? "light" : "dark")}
      aria-label={dark ? "切换到浅色" : "切换到深色"}
    >
      {dark ? <SunIcon /> : <MoonIcon />}
    </Button>
  )
}

function Notice({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="mx-auto mt-16 max-w-md rounded-xl border bg-background p-6 text-center">
      <h2 className="font-heading text-base font-medium">{title}</h2>
      <div className="mt-2 text-sm text-muted-foreground">{children}</div>
    </div>
  )
}

function LoginBanner({ login }: { login: LoginStatus }) {
  if (login.state !== "expired" && login.state !== "risk") return null
  const expired = login.state === "expired"
  return (
    <div
      className={cn(
        "mb-4 flex items-start gap-2 rounded-lg border px-3 py-2 text-sm",
        expired
          ? "border-destructive/30 bg-destructive/5"
          : "border-amber-500/30 bg-amber-500/5",
      )}
    >
      <TriangleAlertIcon
        className={cn("mt-0.5 size-4 shrink-0", expired ? "text-destructive" : "text-amber-600")}
      />
      {expired ? (
        <p>
          闲鱼登录已失效，免密续期也没成功。在终端运行{" "}
          <code className="rounded bg-muted px-1 py-0.5">uv run xianyu login</code>{" "}
          重新扫码，再在右上角账号菜单里点「重新检查登录态」。
        </p>
      ) : (
        <p>
          触发了闲鱼风控，先暂停操作。到闲鱼 App 或网页完成验证后，再在右上角账号菜单里点「重新检查登录态」。
        </p>
      )}
    </div>
  )
}

function Content() {
  const { accounts, account, isLoading, error } = useAccount()
  if (isLoading) {
    return (
      <div className="flex flex-col gap-4">
        <Skeleton className="h-8 w-40" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }
  if (error) {
    return <Notice title="连不上后台">{errorMessage(error)}</Notice>
  }
  if (!account) {
    return (
      <Notice title="还没有闲鱼账号">
        在项目目录运行 <code className="rounded bg-muted px-1 py-0.5">uv run xianyu login</code>
        ，用闲鱼 App 扫码登录后刷新本页。
      </Notice>
    )
  }
  const current = accounts.find((a) => a.id === account)
  // 换账号时整页重新挂载，各页面的筛选和弹窗状态不会串到别的账号
  return (
    <>
      <GuardBanner />
      {/* 熔断期间顶部的熔断提示已经说了风控和下一步，登录态提示不再重复说风控 */}
      {current && !(current.block && current.login.state === "risk") && (
        <LoginBanner login={current.login} />
      )}
      <Outlet key={account} />
    </>
  )
}

export function AppShell() {
  return (
    <div className="flex min-h-svh bg-muted/40">
      <aside className="sticky top-0 hidden h-svh w-56 shrink-0 flex-col gap-6 border-r bg-sidebar px-3 py-4 md:flex">
        <div className="px-2.5">
          <Brand />
        </div>
        <Nav />
      </aside>
      <div className="flex min-w-0 flex-1 flex-col">
        <header className="sticky top-0 z-20 flex h-14 items-center gap-2 border-b bg-background/80 px-4 backdrop-blur md:px-8">
          <div className="md:hidden">
            <Brand />
          </div>
          <div className="flex-1" />
          <AccountSwitcher />
          <ThemeToggle />
        </header>
        <main className="flex-1 px-4 py-6 md:px-8">
          <div className="mx-auto max-w-6xl">
            <Content />
          </div>
        </main>
      </div>
    </div>
  )
}
