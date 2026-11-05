import { ImageOffIcon } from "lucide-react"
import { useState } from "react"

import { cn } from "@/lib/utils"

import { pddThumb } from "./pdd-format"

/** 拼多多商品的方形缩略图，取不到时显示一个占位 */
export function PddThumb({
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
      <div
        className={cn(box, "flex items-center justify-center text-muted-foreground")}
        style={{ width: size, height: size }}
      >
        <ImageOffIcon className="size-4" />
      </div>
    )
  }
  return (
    <img
      src={pddThumb(url, size * 2)}
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
