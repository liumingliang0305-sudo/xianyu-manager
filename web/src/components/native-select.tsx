import type { ComponentProps } from "react"

import { cn } from "@/lib/utils"

/** 原生下拉框，样式和输入框一致；选项少、不需要搜索时用它 */
export function NativeSelect({ className, ...props }: ComponentProps<"select">) {
  return (
    <select
      className={cn(
        "h-8 rounded-lg border border-input bg-transparent px-2 text-sm transition-colors outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30",
        className,
      )}
      {...props}
    />
  )
}
