import { useEffect, useEffectEvent, useRef, useState } from "react"
import { toast } from "sonner"

import { api, errorMessage } from "@/lib/api"

// 和 app/feedback.py 的 MAX_IMAGES 一致
export const MAX_SCREENSHOTS = 6
// 和 app/images.py 一致：原图最大 20 MB，这几种格式
const MAX_BYTES = 20 * 1024 * 1024
export const ACCEPT = "image/jpeg,image/png,image/webp,image/gif,image/bmp"
const ACCEPTED = new Set(ACCEPT.split(","))

const MAC = /Mac|iPhone|iPad/.test(navigator.userAgent)
/** 粘贴的快捷键 */
export const PASTE_KEY = MAC ? "⌘V" : "Ctrl+V"
/**
 * 截图怎么进剪贴板。Mac 上默认的 ⌘⇧4 只把图存成桌面上的文件、不进剪贴板，这时粘贴什么都没有，
 * 只好存下来再选文件（GitHub #11）；加按 Control 才进剪贴板。
 */
export const CLIPBOARD_SHOT = MAC
  ? "截图时加按 Control（⌃⇧⌘4）才会进剪贴板，只按 ⌘⇧4 会存成文件"
  : "用 Win+Shift+S 截的图会进剪贴板"
const NO_IMAGE = `剪贴板里没有图片。${CLIPBOARD_SHOT}；存成文件的拖进来或点「选文件」`

/** 粘贴进来的图片：一般在 files 里，有的来源只放在 items 里 */
export function clipboardFiles(data: DataTransfer): File[] {
  const files = [...data.files]
  if (files.length) return files
  return [...data.items].flatMap((item) => {
    const file = item.kind === "file" ? item.getAsFile() : null
    return file ? [file] : []
  })
}

/** 按 ⌘V / Ctrl+V 粘贴时剪贴板里没有图片：在输入框里是正常粘贴文字，不打扰；别处说一声为什么没加上 */
export function pastedNothing(target: EventTarget) {
  if (target instanceof Element && target.closest("input, textarea, [contenteditable]")) return
  toast.error(NO_IMAGE)
}

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

  /** 点「粘贴」按钮：读剪贴板里的图片。第一次浏览器会问要不要允许读剪贴板 */
  const paste = async () => {
    let items: ClipboardItem[]
    try {
      if (!navigator.clipboard?.read) throw new Error("unsupported")
      items = await navigator.clipboard.read()
    } catch {
      toast.error(`浏览器没让读剪贴板，直接在这个窗口里按 ${PASTE_KEY} 粘贴也行`)
      return
    }
    const files: File[] = []
    for (const item of items) {
      const type = item.types.find((t) => t.startsWith("image/"))
      if (type) files.push(new File([await item.getType(type)], "截图", { type }))
    }
    if (files.length) add(files)
    else toast.error(NO_IMAGE)
  }

  return {
    shots,
    add,
    paste,
    remove,
    retry,
    uploading: shots.some((s) => s.status === "uploading"),
    failed: shots.some((s) => s.status === "failed"),
    shas: shots.flatMap((s) => (s.sha ? [s.sha] : [])),
  }
}

export type Screenshots = ReturnType<typeof useScreenshots>

/** 拖进来的是文件（不是选中的文字）才接 */
function hasFiles(event: DragEvent) {
  return !!event.dataTransfer?.types.includes("Files")
}

/**
 * 弹窗开着时，文件拖到窗口里任何地方（包括弹窗外面的灰色遮罩）都算加截图。
 * 不拦的话松手落在遮罩上，浏览器会直接打开那张图，写了一半的反馈就没了。
 * 返回是不是正拖着文件，用来高亮截图区。
 */
export function useFileDrop(onFiles: (files: File[]) => void, disabled: boolean) {
  const [dragging, setDragging] = useState(false)
  const over = useEffectEvent((event: DragEvent) => {
    if (!hasFiles(event)) return
    event.preventDefault()
    if (event.dataTransfer) event.dataTransfer.dropEffect = disabled ? "none" : "copy"
    setDragging(!disabled)
  })
  const drop = useEffectEvent((event: DragEvent) => {
    if (!hasFiles(event)) return
    event.preventDefault()
    setDragging(false)
    if (!disabled) onFiles([...(event.dataTransfer?.files ?? [])])
  })
  useEffect(() => {
    const onOver = (event: DragEvent) => over(event)
    const onDrop = (event: DragEvent) => drop(event)
    // 拖出窗口时 relatedTarget 是 null
    const onLeave = (event: DragEvent) => !event.relatedTarget && setDragging(false)
    window.addEventListener("dragover", onOver)
    window.addEventListener("dragleave", onLeave)
    window.addEventListener("drop", onDrop)
    return () => {
      window.removeEventListener("dragover", onOver)
      window.removeEventListener("dragleave", onLeave)
      window.removeEventListener("drop", onDrop)
    }
  }, [])
  return dragging
}
