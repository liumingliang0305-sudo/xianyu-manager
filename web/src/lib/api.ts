// 后端接口（app/api.py）。金额：响应里是分（*_cent），请求里的价格是元的字符串。

/** 风控熔断：从 since 到 until（秒级时间戳）这个账号不发任何请求 */
export interface AccountBlock {
  since: number
  until: number
  reason: string
}

/** valid 有效；expired 免密续期也没成功，要重新登录；risk 触发风控；error 网络或平台出错，没判断出来 */
export type LoginState = "valid" | "expired" | "risk" | "error"

export interface LoginStatus {
  state: LoginState
  checked_at: number
  /** 这次检查时做了免密续期 */
  renewed: boolean
  message: string
  /** 保持登录到期时间（秒），没开保持登录是 null */
  keep_login_until: number | null
}

export interface Account {
  id: string
  saved_at: number | null
  login: LoginStatus
  block: AccountBlock | null
}

/**
 * 扫码登录的进度。waiting 等扫码；scanned 已扫码，等手机上确认；confirmed 正在拿凭证；done 成功，凭证已保存；
 * expired 二维码过期；canceled 手机上取消了；verify 平台要求额外身份验证；failed 其他错误
 */
export type LoginPhase =
  | "waiting"
  | "scanned"
  | "confirmed"
  | "done"
  | "expired"
  | "canceled"
  | "verify"
  | "failed"

export interface LoginProgress {
  id: string
  phase: LoginPhase
  /** 出错的原因；登录成功时是保持登录的情况 */
  message: string
  /** 登录成功后是账号 unb */
  account_id: string
}

/** 导入浏览器登录的结果 */
export interface LoginImport {
  account_id: string
  /** 保持登录到期时间（秒），这份 cookie 没开保持登录是 null */
  keep_login_until: number | null
  message: string
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

/**
 * 即时消息的连接状态。connecting 正在连；online 在线，新消息实时收；waiting 断了，等着重连；
 * stopped 停了，needs 说要人做什么；off 后台没开即时消息（只有 xym serve 会连）
 */
export type ImState = "connecting" | "online" | "waiting" | "stopped" | "off"

export interface ImStatus {
  state: ImState
  since: number | null
  message: string
  /**
   * 停下以后要做的：login 重新登录；unblock 完成验证后解除熔断；reconnect 手动重新连接；
   * elsewhere 另一个 xym serve 在连这个账号，关掉它（这边会自动接上）
   */
  needs: "login" | "unblock" | "reconnect" | "elsewhere" | null
}

export interface Conversation {
  account_id: string
  cid: string
  peer_id: string
  peer_name: string
  /** 会话类型，"1" 是商品会话；只存和人的聊天 */
  biz_type: string
  item_id: string
  item_title: string
  item_pic: string
  order_id: string
  unread: number
  updated_at: number | null
  fetched_at: number | null
  /** 平台上还有没有更早的消息，没往前翻过是 null */
  older_more: boolean | null
  first_seen_at: number
  last_text: string
  last_sender_id: string
  last_sent_at: number | null
  /** 平台上有本地还没拉的消息，打开时补拉 */
  stale: boolean
}

export interface ChatMessage {
  account_id: string
  message_id: string
  cid: string
  sender_id: string
  sender_name: string
  sent_at: number | null
  /** 1 是文本，别的类型 text 是平台给的摘要 */
  content_type: number | null
  text: string
  item_id: string
  received_at: number
  /** 这个账号自己发的 */
  mine: boolean
  /** 图片消息里的图，别的类型是空数组 */
  images: ChatImage[]
}

/** 聊天里的一张图：阿里 CDN 的地址和原图宽高（像素） */
export interface ChatImage {
  url: string
  width: number | null
  height: number | null
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
const convPath = (account: string, cid: string) =>
  `/accounts/${encodeURIComponent(account)}/conversations/${encodeURIComponent(cid)}`

/** 新消息、补拉完、连接状态变了都会推一条（Server-Sent Events） */
export const messageStreamUrl = (account: string) =>
  `/api/accounts/${encodeURIComponent(account)}/messages/stream`

export const api = {
  accounts: () => request<{ accounts: Account[] }>("/accounts").then((r) => r.accounts),

  checkAccount: (account: string) =>
    request<Account>(`/accounts/${encodeURIComponent(account)}/check`, { method: "POST" }),

  unblock: (account: string) =>
    request<{ ok: boolean }>(`/accounts/${encodeURIComponent(account)}/block`, {
      method: "DELETE",
    }),

  /** 取一张新二维码，上一张没扫完的作废 */
  startLogin: () => request<LoginProgress & { qr_svg: string }>("/login", { method: "POST" }),

  loginProgress: (id: string) => request<LoginProgress>(`/login/${encodeURIComponent(id)}`),

  cancelLogin: (id: string) =>
    request<{ ok: boolean }>(`/login/${encodeURIComponent(id)}`, { method: "DELETE" }),

  /** 导入浏览器的登录（整串 Cookie 请求头）。后台先向平台确认能用再保存 */
  importLogin: (cookie: string) =>
    request<LoginImport>("/login/import", { method: "POST", body: JSON.stringify({ cookie }) }),

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

  conversations: (account: string) =>
    request<{ conversations: Conversation[]; im: ImStatus }>(
      `/accounts/${encodeURIComponent(account)}/conversations`,
    ),

  /** 本地库里最新的 limit 条（按时间正序）；local_more：本地还有更早的 */
  messages: (account: string, cid: string, limit: number) =>
    request<{ conversation: Conversation; messages: ChatMessage[]; local_more: boolean }>(
      `${convPath(account, cid)}/messages?limit=${limit}`,
    ),

  /** 向平台拉：最新的消息（补上本地缺的），或者往前翻一页。要即时消息在线 */
  fetchMessages: (account: string, cid: string, older: boolean) =>
    request<{ added: number; older_more: boolean | null }>(`${convPath(account, cid)}/fetch`, {
      method: "POST",
      body: JSON.stringify({ older }),
    }),

  /**
   * 发一条文本消息，返回存进本地库的这条。要即时消息在线，只能发商品会话。
   * 出错 code 是 send_uncertain 时消息可能已经发出去了，别急着重发
   */
  sendMessage: (account: string, cid: string, text: string) =>
    request<{ message: ChatMessage }>(`${convPath(account, cid)}/messages`, {
      method: "POST",
      body: JSON.stringify({ text }),
    }).then((r) => r.message),

  /** 看过了：清掉平台上这个会话的红点，本地未读清零（不给对方发已读回执）。返回清之前的未读数 */
  markRead: (account: string, cid: string) =>
    request<{ cleared: number }>(`${convPath(account, cid)}/read`, { method: "POST" }),

  reconnectIm: (account: string) =>
    request<ImStatus>(`/accounts/${encodeURIComponent(account)}/im/reconnect`, {
      method: "POST",
    }),

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
      return "闲鱼登录已失效，请重新登录"
    case "risk_control":
      return "触发了闲鱼风控，账号已自动暂停请求。先到闲鱼 App 或网页完成验证"
    case "circuit_open":
      return `${error.message}。在闲鱼 App 或网页完成验证后，可以在页面顶部提前解除`
    default:
      return error.message
  }
}
