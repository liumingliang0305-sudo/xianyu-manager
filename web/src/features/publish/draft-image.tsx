import { ImageIcon, ImageOffIcon } from "lucide-react"

import { cn } from "@/lib/utils"

import { useDraftImage } from "./queries"

/** 草稿里的图（存在后台服务器上，要带访问令牌取）。没有图时显示占位。 */
export function DraftImage({
  account,
  draftId,
  sha,
  size,
  className,
}: {
  account: string
  draftId: number
  sha: string | undefined
  size: number
  className?: string
}) {
  const image = useDraftImage(account, draftId, sha)
  const url = sha ? image.data : undefined
  const box = cn("shrink-0 overflow-hidden rounded-md bg-muted", className)
  if (!sha || image.isError || !url) {
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
      src={url}
      alt=""
      width={size}
      height={size}
      className={cn(box, "object-cover")}
      style={{ width: size, height: size }}
    />
  )
}
