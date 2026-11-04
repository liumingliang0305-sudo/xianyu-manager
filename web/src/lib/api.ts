// 后端接口（app/api.py）。金额：响应里是分（*_cent），请求里的价格是元的字符串。
// 除了登录本身，请求都带访问令牌（lib/auth.ts）；令牌过期、作废了换一个再试一次，换不到就回登录页。

import { acceptSession, accessToken, tokenRejected, type AuthSession } from "@/lib/auth"

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
  /** 发布失败时还没有商品 ID，是空串 */
  item_id: string
  /** desc 是以前的"改描述"，现在改描述走编辑（edit），旧记录还在 */
  action: "price" | "desc" | "edit" | "downshelf" | "delete" | "publish" | "relist"
  detail: {
    from?: number | string
    to?: number | string
    orig_to?: number
    /** 发布、编辑、重新上架：从哪个草稿提交的；price 是发布的售价（分） */
    draft_id?: number
    price?: number | null
    /** 重新上架：照哪个商品抄的 */
    from_item?: string
    /** 编辑：改了哪几块（"描述"、"图片"、"价格"……）；改了价格时 from、to 是新旧售价 */
    fields?: string[]
  }
  ok: boolean
  error: string | null
  created_at: number
}

export interface SyncResult {
  added: string[]
  updated: string[]
  gone: string[]
}

/** 卖出的（当前账号是卖家）还是买到的 */
export type OrderSide = "sold" | "bought"

/** 后端按平台的状态文字归类，认不出的是 other */
export type OrderStatus = "unpaid" | "to_ship" | "shipped" | "done" | "closed" | "refund" | "other"

export interface Order {
  account_id: string
  order_id: string
  side: OrderSide
  item_id: string
  title: string
  pic_url: string
  status: OrderStatus
  status_text: string
  price_cent: number | null
  quantity: number
  /** 对方：卖出的是买家，买到的是卖家 */
  peer_id: string
  peer_nick: string
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
  /** 平台给当前账号显示的按钮，比如"修改价格" */
  actions: string[]
  /** 当前账号是不是卖家；平台没给时是 null */
  seller: boolean | null
  /** 对方：卖出的是买家，买到的是卖家 */
  peer_id: string
  peer_nick: string
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
  /** list：整页拉的（我买到的，或专业卖家的卖家工作台）；detail：逐个刷新了本地还没结束的卖出订单 */
  source: "list" | "detail"
  added: string[]
  updated: string[]
  changed: string[]
  failed: string[]
}

