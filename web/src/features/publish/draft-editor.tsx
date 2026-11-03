import { useQueryClient } from "@tanstack/react-query"
import {
  ArrowLeftIcon,
  ArrowRightIcon,
  ExternalLinkIcon,
  ImagePlusIcon,
  Loader2Icon,
  SendIcon,
  SparklesIcon,
  Trash2Icon,
  XIcon,
} from "lucide-react"
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react"
import { toast } from "sonner"

import { Callout } from "@/components/callout"
import { LoadError } from "@/components/states"
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
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import { Textarea } from "@/components/ui/textarea"
import {
  api,
  errorMessage,
  type Address,
  type Draft,
  type DraftForm,
  type DraftProperty,
  type PostMode,
} from "@/lib/api"
import { dateTime, itemUrl, parseYuan, timeAgo, yuan } from "@/lib/format"
import { cn } from "@/lib/utils"

import { DraftImage } from "./draft-image"
import {
  draftKeys,
  useAddImage,
  useAddresses,
  useChooseLabel,
  useDeleteDraft,
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
  index,
  total,
  busy,
  onMove,
  onRemove,
}: {
  account: string
  draftId: number
  sha: string
  index: number
  total: number
  busy: boolean
  onMove: (to: number) => void
  onRemove: () => void
}) {
  return (
    <li className="group relative">
      <DraftImage account={account} draftId={draftId} sha={sha} size={96} className="border" />
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

function AddressSelect({
  account,
  value,
  onChange,
}: {
  account: string
  value: Address | null
  onChange: (address: Address) => void
}) {
  const addresses = useAddresses(account)
  if (addresses.isError) {
    return (
      <LoadError
        what="读取闲鱼上的地址"
        error={addresses.error}
        onRetry={() => void addresses.refetch()}
        className="justify-start px-0 py-1"
      />
    )
  }
  const options = addresses.data ?? []
  const known = value && options.some((o) => o.key === value.poiId)
  return (
    <select
      id="draft-address"
      value={value?.poiId ?? ""}
      disabled={addresses.isPending}
      onChange={(e) => {
        const option = options.find((o) => o.key === e.target.value)
        if (option) onChange(option.address)
      }}
      className="h-8 w-full max-w-md rounded-lg border border-input bg-transparent px-2 text-sm outline-none focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50 dark:bg-input/30"
    >
      {!value && <option value="">{addresses.isPending ? "正在读闲鱼上的地址…" : "选择发货地…"}</option>}
      {value && !known && <option value={value.poiId}>{value.poiName}</option>}
      {options.map((o) => (
        <option key={o.key} value={o.key}>
          {o.name}（{o.detail}）
        </option>
      ))}
    </select>
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
  return (
    <AlertDialog open onOpenChange={(open) => !open && !remove.isPending && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>删除这个草稿？</AlertDialogTitle>
          <AlertDialogDescription>
            {draft.item_id
              ? "只删本地的草稿记录和图片，已经发到闲鱼的商品不受影响。"
              : "草稿和它的图片会从后台服务器上删掉，不能恢复。"}
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={remove.isPending}>取消</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={remove.isPending}
            onClick={(e) => {
              e.preventDefault()
              remove.mutate(draft.id, {
                onSuccess: () => {
                  toast.success("草稿已删除")
                  onDeleted()
                },
                onError: (error) => toast.error(errorMessage(error)),
              })
            }}
          >
            {remove.isPending && <Loader2Icon className="animate-spin" />}
            删除
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
            <DraftImage account={account} draftId={draft.id} sha={img.sha} size={96} className="border" />
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

export function DraftEditor({
  account,
  draft,
  onDeleted,
}: {
  account: string
  draft: Draft
  onDeleted: () => void
}) {
  if (draft.item_id) return <PublishedDraft account={account} draft={draft} onDeleted={onDeleted} />
  return <EditableDraft account={account} draft={draft} onDeleted={onDeleted} />
}

/** 正在编辑的草稿。表单停手 0.8 秒自动保存；推荐分类、发布前先把没保存的存掉。 */
function EditableDraft({
  account,
  draft,
  onDeleted,
}: {
  account: string
  draft: Draft
  onDeleted: () => void
}) {
  const client = useQueryClient()
  const [form, setForm] = useState(() => fromDraft(draft))
  const [dirty, setDirty] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const [dragging, setDragging] = useState(false)
  const fileInput = useRef<HTMLInputElement>(null)

  const save = useSaveDraft(account, draft.id)
  const addImage = useAddImage(account, draft.id)
  const removeImage = useRemoveImage(account, draft.id)
  const recommend = useRecommend(account, draft.id)
  const choose = useChooseLabel(account, draft.id)
  const publish = usePublish(account, draft.id)

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
      void api.updateDraft(account, draft.id, toPayload(form, order)).then((saved) =>
        client.setQueryData<Draft[]>(draftKeys.list(account), (old) =>
          old?.map((d) => (d.id === saved.id ? saved : d)),
        ),
      )
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

  const runPublish = async () => {
    try {
      await flush()
      const done = await publish.mutateAsync()
      toast.success(`已发布到闲鱼，商品 ID ${done.item_id}`)
    } catch (error) {
      toast.error(`发布失败：${errorMessage(error)}`)
    }
  }

  const lacks = missing(draft, form)
  const busy = publish.isPending
  const labelBusy = recommend.isPending || choose.isPending || busy
  const descEmoji = EMOJI.test(form.description)

  let saveState: string
  if (save.isPending) saveState = "保存中…"
  else if (invalid) saveState = "价格格式不对，没保存"
  else if (dirty) saveState = "有改动，马上保存"
  else saveState = `已保存 · ${timeAgo(draft.updated_at)}`

  return (
    <div className="flex flex-col">
      <div className="divide-y divide-rule px-6 pt-6">
        <Field
          label="图片"
          required
          hintId="draft-images-hint"
          hint="1–9 张，第一张是首图，可以直接拖进来。图片存在后台服务器上，点发布时才传到闲鱼；图片里不能有二维码"
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
              {draft.properties.length ? "重新推荐" : "推荐分类"}
            </Button>
            {draft.category && (
              <span className="text-sm">
                分类：<span className="font-medium">{draft.category}</span>
              </span>
            )}
            {choose.isPending && <Loader2Icon className="size-4 animate-spin text-muted-foreground" />}
          </div>
          {draft.unsupported.length > 0 && (
            <Callout tone="warning" title="这个分类网页端不支持发布">
              <p>要填{draft.unsupported.join("、")}。换个分类试试，或者去闲鱼 App 发。</p>
            </Callout>
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

        <Field label="邮费">
          <div className="flex flex-wrap items-center gap-1.5" role="radiogroup" aria-label="邮费">
            {POST_OPTIONS.map((o) => (
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
          hint="闲鱼账号里上次用的地址和常用地址。新草稿会沿用上一个草稿选的"
        >
          <AddressSelect account={account} value={form.address} onChange={(address) => update({ address })} />
        </Field>
      </div>

      <div className="flex flex-col gap-3 border-t px-6 py-4">
        {draft.error && (
          <Callout tone="negative" title="上次发布失败">
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
            <Trash2Icon />
            删除草稿
          </Button>
          <Button
            onClick={() => void runPublish()}
            disabled={busy || lacks.length > 0 || invalid || draft.unsupported.length > 0}
          >
            {busy ? <Loader2Icon className="animate-spin" /> : <SendIcon />}
            {busy ? "发布中…" : "发布到闲鱼"}
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
    </div>
  )
}
