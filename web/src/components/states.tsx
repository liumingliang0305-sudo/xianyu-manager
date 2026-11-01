import { RefreshCwIcon, SearchIcon } from "lucide-react"
import type { ComponentProps, ReactNode } from "react"

import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Skeleton } from "@/components/ui/skeleton"
import { errorMessage } from "@/lib/api"
import { cn } from "@/lib/utils"

/** 整块没有数据时（第一次用、筛选后没有结果）：一句话说清楚，能做的下一步放在下面。不放插画。 */
export function EmptyState({
  title,
  action,
  className,
  children,
}: {
  title?: string
  action?: ReactNode
  className?: string
  children?: ReactNode
}) {
  return (
    <div className={cn("flex flex-col items-center gap-1 px-6 py-12 text-center", className)}>
      {title && <p className="font-medium">{title}</p>}
      {children && <div className="max-w-sm text-sm text-muted-foreground">{children}</div>}
      {action && <div className="mt-3">{action}</div>}
    </div>
  )
}

/** 一小块还没有内容（比如改动记录）：保留这块的位置，虚线框里一句话。 */
export function EmptyNote({ children }: { children: ReactNode }) {
  return (
    <p className="rounded-lg border border-dashed px-4 py-2.5 text-sm text-muted-foreground">
      {children}
    </p>
  )
}

/** 加载失败：出现在失败的那一块里，说哪里失败了，带一个重试。 */
export function LoadError({
  what,
  error,
  onRetry,
  className,
}: {
  what: string
  error: unknown
  onRetry?: () => void
  className?: string
}) {
  return (
    <div
      role="alert"
      className={cn(
        "flex flex-wrap items-center justify-center gap-x-3 gap-y-1 px-6 py-10 text-center text-sm",
        className,
      )}
    >
      <span>
        <span className="text-destructive">{what}失败</span>
        <span className="text-muted-foreground">：{errorMessage(error)}</span>
      </span>
      {onRetry && (
        <Button variant="ghost" size="sm" onClick={onRetry}>
          <RefreshCwIcon />
          重试
        </Button>
      )}
    </div>
  )
}

/** 列表加载中：按真实行高画几条灰条，数据到了布局不跳 */
export function RowsSkeleton({ rows = 4, height = "h-12" }: { rows?: number; height?: string }) {
  return (
    <div className="flex flex-col gap-3 p-4">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className={cn("w-full", height)} />
      ))}
    </div>
  )
}

/** 列表上方的搜索框 */
export function SearchInput({ className, ...props }: ComponentProps<typeof Input>) {
  return (
    <div className={cn("relative", className)}>
      <SearchIcon className="pointer-events-none absolute top-1/2 left-2.5 size-4 -translate-y-1/2 text-muted-foreground" />
      <Input type="search" autoComplete="off" spellCheck={false} className="pl-8" {...props} />
    </div>
  )
}
