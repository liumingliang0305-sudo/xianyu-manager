import { useMemo } from "react"

import { ImagePickerDialog } from "@/components/image-picker-dialog"
import { LoadError } from "@/components/states"

import { useSource } from "./queries"
import { SourcePhoto } from "./source-photo"

/**
 * 照第三方货源建草稿前挑图：最多 9 张、排好顺序。
 * 默认选好下载下来的前 9 张（以前直接建草稿带的就是这些），直接点「建草稿」和以前一样。
 */
export function SourceDraftPicker({
  sourceId,
  pending,
  onConfirm,
  onClose,
}: {
  sourceId: string
  pending: boolean
  onConfirm: (images: string[]) => void
  onClose: () => void
}) {
  const detail = useSource(sourceId)
  const source = detail.data
  const images = useMemo(
    () => source?.images.map((img, index) => ({ key: `${index}`, sha: img.sha })),
    [source],
  )
  return (
    <ImagePickerDialog
      title="挑草稿用的图"
      description="草稿建在当前账号上：描述是货源的标题和正文，图按这里的顺序带过去，价格到发布页填。"
      images={images}
      error={
        detail.isError ? (
          <LoadError what="读取货源" error={detail.error} onRetry={() => void detail.refetch()} />
        ) : undefined
      }
      renderImage={(img, size) => <SourcePhoto sourceId={sourceId} sha={img.sha} size={size} />}
      missingHint="可以先关掉这里，在货源详情里点「重新下载」。"
      confirmLabel="建草稿"
      pending={pending}
      onConfirm={onConfirm}
      onClose={onClose}
    />
  )
}
