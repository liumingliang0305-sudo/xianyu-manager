import { FishIcon } from "lucide-react"

import { cn } from "@/lib/utils"

/** 标志和"闲鱼管理"。窄屏顶栏里 compact，只留标志 */
export function Brand({ compact = false }: { compact?: boolean }) {
  return (
    <div className="flex items-center gap-2 text-[15px] font-semibold">
      <span className="flex size-7 items-center justify-center rounded-lg bg-brand text-brand-foreground">
        <FishIcon className="size-4" />
      </span>
      <span className={cn(compact && "sr-only sm:not-sr-only")}>闲鱼管理</span>
    </div>
  )
}
