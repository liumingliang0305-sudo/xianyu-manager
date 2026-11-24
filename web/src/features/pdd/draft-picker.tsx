import { useMemo } from "react"

import {
  ImagePickerDialog,
  MAX_DRAFT_IMAGES,
  type PickableImage,
} from "@/components/image-picker-dialog"
import { LoadError } from "@/components/states"
import type { PddGoods } from "@/lib/api"

import { PddThumb } from "./pdd-bits"
import { usePddGoodsDetail } from "./queries"

interface PddPick extends PickableImage {
  url: string
}

/** 货源里能挑的图：主图、详情图、规格图，同一个地址只出现一次 */
function pickable(goods: PddGoods): PddPick[] {
  const seen = new Set<string>()
  const picks: PddPick[] = []
  const add = (url: string, sha: string | null, group: string, label?: string) => {
    if (!url || seen.has(url)) return
    seen.add(url)
    picks.push({ key: url, url, sha, group, label })
  }
  goods.images.forEach((img) => add(img.url, img.sha, "主图"))
  goods.detail_images.forEach((img) => add(img.url, img.sha, "详情图"))
  goods.skus.forEach((sku) => add(sku.thumb, sku.thumb_sha, "规格图", sku.name || undefined))
  return picks
}

/**
 * 照拼多多货源建草稿前挑图：主图、详情图、规格图都能挑，最多 9 张、排好顺序。
 * 默认选好下载下来的主图（以前直接建草稿带的就是这些），直接点「建草稿」和以前一样。
 */
export function PddDraftPicker({
  goodsId,
  accountName,
  pending,
  onConfirm,
  onClose,
}: {
  goodsId: string
  /** 草稿建在哪个账号上，写在说明里 */
  accountName: string
  pending: boolean
  onConfirm: (images: string[]) => void
  onClose: () => void
}) {
  const detail = usePddGoodsDetail(goodsId)
  const goods = detail.data
  const images = useMemo(() => (goods ? pickable(goods) : undefined), [goods])
  const initial = useMemo(
    () => goods?.images.flatMap((img) => (img.sha ? [img.sha] : [])).slice(0, MAX_DRAFT_IMAGES),
    [goods],
  )
  return (
    <ImagePickerDialog
      title="挑草稿用的图"
      description={`草稿建在「${accountName}」上：描述是标题、参数和可选规格，图按这里的顺序带过去，价格到发布页填。`}
      images={images}
      error={
        detail.isError ? (
          <LoadError what="读取货源" error={detail.error} onRetry={() => void detail.refetch()} />
        ) : undefined
      }
      renderImage={(img, size) => <PddThumb url={img.url} size={size} />}
      initial={initial}
      missingHint="可以先关掉这里，在货源里点「重新下载图片」。"
      confirmLabel="建草稿"
      pending={pending}
      onConfirm={onConfirm}
      onClose={onClose}
    />
  )
}
