import type { Item } from "@/lib/api"

/** 35800 → "¥358"，1050 → "¥10.5" */
export function yuan(cents: number | null | undefined): string {
  if (cents == null) return "-"
  const text = (cents / 100).toFixed(2).replace(/\.?0+$/, "")
  return `¥${text}`
}

/** 用户输入的元（字符串）→ 分；不合法返回 null。和后端 yuan_to_cent 规则一致：最多两位小数。 */
export function parseYuan(input: string): number | null {
  const text = input.trim()
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return null
  const [whole, frac = ""] = text.split(".")
  return Number(whole) * 100 + Number(frac.padEnd(2, "0"))
}

export function count(value: number | null | undefined): string {
  return value == null ? "-" : value.toLocaleString("zh-CN")
}

const relative = new Intl.RelativeTimeFormat("zh-CN", { numeric: "auto" })

/** 秒级时间戳 → "3 分钟前" */
export function timeAgo(seconds: number | null | undefined): string {
  if (!seconds) return "从未"
  const diff = seconds - Date.now() / 1000
  const abs = Math.abs(diff)
  if (abs < 60) return "刚刚"
  if (abs < 3600) return relative.format(Math.round(diff / 60), "minute")
  if (abs < 86400) return relative.format(Math.round(diff / 3600), "hour")
  return relative.format(Math.round(diff / 86400), "day")
}

export function dateTime(seconds: number | null | undefined): string {
  if (!seconds) return "-"
  return new Date(seconds * 1000).toLocaleString("zh-CN", { hour12: false })
}

export type ItemState = "online" | "sold" | "gone"

export function itemState(item: Pick<Item, "status" | "on_platform">): ItemState {
  if (!item.on_platform) return "gone"
  return item.status === 0 ? "online" : "sold"
}

export const STATE_LABEL: Record<ItemState, string> = {
  online: "在售",
  sold: "已卖出",
  gone: "不在主页",
}

// 阿里图床只认这几档尺寸，别的（比如 96）会返回 1×1 的空图。2026-10-29 实测
const THUMB_SIZES = [80, 120, 160, 200, 240, 300, 400]

/**
 * 闲鱼图片缩略图：加 `_<n>x<n>.jpg` 后缀，图床会等比缩放并转成 jpg（heic 原图也行）。
 * `size` 是想要的像素边长，会取不小于它的最近一档。
 */
export function thumb(url: string, size = 160): string {
  if (!url) return ""
  const n = THUMB_SIZES.find((s) => s >= size) ?? THUMB_SIZES[THUMB_SIZES.length - 1]
  return `${url.replace(/^http:/, "https:")}_${n}x${n}.jpg`
}

export function itemUrl(itemId: string): string {
  return `https://www.goofish.com/item?id=${itemId}`
}
