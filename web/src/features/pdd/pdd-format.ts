import { yuan } from "@/lib/format"

/** 拼多多 CDN 的缩略图：原图地址后面加 imageMogr2 参数，96px 的缩略图只有原图的十几分之一大 */
export function pddThumb(url: string, size: number): string {
  return `${url}?imageMogr2/thumbnail/${size}x${size}`
}

/** 价格区间："¥298–668"，最低最高一样时只写一个 */
export function priceRange(min: number | null, max: number | null): string {
  if (min == null) return yuan(max)
  if (max == null || max === min) return yuan(min)
  return `${yuan(min)}–${yuan(max).replace("¥", "")}`
}
