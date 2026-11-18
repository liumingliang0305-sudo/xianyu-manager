import { useEffect, useRef, useState, type DragEvent } from "react"
import { toast } from "sonner"

import { api, errorMessage } from "@/lib/api"

// 和 app/feedback.py 的 MAX_IMAGES 一致
export const MAX_SCREENSHOTS = 6
// 和 app/images.py 一致：原图最大 20 MB，这几种格式
const MAX_BYTES = 20 * 1024 * 1024
export const ACCEPT = "image/jpeg,image/png,image/webp,image/gif,image/bmp"
const ACCEPTED = new Set(ACCEPT.split(","))

interface Shot {
  key: string
  name: string
  /** 本地预览：blob: 地址，删掉或关掉弹窗时释放 */
  preview: string
  file: File
  status: "uploading" | "done" | "failed"
  sha?: string
  error?: string
}

/**
 * 反馈里的截图：加进来马上传到后台（转成 JPEG 存着），提交反馈时只带 sha。
 * 传的时候、传失败的都不能提交。
 */
export function useScreenshots() {
  const [shots, setShots] = useState<Shot[]>([])
  const previews = useRef(new Set<string>())

  useEffect(() => {
    const urls = previews.current
    return () => urls.forEach((url) => URL.revokeObjectURL(url))
  }, [])

  const patch = (key: string, change: Partial<Shot>) =>
    setShots((list) => list.map((shot) => (shot.key === key ? { ...shot, ...change } : shot)))

  const upload = (key: string, file: File) => {
    patch(key, { status: "uploading", error: undefined })
    api.uploadFeedbackImage(file).then(
      (stored) => patch(key, { status: "done", sha: stored.sha }),
      (error) => patch(key, { status: "failed", error: errorMessage(error) }),
    )
  }

  const add = (files: File[]) => {
    const images = files.filter((file) => ACCEPTED.has(file.type))
    if (images.length < files.length) toast.error("只能加 JPG、PNG、WEBP、GIF、BMP 图片")
    const tooBig = images.filter((file) => file.size > MAX_BYTES)
    if (tooBig.length) toast.error(`图片最大 20 MB，有 ${tooBig.length} 张太大没加`)
    const fits = images.filter((file) => file.size <= MAX_BYTES)
    const room = MAX_SCREENSHOTS - shots.length
    if (fits.length > room) {
      toast.error(`截图最多 ${MAX_SCREENSHOTS} 张，多出来的 ${fits.length - room} 张没加`)
    }
    const added = fits.slice(0, Math.max(room, 0)).map((file): Shot => {
      const preview = URL.createObjectURL(file)
      previews.current.add(preview)
      return { key: crypto.randomUUID(), name: file.name, preview, file, status: "uploading" }
    })
    if (!added.length) return
    setShots((list) => [...list, ...added])
    for (const shot of added) upload(shot.key, shot.file)
  }

  const remove = (key: string) => {
    const shot = shots.find((s) => s.key === key)
    if (shot) {
      URL.revokeObjectURL(shot.preview)
      previews.current.delete(shot.preview)
    }
    setShots((list) => list.filter((s) => s.key !== key))
  }

  const retry = (key: string) => {
    const shot = shots.find((s) => s.key === key)
    if (shot) upload(shot.key, shot.file)
  }

  return {
    shots,
    add,
    remove,
    retry,
    uploading: shots.some((s) => s.status === "uploading"),
    failed: shots.some((s) => s.status === "failed"),
    shas: shots.flatMap((s) => (s.sha ? [s.sha] : [])),
  }
}

export type Screenshots = ReturnType<typeof useScreenshots>

/** 拖进来的是文件（不是选中的文字）才接 */
export function hasFiles(event: DragEvent) {
  return event.dataTransfer.types.includes("Files")
}
