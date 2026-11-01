import { Tabs as TabsPrimitive } from "radix-ui"

import { cn } from "@/lib/utils"

export interface CountOption<T extends string> {
  value: T
  label: string
  count: number | null
}

/**
 * 列表上面的一排计数卡片，同时是列表的筛选：点哪张，下面就只显示哪一类。
 * 选中的那张填闲鱼黄。卡片平分整行；768px 以下变成小标签，放不下就换行（不藏在横向滚动里）。键盘左右键切换（用的是 Tabs 的语义）。
 * `count` 是 null 时还在加载，显示一个灰条。
 */
export function CountFilter<T extends string>({
  label,
  value,
  options,
  onChange,
  className,
}: {
  /** 给读屏用的名字，比如"商品状态" */
  label: string
  value: T
  options: CountOption<T>[]
  onChange: (value: T) => void
  className?: string
}) {
  return (
    <TabsPrimitive.Root value={value} onValueChange={(v) => onChange(v as T)} className={className}>
      <TabsPrimitive.List
        aria-label={label}
        className="flex flex-wrap gap-2 md:flex-nowrap"
      >
        {options.map((option) => (
          <TabsPrimitive.Trigger
            key={option.value}
            value={option.value}
            className={cn(
              "group flex items-center gap-1.5 rounded-full border bg-card px-3 py-1.5 text-left text-sm transition-colors outline-none select-none",
              "md:min-w-0 md:flex-1 md:flex-col md:items-start md:gap-0.5 md:rounded-xl md:px-3 md:py-2.5",
              "hover:border-input focus-visible:ring-3 focus-visible:ring-ring/50",
              "data-[state=active]:border-brand data-[state=active]:bg-brand data-[state=active]:text-brand-foreground",
            )}
          >
            <span className="text-muted-foreground group-data-[state=active]:text-brand-foreground md:max-w-full md:truncate md:text-[13px] md:leading-5">
              {option.label}
            </span>
            {option.count == null ? (
              <span className="h-5 w-6 rounded-sm bg-muted md:h-6" aria-label="加载中" />
            ) : (
              <span className="font-semibold tabular-nums md:text-base md:leading-6">
                {option.count.toLocaleString("zh-CN")}
              </span>
            )}
          </TabsPrimitive.Trigger>
        ))}
      </TabsPrimitive.List>
    </TabsPrimitive.Root>
  )
}
