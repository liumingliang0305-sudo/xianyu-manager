import { ArrowLeftIcon, ArrowRightIcon, Loader2Icon, XIcon } from "lucide-react"
import { useMemo, useState, type ReactNode } from "react"

import { EmptyNote } from "@/components/states"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

/** 闲鱼一个商品最多 9 张图（和 xianyu_core/publish.py 的 MAX_IMAGES 一致） */
export const MAX_DRAFT_IMAGES = 9

// 下面能挑的图、上面挑好的图的边长（px）。窄窗口里一行也放得下三张
const TILE = 96
const PICKED = 72

/** 能挑的一张图 */
export interface PickableImage {
  /** 列表里的唯一 key（没下载的图 sha 是 null，靠它区分） */
  key: string
  /** 后台存的图片编号；还没下载下来是 null，显示出来但不能选 */
  sha: string | null
  /** 分组名（「主图」「详情图」），同组的放在一起，组按第一次出现的顺序排；不分组就不传 */
  group?: string
  /** 这张图的名字，读屏和鼠标停上去时用；不传是「组名 第几张」 */
  label?: string
}

export interface ImagePickerDialogProps<T extends PickableImage> {
  title: ReactNode
  description?: ReactNode
  /** 能挑的图，同一个 sha 只显示第一次出现的那张；还在读是 undefined（显示占位） */
  images: T[] | undefined
  /** 读图片列表出错时显示的内容（一般是 LoadError），有它时不显示图片 */
  error?: ReactNode
  /** 画一张方形缩略图，边长 size（px）。各处的图取法不一样（拼多多 CDN、带令牌读后台），由用的地方给 */
  renderImage: (image: T, size: number) => ReactNode
  /** 默认选中的 sha，按顺序；不传是能选的前 max 张 */
  initial?: string[]
  /** 最多选几张，默认 9 */
  max?: number
  /** 有没下载下来的图时，图片上面的一句说明（比如去哪里补下载） */
  missingHint?: ReactNode
  /** 确定按钮的字，比如「建草稿」 */
  confirmLabel: string
  /** 请求进行中按钮上的字，默认是 confirmLabel 加「中…」 */
  pendingLabel?: string
  /** 点了确定、请求还在跑：按钮转圈，不能再改、不能关 */
  pending?: boolean
  /** 点确定：挑好的 sha，按顺序，至少一张 */
  onConfirm: (shas: string[]) => void
  onClose: () => void
}

/**
 * 挑图弹窗：从一组已经存在后台的图里挑几张、排好顺序（第一张是首图）。
 * 上面一排是挑好的（带序号，箭头调顺序、× 去掉），下面是全部的图（按组分开，点一下选上或去掉，
 * 选上的角上标第几张）。满了再点会说明为什么选不上；没下载下来的图显示出来但不能选。
 */
export function ImagePickerDialog<T extends PickableImage>(props: ImagePickerDialogProps<T>) {
  const { title, description, images, error, pending = false, onClose } = props
  let body
  if (error)
    body = (
      <Placeholder confirmLabel={props.confirmLabel} onClose={onClose}>
        {error}
      </Placeholder>
    )
  else if (!images)
    body = (
      <Placeholder confirmLabel={props.confirmLabel} onClose={onClose}>
        <div className="flex flex-wrap gap-2" aria-label="图片读取中">
          {Array.from({ length: 8 }, (_, i) => (
            <Skeleton key={i} style={{ width: TILE, height: TILE }} />
          ))}
        </div>
      </Placeholder>
    )
  else body = <Picker {...props} images={images} pending={pending} />
  return (
    <Dialog open onOpenChange={(open) => !open && !pending && onClose()}>
      <DialogContent
        className="flex flex-col sm:max-w-3xl"
        showCloseButton={!pending}
        // 点到弹窗外面不关，免得挑了半天的图丢了
        onInteractOutside={(event) => event.preventDefault()}
      >
        <DialogHeader className="pr-6">
          <DialogTitle>{title}</DialogTitle>
          <DialogDescription className={description ? undefined : "sr-only"}>
            {description ?? `最多选 ${props.max ?? MAX_DRAFT_IMAGES} 张，第一张是首图`}
          </DialogDescription>
        </DialogHeader>
        {body}
      </DialogContent>
    </Dialog>
  )
}

