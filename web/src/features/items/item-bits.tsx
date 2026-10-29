import { ImageOffIcon } from "lucide-react"
import { useState } from "react"

import { Badge } from "@/components/ui/badge"
import { STATE_LABEL, thumb, type ItemState } from "@/lib/format"
import { cn } from "@/lib/utils"

export function ItemThumb({
  url,
  size = 48,
  className,
}: {
  url: string
  size?: number
  className?: string
}) {
  const [failed, setFailed] = useState(false)
  const box = cn("shrink-0 overflow-hidden rounded-lg bg-muted", className)
  if (!url || failed) {
    return (
      <div className={cn(box, "flex items-center justify-center text-muted-foreground")} style={{ width: size, height: size }}>
        <ImageOffIcon className="size-4" />
      </div>
    )
  }
  return (
    <img
      src={thumb(url, size * 2)}
      alt=""
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      className={cn(box, "object-cover")}
      style={{ width: size, height: size }}
    />
  )
}

const STATE_CLASS: Record<ItemState, string> = {
  online: "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  sold: "bg-secondary text-secondary-foreground",
  gone: "border-border bg-transparent text-muted-foreground",
}

export function StateBadge({ state }: { state: ItemState }) {
  return <Badge className={STATE_CLASS[state]}>{STATE_LABEL[state]}</Badge>
}