/** 从本地存的消息里补订单（卖出的、买到的）的结果：found 消息里有几笔，每笔落在后面一组 */
export interface OrderScanResult {
  found: number
  /** 新记下的 */
  added: string[]
  /** 本地已经有、刷新了一次的 */
  updated: string[]
  /** 本地已经结束（交易成功、已关闭），没再拉 */
  skipped: string[]
  /** 平台查不到 */
  missing: string[]
  /** 平台报错 */
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

/** 搜索排序，空串是综合 */
export type SearchSort = "" | "active" | "credit" | "reduce" | "new" | "price_asc" | "price_desc"

/** 搜索的勾选项：个人闲置、包邮、全新、验货宝 */
export type SearchFilter = "personal" | "free_ship" | "brand_new" | "appraise"

export interface SearchConditions {
  keyword: string
  sort: SearchSort
  /** 只看几天内发布的：1、3、7、14，不限是 null */
  publish_days: number | null
  /** 价格区间，元的字符串，空串是不限 */
  min_price: string
  max_price: string
  filters: SearchFilter[]
}

/** 搜索结果里的一件商品（别人的） */
export interface SearchItem {
  item_id: string
  title: string
  price_cent: number
  /** 卖家填的原价，没填是 null */
  orig_price_cent: number | null
  pic_url: string
  /** 发货地，一般是省或市 */
  area: string
  seller_nick: string
  want_count: number | null
  published_at: number | null
  free_ship: boolean
  /** 成色、品牌、属性、降价这些标签 */
  tags: string[]
  /** "卖家信用极好"、"回复超快"这些 */
  seller_tags: string[]
  url: string
}

export interface SearchResult {
  items: SearchItem[]
  page: number
  has_next: boolean
  /** 平台说一共找到多少条（不一定都翻得到） */
  total: number | null
}

/** 邮费：包邮、按距离计费、一口价、无需邮寄；keep 是编辑的商品沿用原来的（App 里设的运费模板之类） */
export type PostMode = "free" | "distance" | "fixed" | "none" | "keep"

/** 发货地，闲鱼的 itemAddrDTO 原样，存进草稿 */
export interface Address {
  area: string
  city: string
  divisionId: number | string
  gps: string
  poiId: string
  poiName: string
  prov: string
}

/** 可选的发货地：上次用的排第一，后面是常用地址 */
export interface AddressOption {
  key: string
  name: string
  /** 省市区和详细地址 */
  detail: string
  address: Address
}

/** 草稿里的一张图。url 是闲鱼上的地址（传过的，或者编辑的商品原有的），null 是还只在后台服务器上 */
export interface DraftImage {
  sha: string
  width: number
  height: number
  url: string | null
}

/** 闲鱼推荐的分类或属性（品牌、成色……），selected 是选中的 key */
export interface DraftProperty {
  id: string
  name: string
  multiple: boolean
  options: { key: string; name: string }[]
  selected: string[]
}

export interface Draft {
  id: number
  description: string
  price_cent: number | null
  orig_price_cent: number | null
  post_mode: PostMode
  /** 一口价的邮费 */
  post_price_cent: number | null
  /** 编辑的商品原来的邮费对不上四种选法，可以选 keep 沿用 */
  post_keep: boolean
  take_self: boolean
  images: DraftImage[]
  /** 分类名，还没推荐过是 null */
  category: string | null
  /** 第一项是分类（id "-10000"），后面是属性 */
  properties: DraftProperty[]
  /** 编辑的商品还没重新推荐时，它现在的属性（不含分类） */
  labels: { name: string; value: string }[]
  /** 网页端不支持发布的东西（要填 3C 证书编号之类），有就发不了 */
  unsupported: string[]
  address: Address | null
  /** 发布成功后的商品 ID；编辑草稿提交成功后是改的那个商品 */
  item_id: string | null
  /** 编辑已有商品的草稿：改的是哪个商品。发新商品的是 null */
  edit_item_id: string | null
  /** 重新上架的草稿：照哪个商品抄的，发布出去是一个新商品。别的是 null */
  relist_item_id: string | null
  /** 上次发布（提交）失败的原因 */
  error: string | null
  created_at: number
  updated_at: number
  published_at: number | null
}

/** 保存草稿时整个提交的表单；价格是元的字符串，空是没填 */
export interface DraftForm {
  description: string
  price: string | null
  orig_price: string | null
  post_mode: PostMode
  post_price: string | null
  take_self: boolean
  /** 图片的顺序（sha） */
  images: string[]
  address: Address | null
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

function withToken(init: RequestInit | undefined, token: string | null): RequestInit {
  return {
    ...init,
    headers: {
      "content-type": "application/json",
      ...(token ? { authorization: `Bearer ${token}` } : {}),
      ...init?.headers,
    },
  }
}

async function isTokenRejected(resp: Response): Promise<boolean> {
  if (resp.status !== 401) return false
  const body = await resp.clone().json().catch(() => null)
  return body?.code === "unauthenticated"
}

/** 带上访问令牌的 fetch。接口说令牌不行了（401 unauthenticated），换一个新的再试一次 */
async function authorizedFetch(url: string, init?: RequestInit): Promise<Response> {
  const token = await accessToken()
  const resp = await fetch(url, withToken(init, token))
  if (!token || !(await isTokenRejected(resp))) return resp
  const fresh = await tokenRejected()
  return fresh ? fetch(url, withToken(init, fresh)) : resp
}

async function request<T>(path: string, init?: RequestInit, { auth = true } = {}): Promise<T> {
  let resp: Response
  try {
    resp = auth
      ? await authorizedFetch(`/api${path}`, init)
      : await fetch(`/api${path}`, withToken(init, null))
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
const draftsPath = (account: string) => `/accounts/${encodeURIComponent(account)}/drafts`
const ordersPath = (account: string) => `/accounts/${encodeURIComponent(account)}/orders`
const convPath = (account: string, cid: string) =>
  `/accounts/${encodeURIComponent(account)}/conversations/${encodeURIComponent(cid)}`

/** 草稿里的一张图。要带访问令牌取，返回图片本身，页面上用 blob: 地址显示 */
export async function draftImage(account: string, draftId: number, sha: string): Promise<Blob> {
  let resp: Response
  try {
    resp = await authorizedFetch(`/api${draftsPath(account)}/${draftId}/images/${sha}`)
  } catch {
    throw new ApiError(0, "network", "连不上后台，确认 uv run xym serve 在运行")
  }
  if (!resp.ok) {
    const body = await resp.json().catch(() => null)
    throw new ApiError(resp.status, body?.code ?? "http_error", body?.message ?? "取图片失败")
  }
  return resp.blob()
}

/** 断了以后过多久重连 */
const STREAM_RETRY_MS = 3000

/**
 * 订阅新消息推送（Server-Sent Events）：新消息、补拉完、连接状态变了都会推一条。
 * 用 fetch 读而不是 EventSource：EventSource 带不了 Authorization 头，令牌只能放进网址，会被记进访问日志。
 * 连上时调 onOpen；断了过一会儿自己重连（令牌过期了先换新的）；没登录、账号不归自己了就不再连。signal 中止时停。
 */
export async function subscribeMessages(
  account: string,
  signal: AbortSignal,
  onOpen: () => void,
  onEvent: (type: string, data: string) => void,
) {
  const url = `/api/accounts/${encodeURIComponent(account)}/messages/stream`
  while (!signal.aborted) {
    try {
      const resp = await authorizedFetch(url, { signal, headers: { accept: "text/event-stream" } })
      if (resp.status === 401 || resp.status === 404) return
      if (resp.ok && resp.body) {
        onOpen()
        await readEvents(resp.body, onEvent)
      }
    } catch {
      // 网络断了、后台重启了，或者是中止了
    }
    if (signal.aborted) return
    await new Promise((resolve) => setTimeout(resolve, STREAM_RETRY_MS))
  }
}

/** 按 SSE 的格式拆事件：空行分隔，event: 是类型，data: 是内容；": ping" 这种注释行没有 data，跳过 */
async function readEvents(
  body: ReadableStream<Uint8Array>,
  onEvent: (type: string, data: string) => void,
) {
  const reader = body.getReader()
  const decoder = new TextDecoder()
  let buffer = ""
  for (;;) {
    const { value, done } = await reader.read()
    if (done) return
    buffer += decoder.decode(value, { stream: true })
    let end = buffer.indexOf("\n\n")
    while (end >= 0) {
      let type = "message"
      const data: string[] = []
      for (const line of buffer.slice(0, end).split("\n")) {
        if (line.startsWith("event:")) type = line.slice(6).trim()
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""))
      }
      if (data.length) onEvent(type, data.join("\n"))
      buffer = buffer.slice(end + 2)
      end = buffer.indexOf("\n\n")
    }
  }
}

export const api = {
  /** 用户名密码登录，成功后记下访问令牌 */
  signIn: (username: string, password: string) =>
    request<AuthSession>(
      "/auth/login",
      { method: "POST", body: JSON.stringify({ username, password }) },
      { auth: false },
    ).then(acceptSession),

  /** 改密码：别处的登录全部作废，这个页面换一套新令牌 */
  changePassword: (oldPassword: string, newPassword: string) =>
    request<AuthSession>("/auth/password", {
      method: "POST",
      body: JSON.stringify({ old_password: oldPassword, new_password: newPassword }),
    }).then(acceptSession),

  /** 生成过插件令牌没有（令牌本身只在生成时给一次） */
  pluginToken: () => request<{ created_at: number | null }>("/auth/plugin-token"),

  /** 生成浏览器插件用的令牌，之前的作废 */
  newPluginToken: () =>
    request<{ token: string; created_at: number }>("/auth/plugin-token", { method: "POST" }),

  dropPluginToken: () => request<{ ok: true }>("/auth/plugin-token", { method: "DELETE" }),

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

  /** 这个商品的编辑草稿，没有就按闲鱼上现在的样子建一个（会请求一次闲鱼） */
  editItem: (account: string, itemId: string) =>
    request<Draft>(`${itemsPath(account)}/${itemId}/edit`, { method: "POST" }),

  /** 照下架、卖掉的商品抄一个发新商品的草稿，有没发的就接着用（没有时请求一次闲鱼）；publish 是抄完直接发 */
  relistItem: (account: string, itemId: string, publish = false) =>
    request<Draft>(`${itemsPath(account)}/${itemId}/relist`, {
      method: "POST",
      body: JSON.stringify({ publish }),
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

  /** 刷新一边的订单：买到的整页同步"我买到的"；卖出的个人卖家逐个刷新还没结束的 */
  syncOrders: (account: string, side: OrderSide) =>
    request<OrderSyncResult>(`${ordersPath(account)}/sync`, {
      method: "POST",
      body: JSON.stringify({ side }),
    }),

  /** 从本地存的消息里找订单（卖出的、买到的），逐笔拉详情记下来（会请求闲鱼，订单多时要一会儿） */
  scanOrders: (account: string) =>
    request<OrderScanResult>(`${ordersPath(account)}/scan`, { method: "POST" }),

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

  /** 用这个账号搜闲鱼上的商品，一页 30 条，会请求闲鱼 */
  search: (account: string, conditions: SearchConditions, page: number) =>
    request<SearchResult>(`/accounts/${encodeURIComponent(account)}/search`, {
      method: "POST",
      body: JSON.stringify({ ...conditions, page }),
    }),

  drafts: (account: string) => request<{ drafts: Draft[] }>(draftsPath(account)).then((r) => r.drafts),

  createDraft: (account: string) => request<Draft>(draftsPath(account), { method: "POST" }),

  getDraft: (account: string, draftId: number) => request<Draft>(`${draftsPath(account)}/${draftId}`),

  /** 保存表单，整个提交 */
  updateDraft: (account: string, draftId: number, form: DraftForm) =>
    request<Draft>(`${draftsPath(account)}/${draftId}`, {
      method: "PUT",
      body: JSON.stringify(form),
    }),

  /** 删草稿（已经发出去的商品不受影响） */
  deleteDraft: (account: string, draftId: number) =>
    request<{ ok: true }>(`${draftsPath(account)}/${draftId}`, { method: "DELETE" }),

  /** 加一张图：请求体就是图片文件，后台转成 JPEG 存在服务器上 */
  addDraftImage: (account: string, draftId: number, file: File) =>
    request<Draft>(`${draftsPath(account)}/${draftId}/images`, {
      method: "POST",
      body: file,
      headers: { "content-type": file.type || "application/octet-stream" },
    }),

  removeDraftImage: (account: string, draftId: number, sha: string) =>
    request<Draft>(`${draftsPath(account)}/${draftId}/images/${sha}`, { method: "DELETE" }),

  /** 按描述推荐分类和属性（会请求闲鱼），之前选过的带着 */
  recommend: (account: string, draftId: number) =>
    request<Draft>(`${draftsPath(account)}/${draftId}/recommend`, { method: "POST" }),

  /** 改一项分类或属性，闲鱼按新的选择刷新别的属性（会请求闲鱼） */
  chooseLabel: (account: string, draftId: number, propertyId: string, keys: string[]) =>
    request<Draft>(`${draftsPath(account)}/${draftId}/labels`, {
      method: "PUT",
      body: JSON.stringify({ property_id: propertyId, keys }),
    }),

  /** 发到闲鱼：查发布限制和违禁词、传图、发布。编辑草稿是把改动提交上去，force：改价超过保护线也提交 */
  publish: (account: string, draftId: number, force = false) =>
    request<Draft>(`${draftsPath(account)}/${draftId}/publish`, {
      method: "POST",
      body: JSON.stringify({ force }),
    }),

  /** 闲鱼账号里上次用的地址和常用地址（会请求闲鱼）；search：后台配了高德 key，能按名字搜 */
  addresses: (account: string) =>
    request<{ addresses: AddressOption[]; search: boolean }>(
      `/accounts/${encodeURIComponent(account)}/publish/addresses`,
    ),

  /** 按名字搜发货地（后台用高德搜，不请求闲鱼），最多 10 个 */
  searchPlaces: (keyword: string) =>
    request<{ places: AddressOption[] }>(
      `/publish/places?q=${encodeURIComponent(keyword)}`,
    ).then((r) => r.places),

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
