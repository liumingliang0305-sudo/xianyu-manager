// 后端接口（app/api.py）。金额：响应里是分（*_cent），请求里的价格是元的字符串。

/** 风控熔断：从 since 到 until（秒级时间戳）这个账号不发任何请求 */
export interface AccountBlock {
  since: number
  until: number
  reason: string
}

export interface Account {
  id: string
  saved_at: number | null
  block: AccountBlock | null
}

export interface Item {
  account_id: string
  item_id: string
  title: string
  price_cent: number
  status: number
  category_id: number | null
  pic_url: string
  want_count: number | null
  browse_count: number | null
  collect_count: number | null
  on_platform: boolean
  first_seen_at: number
  synced_at: number
}

export interface ItemDetail {
  item_id: string
  title: string
  desc: string
  price_cent: number
  orig_price_cent: number
  quantity: number
  status: number
  status_text: string
  browse_count: number
  want_count: number
  collect_count: number
  category_id: number | null
  image_urls: string[]
  created_at: number | null
}

export interface ItemOp {
  id: number
  account_id: string
  item_id: string
  action: "price" | "desc" | "downshelf" | "delete"
  detail: { from?: number | string; to?: number | string; orig_to?: number }
  ok: boolean
  error: string | null
  created_at: number
}

export interface SyncResult {
  added: string[]
  updated: string[]
  gone: string[]
}

/** 后端按平台的状态文字归类，认不出的是 other */
export type OrderStatus = "unpaid" | "to_ship" | "shipped" | "done" | "closed" | "refund" | "other"

export interface Order {
  account_id: string
  order_id: string
  item_id: string
  title: string
  pic_url: string
  status: OrderStatus
  status_text: string
  price_cent: number | null
  quantity: number
  buyer_id: string
  buyer_nick: string
  created_at: number | null
  first_seen_at: number
  synced_at: number
}

export interface OrderDetail {
  order_id: string
  item_id: string
  status: OrderStatus
  status_title: string
  status_desc: string
  steps: { title: string; done: boolean; time: number | null }[]
  title: string
  pic_url: string
  sku: string
  quantity: number
  amount_title: string
  amount_cent: number | null
  /** 金额明细，含平台扣的软件服务费（负数） */
  bills: { title: string; cent: number | null }[]
  /** 卖家视角的"预估到手" */
  income_title: string
  income_cent: number | null
  fields: { title: string; value: string }[]
  receiver_name: string
  receiver_phone: string
  receiver_address: string
  /** 平台给卖家显示的按钮，比如"修改价格" */
  actions: string[]
  /** 当前账号是不是卖家；平台没给时是 null */
  seller: boolean | null
  buyer_id: string
  buyer_nick: string
  created_at: number | null
}

/** 订单改价里的一项（商品价格、运费……），key 是提交时的字段名 */
export interface PriceField {
  key: string
  name: string
  price_cent: number | null
}

export interface OrderOp {
  id: number
  account_id: string
  order_id: string
  action: "price"
  detail: {
    from?: number
    to?: number
    fields?: { key: string; name: string; from: number | null; to: number }[]
  }
  ok: boolean
  error: string | null
  created_at: number
}

export interface OrderSyncResult {
  /** list：从卖家工作台整页拉的（专业卖家）；detail：逐个刷新了本地还没结束的订单 */
  source: "list" | "detail"
  added: string[]
  updated: string[]
  changed: string[]
  failed: string[]
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string

  constructor(status: number, code: string, message: string) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let resp: Response
  try {
    resp = await fetch(`/api${path}`, {
      ...init,
      headers: { "content-type": "application/json", ...init?.headers },
    })
  } catch {
    throw new ApiError(0, "network", "连不上后台，确认 uv run xym serve 在运行")
  }
  const body = await resp.json().catch(() => null)
  if (!resp.ok) {
    const message =
      body?.message ?? (typeof body?.detail === "string" ? body.detail : null) ?? `请求失败（HTTP ${resp.status}）`
    throw new ApiError(resp.status, body?.code ?? "http_error", message)
  }
  return body as T
}

