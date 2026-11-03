import { useQueryClient } from "@tanstack/react-query"
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  ExternalLinkIcon,
  ImagePlusIcon,
  Loader2Icon,
  MapPinIcon,
  SaveIcon,
  SendIcon,
  SparklesIcon,
  Trash2Icon,
  Undo2Icon,
  XIcon,
} from "lucide-react"
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react"
import { toast } from "sonner"

import { Callout } from "@/components/callout"
import { EmptyNote, LoadError, RowsSkeleton, SearchInput } from "@/components/states"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import {
  api,
  ApiError,
  errorMessage,
  type Address,
  type AddressOption,
  type Draft,
  type DraftForm,
  type DraftProperty,
  type PostMode,
} from "@/lib/api"
import { dateTime, itemUrl, parseYuan, timeAgo, yuan } from "@/lib/format"
import { cn } from "@/lib/utils"

import { DraftImage } from "./draft-image"
import {
  putDraft,
  useAddImage,
  useAddresses,
  useChooseLabel,
  useDeleteDraft,
  usePlaces,
  usePublish,
  useRecommend,
  useRemoveImage,
  useSaveDraft,
} from "./queries"

const MAX_IMAGES = 9
const MAX_DESC = 1500
/** 停止输入多久以后自动保存 */
const SAVE_DELAY_MS = 800
const ACCEPT = "image/jpeg,image/png,image/webp,image/gif,image/bmp"
const EMOJI = /\p{Extended_Pictographic}/u

const POST_OPTIONS: { value: PostMode; label: string }[] = [
  { value: "free", label: "包邮" },
  { value: "distance", label: "按距离计费" },
  { value: "fixed", label: "一口价" },
  { value: "none", label: "无需邮寄" },
]
/** 编辑的商品原来的邮费对不上上面四种时才有 */
const KEEP_POST = { value: "keep", label: "沿用原来的" } as const

interface FormState {
  description: string
  price: string
  origPrice: string
  postMode: PostMode
  postPrice: string
  takeSelf: boolean
  address: Address | null
}

function centText(cents: number | null): string {
  return cents == null ? "" : String(cents / 100)
}

function fromDraft(draft: Draft): FormState {
  return {
    description: draft.description,
    price: centText(draft.price_cent),
    origPrice: centText(draft.orig_price_cent),
    postMode: draft.post_mode,
    postPrice: centText(draft.post_price_cent),
    takeSelf: draft.take_self,
    address: draft.address,
  }
}

/** 价格框里的错，没错是空对象。空着不算错（售价到发布时才必填） */
function priceErrors(form: FormState): Partial<Record<"price" | "origPrice" | "postPrice", string>> {
  const errors: Partial<Record<"price" | "origPrice" | "postPrice", string>> = {}
  const bad = (text: string) => text.trim() !== "" && parseYuan(text) == null
  if (bad(form.price)) errors.price = "写数字，最多两位小数"
  else if (form.price.trim() && parseYuan(form.price) === 0) errors.price = "售价要大于 0"
  if (bad(form.origPrice)) errors.origPrice = "写数字，最多两位小数"
  if (form.postMode === "fixed") {
    const post = parseYuan(form.postPrice)
    if (bad(form.postPrice)) errors.postPrice = "写数字，最多两位小数"
    else if (post != null && (post === 0 || post > 100000)) errors.postPrice = "要大于 0、不超过 1000 元"
  }
  return errors
}

function toPayload(form: FormState, images: string[]): DraftForm {
  const yuanOrNull = (text: string) => (text.trim() ? text.trim() : null)
  return {
    description: form.description,
    price: yuanOrNull(form.price),
    orig_price: yuanOrNull(form.origPrice),
    post_mode: form.postMode,
    post_price: form.postMode === "fixed" ? yuanOrNull(form.postPrice) : null,
    take_self: form.takeSelf,
    images,
    address: form.address,
  }
}

/** 发布前还差什么（本地能看出来的），空数组表示可以点发布 */
function missing(draft: Draft, form: FormState): string[] {
  const lacks: string[] = []
  if (draft.images.length === 0) lacks.push("图片")
  if (!form.description.trim()) lacks.push("描述")
  if (!form.price.trim()) lacks.push("售价")
  if (form.postMode === "fixed" && !form.postPrice.trim()) lacks.push("邮费")
  if (!draft.category) lacks.push("分类")
  if (!form.address) lacks.push("发货地")
  return lacks
}

