import { ImageIcon, ImageOffIcon } from "lucide-react"

import { ItemThumb } from "@/features/items/item-bits"
import { cn } from "@/lib/utils"

import { useDraftImage } from "./queries"

/**
 * 草稿里的图。已经在闲鱼上的（传过的，或者编辑的商品原有的）直接用闲鱼的缩略图；
 * 还没传的存在后台服务器上，要带访问令牌取。没有图时显示占位。
 */
export function DraftImage({
  account,
  draftId,
  sha,
  url,
  size,
  className,
}: {
  account: string
  draftId: number
  sha: string | undefined
  url?: string | null
  size: number
  className?: string
}) {
  const image = useDraftImage(account, draftId, url ? undefined : sha)
  if (url) return <ItemThumb url={url} size={size} className={className} />
  const src = sha ? image.data : undefined
  const box = cn("shrink-0 overflow-hidden rounded-md bg-muted", className)
  if (!sha || image.isError || !src) {
    const Icon = sha && image.isError ? ImageOffIcon : ImageIcon
    return (
      <div
        className={cn(box, "flex items-center justify-center text-muted-foreground", image.isPending && sha && "animate-pulse")}
        style={{ width: size, height: size }}
      >
        {!(sha && image.isPending) && <Icon className="size-4" />}
      </div>
    )
  }
  return (
    <img
      src={src}
      alt=""
      width={size}
      height={size}
      className={cn(box, "object-cover")}
      style={{ width: size, height: size }}
    />
  )
}
