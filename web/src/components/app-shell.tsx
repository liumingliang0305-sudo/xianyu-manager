import {
  CheckIcon,
  ChevronDownIcon,
  FishIcon,
  MessageSquareIcon,
  MoonIcon,
  PackageIcon,
  ShoppingCartIcon,
  SunIcon,
  UserIcon,
} from "lucide-react"
import { useTheme } from "next-themes"
import type { ComponentType, ReactNode } from "react"
import { NavLink, Outlet } from "react-router"

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
import { errorMessage } from "@/lib/api"
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

function AccountSwitcher() {
  const { accounts, account, setAccount } = useAccount()
  if (!account) return null
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button variant="ghost" size="sm" className="gap-1.5">
          <UserIcon />
          <span className="font-mono">{maskId(account)}</span>
          <ChevronDownIcon className="text-muted-foreground" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-60">
        <DropdownMenuLabel>闲鱼账号</DropdownMenuLabel>
        {accounts.map((a) => (
          <DropdownMenuItem key={a.id} onSelect={() => setAccount(a.id)}>
            <div className="flex flex-col">
              <span className="font-mono">{a.id}</span>
              <span className="text-xs text-muted-foreground">凭证更新于 {dateTime(a.saved_at)}</span>
            </div>
            {a.id === account && <CheckIcon className="ml-auto" />}
          </DropdownMenuItem>
        ))}
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

function Content() {
  const { account, isLoading, error } = useAccount()
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
  // 换账号时整页重新挂载，各页面的筛选和弹窗状态不会串到别的账号
  return <Outlet key={account} />
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