const itemsPath = (account: string) => `/accounts/${encodeURIComponent(account)}/items`
const ordersPath = (account: string) => `/accounts/${encodeURIComponent(account)}/orders`

export const api = {
  accounts: () => request<{ accounts: Account[] }>("/accounts").then((r) => r.accounts),

  unblock: (account: string) =>
    request<{ ok: boolean }>(`/accounts/${encodeURIComponent(account)}/block`, {
      method: "DELETE",
    }),

  items: (account: string) =>
    request<{ items: Item[]; last_synced_at: number | null }>(
      `${itemsPath(account)}?include_gone=true`,
    ),

  sync: (account: string, stats: boolean) =>
    request<SyncResult>(`${itemsPath(account)}/sync`, {
      method: "POST",
      body: JSON.stringify({ stats }),
    }),

  detail: (account: string, itemId: string) =>
    request<ItemDetail>(`${itemsPath(account)}/${itemId}/detail`),

  changePrice: (
    account: string,
    itemId: string,
    body: { price: string; orig_price?: string; force?: boolean },
  ) =>
    request<{ old_price_cent: number; price_cent: number }>(`${itemsPath(account)}/${itemId}/price`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  changeDesc: (account: string, itemId: string, desc: string) =>
    request<{ ok: true }>(`${itemsPath(account)}/${itemId}/desc`, {
      method: "PUT",
      body: JSON.stringify({ desc }),
    }),

  downshelf: (account: string, itemId: string) =>
    request<{ ok: true }>(`${itemsPath(account)}/${itemId}/downshelf`, { method: "POST" }),

  remove: (account: string, itemId: string) =>
    request<{ ok: true }>(`${itemsPath(account)}/${itemId}`, { method: "DELETE" }),

  ops: (account: string, itemId?: string) =>
    request<{ ops: ItemOp[] }>(
      `/accounts/${encodeURIComponent(account)}/item-ops${itemId ? `?item_id=${itemId}` : ""}`,
    ).then((r) => r.ops),

  orders: (account: string) =>
    request<{ orders: Order[]; last_synced_at: number | null }>(ordersPath(account)),

  addOrder: (account: string, order: string) =>
    request<{ order: Order; added: boolean }>(ordersPath(account), {
      method: "POST",
      body: JSON.stringify({ order }),
    }),

  removeOrder: (account: string, orderId: string) =>
    request<{ ok: true }>(`${ordersPath(account)}/${orderId}`, { method: "DELETE" }),

  syncOrders: (account: string) =>
    request<OrderSyncResult>(`${ordersPath(account)}/sync`, { method: "POST" }),

  orderDetail: (account: string, orderId: string) =>
    request<OrderDetail>(`${ordersPath(account)}/${orderId}/detail`),

  orderPriceFields: (account: string, orderId: string) =>
    request<{ fields: PriceField[] }>(`${ordersPath(account)}/${orderId}/price-fields`).then(
      (r) => r.fields,
    ),

  changeOrderPrice: (
    account: string,
    orderId: string,
    body: { fees: Record<string, string>; force?: boolean },
  ) =>
    request<{ old_price_cent: number; price_cent: number }>(
      `${ordersPath(account)}/${orderId}/price`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  orderOps: (account: string, orderId?: string) =>
    request<{ ops: OrderOp[] }>(
      `/accounts/${encodeURIComponent(account)}/order-ops${orderId ? `?order_id=${orderId}` : ""}`,
    ).then((r) => r.ops),
}

/** 给用户看的错误说明：几类需要人处理的错误给出下一步怎么做。 */
export function errorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return error instanceof Error ? error.message : String(error)
  switch (error.code) {
    case "auth_required":
      return "闲鱼登录已失效，请在终端运行 uv run xianyu login 重新扫码"
    case "risk_control":
      return "触发了闲鱼风控，账号已自动暂停请求。先到闲鱼 App 或网页完成验证"
    case "circuit_open":
      return `${error.message}。在闲鱼 App 或网页完成验证后，可以在页面顶部提前解除`
    default:
      return error.message
  }
}
