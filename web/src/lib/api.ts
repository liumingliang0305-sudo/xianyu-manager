// 后端接口（app/api.py）。金额：响应里是分（*_cent），请求里的价格是元的字符串。

export interface Account {
  id: string
  saved_at: number | null
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

export const api = {
  accounts: () => request<{ accounts: Account[] }>("/accounts").then((r) => r.accounts),

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
}

/** 给用户看的错误说明：几类需要人处理的错误给出下一步怎么做。 */
export function errorMessage(error: unknown): string {
  if (!(error instanceof ApiError)) return error instanceof Error ? error.message : String(error)
  switch (error.code) {
    case "auth_required":
      return "闲鱼登录已失效，请在终端运行 uv run xianyu login 重新扫码"
    case "risk_control":
      return "触发了闲鱼风控，先暂停操作，到闲鱼 App 或网页完成验证后再试"
    default:
      return error.message
  }
}
