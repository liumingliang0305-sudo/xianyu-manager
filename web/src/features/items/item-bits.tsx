import { ImageOffIcon } from "lucide-react"
import { useState } from "react"

import { StatusPill, type Tone } from "@/components/status-pill"
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
  const box = cn("shrink-0 overflow-hidden rounded-md bg-muted", className)
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
      width={size}
      height={size}
      loading="lazy"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
      className={cn(box, "object-cover")}
      style={{ width: size, height: size }}
    />
  )
}

const STATE_TONE: Record<ItemState, Tone> = {
  online: "positive",
  sold: "neutral",
  gone: "outline",
}

export function StateBadge({ state }: { state: ItemState }) {
  return <StatusPill tone={STATE_TONE[state]}>{STATE_LABEL[state]}</StatusPill>
}