/** 表单里的一块：左边名字，右边内容和提示 */
function Field({
  label,
  htmlFor,
  required,
  hint,
  hintId,
  children,
}: {
  label: string
  htmlFor?: string
  required?: boolean
  hint?: ReactNode
  hintId?: string
  children: ReactNode
}) {
  const name = (
    <>
      {label}
      {required && <span className="ml-0.5 text-muted-foreground" aria-hidden>*</span>}
    </>
  )
  return (
    <div className="grid grid-cols-[7rem_1fr] gap-x-6 gap-y-1.5 py-5 first:pt-0">
      {htmlFor ? (
        <Label htmlFor={htmlFor} className="items-start pt-1.5 text-sm font-medium">
          {name}
        </Label>
      ) : (
        <div className="pt-1.5 text-sm font-medium">{name}</div>
      )}
      <div className="flex min-w-0 flex-col gap-2">
        {children}
        {hint && (
          <p id={hintId} className="text-xs text-muted-foreground">
            {hint}
          </p>
        )}
      </div>
    </div>
  )
}

function FieldError({ id, children }: { id: string; children?: string }) {
  if (!children) return null
  return (
    <p id={id} role="alert" className="text-xs text-destructive">
      {children}
    </p>
  )
}

/** 一组互斥或多选的选项（分类、属性、邮费）。选中的是近黑实心 */
function Chip({
  selected,
  disabled,
  onClick,
  role,
  children,
}: {
  selected: boolean
  disabled?: boolean
  onClick: () => void
  role?: "radio"
  children: ReactNode
}) {
  return (
    <button
      type="button"
      role={role}
      aria-checked={role ? selected : undefined}
      aria-pressed={role ? undefined : selected}
      disabled={disabled}
      onClick={onClick}
      className={cn(
        "h-7 rounded-md border px-2.5 text-[13px] transition-colors outline-none focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50",
        selected
          ? "border-primary bg-primary text-primary-foreground"
          : "border-border hover:bg-muted",
      )}
    >
      {children}
    </button>
  )
}

function ImageTile({
  account,
  draftId,
  sha,
  url,
  index,
  total,
  busy,
  onMove,
  onRemove,
}: {
  account: string
  draftId: number
  sha: string
  url: string | null
  index: number
  total: number
  busy: boolean
  onMove: (to: number) => void
  onRemove: () => void
}) {
  return (
    <li className="group relative">
      <DraftImage account={account} draftId={draftId} sha={sha} url={url} size={96} className="border" />
      {index === 0 && (
        <span className="absolute bottom-1 left-1 rounded bg-black/60 px-1.5 py-0.5 text-[11px] font-medium text-white">
          首图
        </span>
      )}
      <div className="absolute top-1 right-1 flex gap-0.5 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100">
        {index > 0 && (
          <Button
            size="icon-xs"
            variant="secondary"
            disabled={busy}
            onClick={() => onMove(index - 1)}
            aria-label={`第 ${index + 1} 张往前移`}
          >
            <ArrowLeftIcon />
          </Button>
        )}
        {index < total - 1 && (
          <Button
            size="icon-xs"
            variant="secondary"
            disabled={busy}
            onClick={() => onMove(index + 1)}
            aria-label={`第 ${index + 1} 张往后移`}
          >
            <ArrowRightIcon />
          </Button>
        )}
        <Button
          size="icon-xs"
          variant="secondary"
          disabled={busy}
          onClick={onRemove}
          aria-label={`删掉第 ${index + 1} 张`}
        >
          <XIcon />
        </Button>
      </div>
    </li>
  )
}

function PropertyRow({
  property,
  busy,
  onChoose,
}: {
  property: DraftProperty
  busy: boolean
  onChoose: (keys: string[]) => void
}) {
  const toggle = (key: string) => {
    const on = property.selected.includes(key)
    if (!property.multiple) {
      if (!on) onChoose([key])
      return
    }
    const next = on ? property.selected.filter((k) => k !== key) : [...property.selected, key]
    if (next.length) onChoose(next)
  }
  return (
    <div className="grid grid-cols-[6.5rem_1fr] items-start gap-3">
      <span className="pt-1 text-[13px] text-muted-foreground">{property.name}</span>
      <div className="flex flex-wrap gap-1.5" role="group" aria-label={property.name}>
        {property.options.map((opt) => (
          <Chip
            key={opt.key}
            selected={property.selected.includes(opt.key)}
            disabled={busy}
            onClick={() => toggle(opt.key)}
          >
            {opt.name}
          </Chip>
        ))}
      </div>
    </div>
  )
}

