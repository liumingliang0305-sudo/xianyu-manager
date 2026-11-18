import { ImageIcon, ImageOffIcon } from "lucide-react"

import { cn } from "@/lib/utils"

import { useFeedbackImage } from "./queries"

/** 反馈里的一张截图，方形裁切。取的时候闪一下占位，取不到显示坏图标 */
export function FeedbackImage({
  feedbackId,
  sha,
  alt,
  className,
}: {
  feedbackId: number
  sha: string
  alt: string
  className?: string
}) {
  const image = useFeedbackImage(feedbackId, sha)
  const box = cn("size-20 shrink-0 overflow-hidden rounded-md border bg-muted", className)
  if (!image.data) {
    const Icon = image.isError ? ImageOffIcon : ImageIcon
    return (
      <div
        role="img"
        aria-label={image.isError ? `${alt}没取到` : alt}
        className={cn(
          box,
          "flex items-center justify-center text-muted-foreground",
          image.isPending && "animate-pulse",
        )}
      >
        {!image.isPending && <Icon className="size-4" aria-hidden />}
      </div>
    )
  }
  return <img src={image.data} alt={alt} className={cn(box, "object-cover")} />
}

/** 点开看原图：blob: 地址在新标签页打开 */
export function FeedbackImageLink({
  feedbackId,
  sha,
  index,
}: {
  feedbackId: number
  sha: string
  index: number
}) {
  const image = useFeedbackImage(feedbackId, sha)
  const thumb = <FeedbackImage feedbackId={feedbackId} sha={sha} alt={`截图 ${index}`} />
  if (!image.data) return thumb
  return (
    <a
      href={image.data}
      target="_blank"
      rel="noreferrer"
      title={`截图 ${index}，点开看原图`}
      className="block rounded-md outline-none transition-opacity hover:opacity-85 focus-visible:ring-3 focus-visible:ring-ring/50"
    >
      {thumb}
    </a>
  )
}
