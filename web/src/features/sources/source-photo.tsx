import { ImageOffIcon } from "lucide-react"
import { useEffect, useRef, useState } from "react"

import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

import { useSourceImage } from "./queries"

/** 按需取本地图片，离开页面时释放 blob URL；不向原站发送查看请求。 */
export function SourcePhoto({
  sourceId,
  sha,
  size = 48,
  full = false,
}: {
  sourceId: string
  sha: string | null
  size?: number
  full?: boolean
}) {
  const ref = useRef<HTMLDivElement>(null)
  const [visible, setVisible] = useState(false)
  const imageRef = useRef<HTMLImageElement>(null)
  const image = useSourceImage(sourceId, sha, visible)

  useEffect(() => {
    const element = ref.current
    if (!element) return
    const observer = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) {
          setVisible(true)
          observer.disconnect()
        }
      },
      { rootMargin: "200px" },
    )
    observer.observe(element)
    return () => observer.disconnect()
  }, [])

  useEffect(() => {
    if (!image.data || !imageRef.current) return
    const url = URL.createObjectURL(image.data)
    imageRef.current.src = url
    return () => URL.revokeObjectURL(url)
  }, [image.data])

  return (
    <div
      ref={ref}
      className={cn(
        "flex shrink-0 items-center justify-center overflow-hidden rounded-md bg-muted text-muted-foreground",
        full && "w-full",
      )}
      style={full ? { minHeight: 160 } : { width: size, height: size }}
    >
      {image.data && !image.isError ? (
        <img
          ref={imageRef}
          alt=""
          width={size}
          height={size}
          loading="lazy"
          className={full ? "max-h-[70vh] w-full object-contain" : "size-full object-cover"}
        />
      ) : sha && !image.isError ? (
        <Skeleton className="size-full" aria-label="图片加载中" />
      ) : (
        <ImageOffIcon className="size-4" aria-label="图片未下载" />
      )}
    </div>
  )
}