/** 输入停下 ms 毫秒以后才变的值，搜索框用：不是每敲一个字都搜一次 */
function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value)
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms)
    return () => clearTimeout(timer)
  }, [value, ms])
  return settled
}

/** 省市区，直辖市不重复写 */
function region(address: Address): string {
  const parts = [address.prov, address.city, address.area]
  if (parts[0] === parts[1]) parts.splice(1, 1)
  return parts.filter(Boolean).join("")
}

function AddressList({
  title,
  options,
  onPick,
}: {
  title: string
  options: AddressOption[]
  onPick: (address: Address) => void
}) {
  return (
    <div>
      <h3 className="pb-1 text-xs text-muted-foreground">{title}</h3>
      <ul className="-mx-2">
        {options.map((o) => (
          <li key={o.key}>
            <button
              type="button"
              onClick={() => onPick(o.address)}
              className="w-full rounded-md px-2 py-2 text-left transition-colors outline-none hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              <span className="block text-sm font-medium">{o.name}</span>
              <span className="block text-xs text-muted-foreground">{o.detail}</span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

/** 选发货地：按名字搜（后台用高德搜），不搜的时候列闲鱼账号里的常用地址。和闲鱼网页端的"宝贝所在地"一样 */
function AddressDialog({
  account,
  onPick,
  onClose,
}: {
  account: string
  onPick: (address: Address) => void
  onClose: () => void
}) {
  const addresses = useAddresses(account)
  const [text, setText] = useState("")
  const keyword = useDebounced(text.trim(), 400)
  const places = usePlaces(keyword)
  const canSearch = addresses.data?.search ?? true
  const pick = (address: Address) => {
    onPick(address)
    onClose()
  }

  let body: ReactNode
  if (keyword) {
    if (places.isPending) body = <RowsSkeleton rows={3} height="h-10" />
    else if (places.isError)
      body = <LoadError what="搜索" error={places.error} onRetry={() => void places.refetch()} />
    else if (places.data.length === 0) body = <EmptyNote>没搜到「{keyword}」，换个说法试试</EmptyNote>
    else body = <AddressList title="搜索结果" options={places.data} onPick={pick} />
  } else if (addresses.isPending) body = <RowsSkeleton rows={3} height="h-10" />
  else if (addresses.isError)
    body = (
      <LoadError what="读取常用地址" error={addresses.error} onRetry={() => void addresses.refetch()} />
    )
  else if (addresses.data.addresses.length === 0)
    body = <EmptyNote>闲鱼账号里还没有常用地址，搜一个吧</EmptyNote>
  else body = <AddressList title="常用地址" options={addresses.data.addresses} onPick={pick} />

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>选发货地</DialogTitle>
          <DialogDescription>
            {canSearch
              ? "搜小区、商场、街道，或者从常用地址里选。发货地准一点，闲鱼会推给更多同城买家"
              : "后台没配高德 key，只能从常用地址里选"}
          </DialogDescription>
        </DialogHeader>
        {canSearch && (
          <SearchInput
            value={text}
            onChange={(e) => setText(e.target.value)}
            placeholder="搜地点，比如 光谷苑…"
            aria-label="搜发货地"
            autoFocus
          />
        )}
        <div className="-mx-6 max-h-[55vh] min-h-40 overflow-y-auto overscroll-contain px-6">{body}</div>
      </DialogContent>
    </Dialog>
  )
}

function AddressPicker({
  account,
  value,
  disabled,
  onChange,
}: {
  account: string
  value: Address | null
  disabled: boolean
  onChange: (address: Address) => void
}) {
  const [open, setOpen] = useState(false)
  return (
    <div className="flex items-center gap-4">
      {value ? (
        <div className="min-w-0">
          <div className="truncate text-sm font-medium">{value.poiName}</div>
          <div className="truncate text-xs text-muted-foreground">{region(value)}</div>
        </div>
      ) : (
        <span className="text-sm text-muted-foreground">还没选</span>
      )}
      <Button id="draft-address" variant="outline" size="sm" disabled={disabled} onClick={() => setOpen(true)}>
        <MapPinIcon />
        {value ? "换一个" : "选发货地"}
      </Button>
      {open && <AddressDialog account={account} onPick={onChange} onClose={() => setOpen(false)} />}
    </div>
  )
}

function DeleteDraftDialog({
  account,
  draft,
  onClose,
  onDeleted,
}: {
  account: string
  draft: Draft
  onClose: () => void
  onDeleted: () => void
}) {
  const remove = useDeleteDraft(account)
  const editing = draft.edit_item_id != null
  let description = "草稿和它的图片会从后台服务器上删掉，不能恢复。"
  if (editing) description = "暂存的改动和新加的图片都会删掉，闲鱼上的商品不受影响。下次编辑重新从闲鱼拉。"
  else if (draft.item_id) description = "只删本地的草稿记录和图片，已经发到闲鱼的商品不受影响。"
  return (
    <AlertDialog open onOpenChange={(open) => !open && !remove.isPending && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>{editing ? "放弃这些改动？" : "删除这个草稿？"}</AlertDialogTitle>
          <AlertDialogDescription>{description}</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={remove.isPending}>取消</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={remove.isPending}
            onClick={(e) => {
              e.preventDefault()
              remove.mutate(draft, {
                onSuccess: () => {
                  toast.success(editing ? "已放弃改动" : "草稿已删除")
                  onDeleted()
                },
                onError: (error) => toast.error(errorMessage(error)),
              })
            }}
          >
            {remove.isPending && <Loader2Icon className="animate-spin" />}
            {editing ? "放弃改动" : "删除"}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

/** 已经发布的草稿：只看，不能改 */
function PublishedDraft({
  account,
  draft,
  onDeleted,
}: {
  account: string
  draft: Draft
  onDeleted: () => void
}) {
  const [deleting, setDeleting] = useState(false)
  const rows: [string, ReactNode][] = [
    ["售价", yuan(draft.price_cent)],
    ["分类", draft.category ?? "-"],
    ["邮费", POST_OPTIONS.find((o) => o.value === draft.post_mode)?.label],
    ["发货地", draft.address?.poiName ?? "-"],
    ["发布时间", dateTime(draft.published_at)],
  ]
  return (
    <div className="flex flex-col gap-5 p-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="text-sm">
          已发布 · 商品 ID{" "}
          <span className="font-mono" translate="no">
            {draft.item_id}
          </span>
          {draft.relist_item_id && (
            <span className="text-muted-foreground">
              {" "}
              · 照{" "}
              <span className="font-mono" translate="no">
                {draft.relist_item_id}
              </span>{" "}
              重新上架
            </span>
          )}
        </p>
        <div className="flex gap-2">
          <Button variant="ghost" onClick={() => setDeleting(true)}>
            <Trash2Icon />
            删除草稿记录
          </Button>
          <Button variant="outline" asChild>
            <a href={itemUrl(draft.item_id!)} target="_blank" rel="noreferrer">
              在闲鱼上看
              <ExternalLinkIcon />
            </a>
          </Button>
        </div>
      </div>
      <ul className="flex flex-wrap gap-2">
        {draft.images.map((img) => (
          <li key={img.sha}>
            <DraftImage
              account={account}
              draftId={draft.id}
              sha={img.sha}
              url={img.url}
              size={96}
              className="border"
            />
          </li>
        ))}
      </ul>
      <p className="text-sm whitespace-pre-wrap">{draft.description}</p>
      <dl className="grid grid-cols-[5rem_1fr] gap-x-4 gap-y-2 text-sm">
        {rows.map(([name, value]) => (
          <div key={name} className="contents">
            <dt className="text-muted-foreground">{name}</dt>
            <dd className="tabular-nums">{value}</dd>
          </div>
        ))}
      </dl>
      {deleting && (
        <DeleteDraftDialog
          account={account}
          draft={draft}
          onClose={() => setDeleting(false)}
          onDeleted={onDeleted}
        />
      )}
    </div>
  )
}

/**
 * 一个草稿的表单。发新商品和编辑已有商品（draft.edit_item_id）用的是同一个表单，
 * 编辑时"发布"换成"保存到闲鱼"，"删除草稿"换成"放弃改动"。
 * `onPublished`：发布（提交改动）成功以后。
 */
export function DraftEditor({
  account,
  draft,
  onDeleted,
  onPublished,
}: {
  account: string
  draft: Draft
  onDeleted: () => void
  onPublished?: (draft: Draft) => void
}) {
  if (draft.item_id) return <PublishedDraft account={account} draft={draft} onDeleted={onDeleted} />
  return (
    <EditableDraft account={account} draft={draft} onDeleted={onDeleted} onPublished={onPublished} />
  )
}

/** 改价超过保护线时让用户再确认一次 */
function PriceGuardDialog({
  message,
  onConfirm,
  onClose,
}: {
  message: string
  onConfirm: () => void
  onClose: () => void
}) {
  return (
    <AlertDialog open onOpenChange={(open) => !open && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>价格改得有点多</AlertDialogTitle>
          <AlertDialogDescription>{message}。确认没输错再保存。</AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>回去改</AlertDialogCancel>
          <AlertDialogAction onClick={onConfirm}>确认保存</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}

/** 正在编辑的草稿。表单停手 0.8 秒自动保存；推荐分类、发布前先把没保存的存掉。 */
function EditableDraft({
  account,
  draft,
  onDeleted,
  onPublished,
}: {
  account: string
  draft: Draft
  onDeleted: () => void
  onPublished?: (draft: Draft) => void
}) {
  const client = useQueryClient()
  const [form, setForm] = useState(() => fromDraft(draft))
  const [dirty, setDirty] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [dragging, setDragging] = useState(false)
  const [guard, setGuard] = useState<string | null>(null)
  const fileInput = useRef<HTMLInputElement>(null)
  const editing = draft.edit_item_id != null
  const relisting = draft.relist_item_id != null

  const save = useSaveDraft(account, draft.id)
  const addImage = useAddImage(account, draft.id)
  const removeImage = useRemoveImage(account, draft.id)
  const recommend = useRecommend(account, draft.id)
  const choose = useChooseLabel(account, draft.id)
  const publish = usePublish(account, draft)

  const errors = priceErrors(form)
  const invalid = Object.keys(errors).length > 0
  const order = draft.images.map((img) => img.sha)

  // 事件和定时器里用的最新表单。切到别的草稿（组件卸掉）时还有没保存的，直接存
  const latest = useRef({ form, dirty, invalid, order })
  useLayoutEffect(() => {
    latest.current = { form, dirty, invalid, order }
  })
  useEffect(
    () => () => {
      const { form, dirty, invalid, order } = latest.current
      if (!dirty || invalid) return
      void api
        .updateDraft(account, draft.id, toPayload(form, order))
        .then((saved) => putDraft(client, account, saved))
    },
    [account, draft.id, client],
  )

  const saveDraft = save.mutateAsync
  const flush = useCallback(async () => {
    const { form, dirty, invalid, order } = latest.current
    if (!dirty) return
    if (invalid) throw new Error("价格格式不对，改好再继续")
    // 马上记下已经在存了，免得自动保存和点按钮同时存两遍
    latest.current.dirty = false
    setDirty(false)
    try {
      await saveDraft(toPayload(form, order))
    } catch (error) {
      setDirty(true)
      throw error
    }
  }, [saveDraft])

  // 表单一变就重新计时
  useEffect(() => {
    if (!dirty || invalid) return
    const timer = setTimeout(() => {
      flush().catch((error) => toast.error(`保存失败：${errorMessage(error)}`))
    }, SAVE_DELAY_MS)
    return () => clearTimeout(timer)
  }, [form, dirty, invalid, flush])

  const update = (patch: Partial<FormState>) => {
    setForm((f) => ({ ...f, ...patch }))
    setDirty(true)
  }

  const addFiles = async (files: File[]) => {
    const room = MAX_IMAGES - draft.images.length
    if (files.length > room) toast.error(`最多 ${MAX_IMAGES} 张，多出来的 ${files.length - room} 张没加`)
    for (const file of files.slice(0, Math.max(room, 0))) {
      try {
        await addImage.mutateAsync(file)
      } catch (error) {
        toast.error(`${file.name}：${errorMessage(error)}`)
      }
    }
  }

  const move = (from: number, to: number) => {
    if (invalid) {
      toast.error("价格格式不对，改好再调图片顺序")
      return
    }
    const next = [...order]
    const [sha] = next.splice(from, 1)
    next.splice(to, 0, sha)
    setDirty(false)
    save.mutate(toPayload(form, next), {
      onError: (error) => {
        setDirty(true)
        toast.error(errorMessage(error))
      },
    })
  }

  const runRecommend = async () => {
    try {
      await flush()
      await recommend.mutateAsync()
    } catch (error) {
      toast.error(errorMessage(error))
    }
  }

  const runPublish = async (force = false) => {
    setGuard(null)
    try {
      await flush()
      const done = await publish.mutateAsync(force)
      // 先把未保存标记清掉，跳走时卸载不会再存一遍
      latest.current.dirty = false
      toast.success(editing ? "已保存到闲鱼" : `已发布到闲鱼，商品 ID ${done.item_id}`)
      onPublished?.(done)
    } catch (error) {
      if (error instanceof ApiError && error.code === "price_guard") setGuard(error.message)
      else toast.error(`${editing ? "保存到闲鱼" : "发布"}失败：${errorMessage(error)}`)
    }
  }

  const lacks = missing(draft, form)
  const busy = publish.isPending
  const labelBusy = recommend.isPending || choose.isPending || busy
  const descEmoji = EMOJI.test(form.description)

  // 编辑时自动保存只是暂存在后台，点"保存到闲鱼"才改到闲鱼上，用词上分开
  const saveWord = editing ? "暂存" : "保存"
  let saveState: string
  if (save.isPending) saveState = `${saveWord}中…`
  else if (invalid) saveState = `价格格式不对，没${saveWord}`
  else if (dirty) saveState = `有改动，马上${saveWord}`
  else saveState = `已${saveWord} · ${timeAgo(draft.updated_at)}`
  const postOptions = draft.post_keep ? [...POST_OPTIONS, KEEP_POST] : POST_OPTIONS

  return (
    <div className="flex flex-col">
      {relisting && (
        <p className="mx-6 mt-6 rounded-lg bg-muted/60 px-3 py-2.5 text-[13px] leading-relaxed text-muted-foreground">
          照商品{" "}
          <span className="font-mono text-foreground" translate="no">
            {draft.relist_item_id}
          </span>{" "}
          抄的，用来重新上架。发布后是一个新商品：商品 ID 是新的，想要、浏览从零算，原来的商品不动。
          原来的图已经在闲鱼上，不用重传。
        </p>
      )}
      <div className="divide-y divide-rule px-6 pt-6">
        <Field
          label="图片"
          required
          hintId="draft-images-hint"
          hint={`1–9 张，第一张是首图，可以直接拖进来。${editing || relisting ? "新加的图" : "图片"}存在后台服务器上，点${editing ? "保存" : "发布"}时才传到闲鱼；图片里不能有二维码`}
        >
          <ul
            aria-describedby="draft-images-hint"
            className={cn(
              "flex flex-wrap gap-2 rounded-lg transition-colors",
              dragging && "bg-muted outline-2 outline-dashed outline-border",
            )}
            onDragOver={(e) => {
              if (!e.dataTransfer.types.includes("Files")) return
              e.preventDefault()
              setDragging(true)
            }}
            onDragLeave={() => setDragging(false)}
            onDrop={(e) => {
              e.preventDefault()
              setDragging(false)
              void addFiles([...e.dataTransfer.files].filter((f) => f.type.startsWith("image/")))
            }}
          >
            {draft.images.map((img, i) => (
              <ImageTile
                key={img.sha}
                account={account}
                draftId={draft.id}
                sha={img.sha}
                url={img.url}
                index={i}
                total={draft.images.length}
                busy={save.isPending || removeImage.isPending || busy}
                onMove={(to) => move(i, to)}
                onRemove={() =>
                  removeImage.mutate(img.sha, { onError: (e) => toast.error(errorMessage(e)) })
                }
              />
            ))}
            {draft.images.length < MAX_IMAGES && (
              <li>
                <button
                  type="button"
                  onClick={() => fileInput.current?.click()}
                  disabled={addImage.isPending || busy}
                  className="flex size-24 flex-col items-center justify-center gap-1 rounded-md border border-dashed border-input text-xs text-muted-foreground transition-colors outline-none hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
                >
                  {addImage.isPending ? (
                    <Loader2Icon className="size-5 animate-spin" />
                  ) : (
                    <ImagePlusIcon className="size-5" />
                  )}
                  {addImage.isPending ? "上传中…" : draft.images.length ? "加图片" : "加首图"}
                </button>
                <input
                  ref={fileInput}
                  type="file"
                  accept={ACCEPT}
                  multiple
                  hidden
                  onChange={(e) => {
                    const files = [...(e.target.files ?? [])]
                    e.target.value = ""
                    void addFiles(files)
                  }}
                />
              </li>
            )}
          </ul>
        </Field>

        <Field
          label="描述"
          htmlFor="draft-desc"
          required
          hintId="draft-desc-hint"
          hint="标题由闲鱼从描述里截。写清楚品牌型号、成色、来源，不能有 emoji"
        >
          <div className="relative">
            <Textarea
              id="draft-desc"
              value={form.description}
              maxLength={MAX_DESC}
              rows={6}
              disabled={busy}
              placeholder="描述一下宝贝的品牌型号、货品来源…"
              aria-describedby="draft-desc-hint"
              aria-invalid={descEmoji || undefined}
              onChange={(e) => update({ description: e.target.value })}
              className="min-h-36 pb-6"
            />
            <span className="pointer-events-none absolute right-2.5 bottom-1.5 text-xs text-muted-foreground tabular-nums">
              {form.description.length}/{MAX_DESC}
            </span>
          </div>
          {descEmoji && (
            <FieldError id="draft-desc-error">描述里有 emoji，网页端发布不让用，发布前删掉</FieldError>
          )}
        </Field>

        <Field
          label="分类和属性"
          required
          hint={
            draft.properties.length
              ? "闲鱼按描述推荐的，平台预先选了一项。改了哪一项，闲鱼会按新的选择刷新别的属性；改了描述可以重新推荐"
              : editing || relisting
                ? `${editing ? "商品现在" : "原来商品"}的分类和属性。想换的话点「重新推荐」，闲鱼按描述和现在的选择给出可选的`
                : "写好描述后点「推荐分类」，闲鱼按描述推荐分类和品牌、成色这些属性"
          }
        >
          <div className="flex items-center gap-3">
            <Button
              variant="outline"
              size="sm"
              disabled={!form.description.trim() || labelBusy || invalid}
              onClick={() => void runRecommend()}
            >
              {recommend.isPending ? <Loader2Icon className="animate-spin" /> : <SparklesIcon />}
              {draft.properties.length || editing || relisting ? "重新推荐" : "推荐分类"}
            </Button>
            {draft.category && (
              <span className="text-sm">
                分类：<span className="font-medium">{draft.category}</span>
              </span>
            )}
            {choose.isPending && <Loader2Icon className="size-4 animate-spin text-muted-foreground" />}
          </div>
          {draft.unsupported.length > 0 && (
            <Callout tone="warning" title={`这个分类网页端不支持${editing ? "编辑" : "发布"}`}>
              <p>
                要填{draft.unsupported.join("、")}。换个分类试试，或者去闲鱼 App {editing ? "改" : "发"}。
              </p>
            </Callout>
          )}
          {draft.properties.length === 0 && draft.labels.length > 0 && (
            <dl className="flex flex-col gap-2 rounded-lg border p-3 text-[13px]">
              {draft.labels.map((label) => (
                <div key={label.name} className="grid grid-cols-[6.5rem_1fr] gap-3">
                  <dt className="text-muted-foreground">{label.name}</dt>
                  <dd>{label.value}</dd>
                </div>
              ))}
            </dl>
          )}
          {draft.properties.length > 0 && (
            <div className="flex flex-col gap-3 rounded-lg border p-3">
              {draft.properties.map((p) => (
                <PropertyRow
                  key={p.id}
                  property={p}
                  busy={labelBusy}
                  onChoose={(keys) =>
                    choose.mutate(
                      { propertyId: p.id, keys },
                      { onError: (e) => toast.error(errorMessage(e)) },
                    )
                  }
                />
              ))}
            </div>
          )}
        </Field>

        <Field label="价格" required>
          <div className="flex flex-wrap gap-4">
            <div className="flex w-40 flex-col gap-1.5">
              <Label htmlFor="draft-price" className="text-xs font-normal text-muted-foreground">
                售价（元）
              </Label>
              <Input
                id="draft-price"
                inputMode="decimal"
                autoComplete="off"
                value={form.price}
                disabled={busy}
                placeholder="比如 49…"
                aria-invalid={!!errors.price || undefined}
                aria-describedby={errors.price ? "draft-price-error" : undefined}
                onChange={(e) => update({ price: e.target.value })}
                className="tabular-nums"
              />
              <FieldError id="draft-price-error">{errors.price}</FieldError>
            </div>
            <div className="flex w-40 flex-col gap-1.5">
              <Label htmlFor="draft-orig" className="text-xs font-normal text-muted-foreground">
                原价（元，选填）
              </Label>
              <Input
                id="draft-orig"
                inputMode="decimal"
                autoComplete="off"
                value={form.origPrice}
                disabled={busy}
                placeholder="买的时候多少钱…"
                aria-invalid={!!errors.origPrice || undefined}
                aria-describedby={errors.origPrice ? "draft-orig-error" : undefined}
                onChange={(e) => update({ origPrice: e.target.value })}
                className="tabular-nums"
              />
              <FieldError id="draft-orig-error">{errors.origPrice}</FieldError>
            </div>
          </div>
        </Field>

        <Field
          label="邮费"
          hint={
            draft.post_keep
              ? "商品原来用的是 App 里设的运费模板，网页端改不了模板；选「沿用原来的」就照原样"
              : undefined
          }
        >
          <div className="flex flex-wrap items-center gap-1.5" role="radiogroup" aria-label="邮费">
            {postOptions.map((o) => (
              <Chip
                key={o.value}
                role="radio"
                selected={form.postMode === o.value}
                disabled={busy}
                onClick={() => update({ postMode: o.value })}
              >
                {o.label}
              </Chip>
            ))}
            <div className="ml-4 flex items-center gap-2">
              <Switch
                id="draft-take-self"
                checked={form.takeSelf}
                disabled={busy}
                onCheckedChange={(takeSelf) => update({ takeSelf })}
              />
              <Label htmlFor="draft-take-self" className="font-normal">
                支持自提
              </Label>
            </div>
          </div>
          {form.postMode === "fixed" && (
            <div className="flex w-40 flex-col gap-1.5">
              <Label htmlFor="draft-post" className="text-xs font-normal text-muted-foreground">
                邮费（元）
              </Label>
              <Input
                id="draft-post"
                inputMode="decimal"
                autoComplete="off"
                value={form.postPrice}
                disabled={busy}
                placeholder="比如 8…"
                aria-invalid={!!errors.postPrice || undefined}
                aria-describedby={errors.postPrice ? "draft-post-error" : undefined}
                onChange={(e) => update({ postPrice: e.target.value })}
                className="tabular-nums"
              />
              <FieldError id="draft-post-error">{errors.postPrice}</FieldError>
            </div>
          )}
        </Field>

        <Field
          label="发货地"
          htmlFor="draft-address"
          required
          hint={
            editing || relisting
              ? "可以按名字搜，也可以从闲鱼账号的常用地址里选"
              : "可以按名字搜，也可以从闲鱼账号的常用地址里选。新草稿会沿用上一个草稿选的"
          }
        >
          <AddressPicker
            account={account}
            value={form.address}
            disabled={busy}
            onChange={(address) => update({ address })}
          />
        </Field>
      </div>

      <div className="flex flex-col gap-3 border-t px-6 py-4">
        {draft.error && (
          <Callout tone="negative" title={editing ? "上次保存到闲鱼失败" : "上次发布失败"}>
            <p>{draft.error}</p>
          </Callout>
        )}
        <div className="flex flex-wrap items-center gap-3">
          <span className="text-xs text-muted-foreground" aria-live="polite">
            {saveState}
          </span>
          <div className="flex-1" />
          {lacks.length > 0 && (
            <span className="text-xs text-muted-foreground">还差：{lacks.join("、")}</span>
          )}
          <Button variant="ghost" onClick={() => setDeleting(true)} disabled={busy}>
            {editing ? <Undo2Icon /> : <Trash2Icon />}
            {editing ? "放弃改动" : "删除草稿"}
          </Button>
          <Button
            onClick={() => void runPublish()}
            disabled={busy || lacks.length > 0 || invalid || draft.unsupported.length > 0}
          >
            {busy ? <Loader2Icon className="animate-spin" /> : editing ? <SaveIcon /> : <SendIcon />}
            {editing ? (busy ? "保存中…" : "保存到闲鱼") : busy ? "发布中…" : "发布到闲鱼"}
          </Button>
        </div>
      </div>

      {deleting && (
        <DeleteDraftDialog
          account={account}
          draft={draft}
          onClose={() => setDeleting(false)}
          onDeleted={() => {
            setDirty(false)
            latest.current.dirty = false
            onDeleted()
          }}
        />
      )}
      {guard && (
        <PriceGuardDialog
          message={guard}
          onConfirm={() => void runPublish(true)}
          onClose={() => setGuard(null)}
        />
      )}
    </div>
  )
}
