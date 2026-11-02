import type { Item, OrderSide, OrderStatus } from "@/lib/api"

// 最多两位小数，末尾的 0 不显示（和闲鱼一样），千位加逗号
const YUAN = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 2 })

/** 35800 → "¥358"，1050 → "¥10.5"，-139 → "-¥1.39"，140000 → "¥1,400" */
export function yuan(cents: number | null | undefined): string {
  if (cents == null) return "-"
  return `${cents < 0 ? "-" : ""}¥${YUAN.format(Math.abs(cents) / 100)}`
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

// 时间统一按北京时间显示（闲鱼的时间都是北京时间），不跟电脑的时区走
const BEIJING = new Intl.DateTimeFormat("zh-CN", {
  timeZone: "Asia/Shanghai",
  year: "numeric",
  month: "numeric",
  day: "numeric",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
  hourCycle: "h23",
})

/** 秒级时间戳 → 北京时间的年月日时分秒（时分秒补零） */
function beijing(seconds: number) {
  const parts = Object.fromEntries(
    BEIJING.formatToParts(new Date(seconds * 1000)).map((p) => [p.type, p.value]),
  )
  const { year, month, day, hour, minute, second } = parts
  return { year, month, day, hour, minute, second }
}

/** "2025/10/30 12:53:20" */
export function dateTime(seconds: number | null | undefined): string {
  if (!seconds) return "-"
  const t = beijing(seconds)
  return `${t.year}/${t.month}/${t.day} ${t.hour}:${t.minute}:${t.second}`
}

/** "10/30 12:53" */
export function shortTime(seconds: number): string {
  const t = beijing(seconds)
  return `${t.month}/${t.day} ${t.hour}:${t.minute}`
}

/** 聊天里的时间：今天只显示时分，今年的显示月日，再早的带年份 */
export function chatTime(seconds: number | null | undefined): string {
  if (!seconds) return ""
  const t = beijing(seconds)
  const now = beijing(Date.now() / 1000)
  const time = `${t.hour}:${t.minute}`
  if (t.year === now.year && t.month === now.month && t.day === now.day) return time
  const day = t.year === now.year ? `${t.month}月${t.day}日` : `${t.year}年${t.month}月${t.day}日`
  return `${day} ${time}`
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

export const ORDER_STATUS_LABEL: Record<OrderStatus, string> = {
  unpaid: "待付款",
  to_ship: "待发货",
  shipped: "已发货",
  done: "交易成功",
  closed: "已关闭",
  refund: "退款中",
  other: "其他",
}

/** 状态的叫法：买到的订单站在买家这边说，已发货叫"待收货"（和闲鱼「我买到的」一样） */
export function orderStatusLabel(status: OrderStatus, side: OrderSide): string {
  if (side === "bought" && status === "shipped") return "待收货"
  return ORDER_STATUS_LABEL[status] ?? status
}

// 阿里图床只认这几档尺寸，别的（比如 96）会返回 1×1 的空图。2026-10-29 实测，600 以上是 2026-10-31 实测
const THUMB_SIZES = [80, 120, 160, 200, 240, 300, 400, 600, 720, 960, 1200]

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

/** 网页版的订单详情页，卖出的用卖家视角，买到的用买家视角 */
export function orderUrl(orderId: string, side: OrderSide): string {
  const role = side === "bought" ? "buyer" : "seller"
  return `https://www.goofish.com/order-detail?orderId=${orderId}&role=${role}`
}