function Placeholder({
  confirmLabel,
  onClose,
  children,
}: {
  confirmLabel: string
  onClose: () => void
  children: ReactNode
}) {
  return (
    <>
      <div className="min-h-0 flex-1 overflow-y-auto overscroll-contain">{children}</div>
      <DialogFooter>
        <Button variant="outline" onClick={onClose}>
          取消
        </Button>
        <Button disabled>{confirmLabel}</Button>
      </DialogFooter>
    </>
  )
}

function Picker<T extends PickableImage>({
  images,
  renderImage,
  initial,
  max = MAX_DRAFT_IMAGES,
  missingHint,
  confirmLabel,
  pendingLabel,
  pending,
  onConfirm,
  onClose,
}: ImagePickerDialogProps<T> & { images: T[]; pending: boolean }) {
  // 同一张图（sha 一样）只留第一次出现的那张
  const list = useMemo(() => {
    const seen = new Set<string>()
    return images.filter((img) => {
      if (!img.sha) return true
      if (seen.has(img.sha)) return false
      seen.add(img.sha)
      return true
    })
  }, [images])
  const bySha = useMemo(
    () => new Map(list.flatMap((img) => (img.sha ? [[img.sha, img] as const] : []))),
    [list],
  )
  const groups = useMemo(() => {
    const byGroup = new Map<string, T[]>()
    for (const img of list) {
      const group = img.group ?? ""
      const items = byGroup.get(group)
      if (items) items.push(img)
      else byGroup.set(group, [img])
    }
    return [...byGroup]
  }, [list])

  const [order, setOrder] = useState(() =>
    [...new Set(initial ?? [...bySha.keys()])].filter((sha) => bySha.has(sha)).slice(0, max),
  )
  // 列表刷新后没了的图不算
  const chosen = order.filter((sha) => bySha.has(sha))
  const [notice, setNotice] = useState("")
  const full = chosen.length >= max
  const missing = list.length - bySha.size

  const toggle = (sha: string) => {
    if (chosen.includes(sha)) {
      setOrder(chosen.filter((s) => s !== sha))
      setNotice("")
    } else if (full) {
      setNotice(`最多选 ${max} 张，先在上面去掉一张再选`)
    } else {
      setOrder([...chosen, sha])
      setNotice("")
    }
  }
  const move = (from: number, to: number) => {
    const next = [...chosen]
    ;[next[from], next[to]] = [next[to], next[from]]
    setOrder(next)
  }

  return (
    <>
      <section aria-labelledby="picked-images" className="grid gap-2">
        <div className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1">
          <h3 id="picked-images" className="text-sm font-medium">
            已选 <span className="tabular-nums">{chosen.length} / {max}</span>
          </h3>
          <p
            aria-live="polite"
            className={cn("text-xs", notice ? "text-warning-ink" : "text-muted-foreground")}
          >
            {notice || "第一张是首图，用箭头调顺序"}
          </p>
        </div>
        {chosen.length > 0 ? (
          <ol className="flex gap-2 overflow-x-auto overscroll-contain pb-1">
            {chosen.map((sha, i) => {
              const img = bySha.get(sha)!
              return (
                <li key={sha} className="flex shrink-0 flex-col gap-1" style={{ width: PICKED }}>
                  <span className="relative block">
                    {renderImage(img, PICKED)}
                    <span className="absolute top-1 left-1 rounded bg-black/60 px-1.5 py-0.5 text-[11px] font-medium text-white tabular-nums">
                      {i === 0 ? "首图" : i + 1}
                    </span>
                  </span>
                  <span className="flex justify-between">
                    <Button
                      size="icon-xs"
                      variant="ghost"
                      disabled={pending || i === 0}
                      onClick={() => move(i, i - 1)}
                      aria-label={`第 ${i + 1} 张往前移`}
                    >
                      <ArrowLeftIcon />
                    </Button>
                    <Button
                      size="icon-xs"
                      variant="ghost"
                      disabled={pending}
                      onClick={() => toggle(sha)}
                      aria-label={`去掉第 ${i + 1} 张`}
                    >
                      <XIcon />
                    </Button>
                    <Button
                      size="icon-xs"
                      variant="ghost"
                      disabled={pending || i === chosen.length - 1}
                      onClick={() => move(i, i + 1)}
                      aria-label={`第 ${i + 1} 张往后移`}
                    >
                      <ArrowRightIcon />
                    </Button>
                  </span>
                </li>
              )
            })}
          </ol>
        ) : (
          <EmptyNote>还没选图片，在下面点图片选上，至少一张。</EmptyNote>
        )}
      </section>

      <div className="-mx-4 grid min-h-0 flex-1 gap-4 overflow-y-auto overscroll-contain border-t px-4 pt-4">
        {missing > 0 && (
          <p className="text-xs text-muted-foreground">
            有 {missing} 张没下载下来，不能选。{missingHint}
          </p>
        )}
        {list.length === 0 && <EmptyNote>没有图片可选。</EmptyNote>}
        {groups.map(([group, items]) => (
          <fieldset key={group} disabled={pending} className="grid gap-2">
            {group && (
              <legend className="mb-2 text-xs font-medium text-muted-foreground">
                {group} <span className="tabular-nums">{items.length} 张</span>
              </legend>
            )}
            <ul className="flex flex-wrap gap-2">
              {items.map((img, index) => {
                const at = img.sha ? chosen.indexOf(img.sha) : -1
                const on = at >= 0
                const name = img.label || `${group || "图片"} ${index + 1}`
                return (
                  <li key={img.key}>
                    <label
                      title={name}
                      className={cn(
                        "relative block cursor-pointer rounded-md",
                        (!img.sha || pending) && "cursor-default",
                      )}
                    >
                      <input
                        type="checkbox"
                        className="peer sr-only"
                        checked={on}
                        disabled={!img.sha}
                        onChange={() => img.sha && toggle(img.sha)}
                        aria-label={`${name}${on ? `，已选，第 ${at + 1} 张` : ""}${img.sha ? "" : "，没下载下来，不能选"}`}
                      />
                      <span
                        className={cn(
                          "block rounded-md ring-offset-2 ring-offset-background transition-[opacity,box-shadow]",
                          "peer-focus-visible:ring-3 peer-focus-visible:ring-ring/50",
                          on && "ring-2 ring-primary peer-focus-visible:ring-primary",
                          !img.sha && "opacity-40",
                          img.sha && !on && full && "opacity-60",
                        )}
                      >
                        {renderImage(img, TILE)}
                      </span>
                      <span
                        aria-hidden
                        className={cn(
                          "absolute top-1 left-1 flex size-5 items-center justify-center rounded border bg-background text-[11px] font-medium tabular-nums shadow-xs",
                          on && "border-primary bg-primary text-primary-foreground",
                        )}
                      >
                        {on ? at + 1 : ""}
                      </span>
                      {!img.sha && (
                        <span className="absolute inset-x-0 bottom-0 rounded-b-md bg-black/60 py-0.5 text-center text-[11px] text-white">
                          没下载
                        </span>
                      )}
                    </label>
                  </li>
                )
              })}
            </ul>
          </fieldset>
        ))}
      </div>

      <DialogFooter>
        <Button variant="outline" onClick={onClose} disabled={pending}>
          取消
        </Button>
        <Button onClick={() => onConfirm(chosen)} disabled={pending || chosen.length === 0}>
          {pending && <Loader2Icon className="animate-spin" aria-hidden />}
          {pending ? (pendingLabel ?? `${confirmLabel}中…`) : confirmLabel}
        </Button>
      </DialogFooter>
    </>
  )
}
