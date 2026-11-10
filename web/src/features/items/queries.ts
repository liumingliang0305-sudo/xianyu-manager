import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { draftKeys } from "@/features/publish/queries"
import { api, errorMessage, type Draft, type Item } from "@/lib/api"
import { ExtensionError, fetchItemDetailViaExtension } from "@/lib/extension"

export const itemKeys = {
  list: (account: string) => ["items", account] as const,
  detail: (account: string, itemId: string) => ["item-detail", account, itemId] as const,
  ops: (account: string, itemId?: string) => ["item-ops", account, itemId ?? "all"] as const,
}

/** 本地库里的商品（含已经不在主页上的），读本地，不请求闲鱼。 */
export function useItems(account: string) {
  return useQuery({ queryKey: itemKeys.list(account), queryFn: () => api.items(account) })
}

/**
 * 商品现在的描述、图片、价格、库存，会请求一次闲鱼；一分钟内重复打开用缓存。
 * 后台调的是卖家自己的编辑页接口。买家看的商品详情接口从后台调会被闲鱼要求验证、账号熔断，不调
 */
export function useItemDetail(account: string, itemId: string, enabled = true) {
  return useQuery({
    queryKey: itemKeys.detail(account, itemId),
    queryFn: () => api.detail(account, itemId),
    enabled,
    staleTime: 60_000,
  })
}

export function useItemOps(account: string, itemId?: string) {
  return useQuery({
    queryKey: itemKeys.ops(account, itemId),
    queryFn: () => api.ops(account, itemId),
  })
}

/** 改动成功或失败后都要刷新：失败也会记一条改动记录。 */
function useRefresh(account: string) {
  const client = useQueryClient()
  return (itemId?: string) => {
    client.invalidateQueries({ queryKey: itemKeys.list(account) })
    client.invalidateQueries({ queryKey: ["item-ops", account] })
    if (itemId) client.invalidateQueries({ queryKey: itemKeys.detail(account, itemId) })
  }
}

export function useSyncItems(account: string) {
  const refresh = useRefresh(account)
  return useMutation({
    mutationFn: () => api.sync(account),
    onSettled: () => refresh(),
  })
}

/**
 * 刷新一件商品的想要、浏览、收藏：请浏览器插件在 Chrome 里打开它的闲鱼页面拿（一件几秒），
 * 拿到了存到后台，列表里的这一件跟着换。浏览、收藏只有买家看的商品详情里有，后台自己不去拿
 */
export function useRefreshItemStats(account: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: async (itemId: string) =>
      api.saveItemStats(account, itemId, await fetchItemDetailViaExtension(itemId)),
    onSuccess: (saved) => {
      client.setQueryData<{ items: Item[]; last_synced_at: number | null }>(
        itemKeys.list(account),
        (old) =>
          old && { ...old, items: old.items.map((it) => (it.item_id === saved.item_id ? saved : it)) },
      )
      void client.invalidateQueries({ queryKey: itemKeys.list(account) })
    },
  })
}

/** 没刷新成的原因。要验证时插件已经把那个闲鱼页面切到前面了 */
export function statsErrorMessage(error: unknown): string {
  if (!(error instanceof ExtensionError)) return errorMessage(error)
  if (error.code === "validate")
    return "闲鱼要验证：在 Chrome 里那个闲鱼商品页面上过一下滑块，过一分钟再点「刷新浏览/收藏」"
  return error.message
}

export function useChangePrice(account: string) {
  const refresh = useRefresh(account)
  return useMutation({
    mutationFn: (v: { itemId: string; price: string; origPrice?: string; force?: boolean }) =>
      api.changePrice(account, v.itemId, {
        price: v.price,
        orig_price: v.origPrice,
        force: v.force,
      }),
    onSettled: (_data, _error, v) => refresh(v.itemId),
  })
}

export function useDownshelf(account: string) {
  const refresh = useRefresh(account)
  return useMutation({
    mutationFn: (itemId: string) => api.downshelf(account, itemId),
    onSettled: (_data, _error, itemId) => refresh(itemId),
  })
}

/**
 * 重新上架：照这个商品抄一个草稿。publish 是抄完直接发，否则拿到草稿放进发布页的列表（已经在里面的换掉），
 * 跳过去时直接就是它。直接发的成功失败都要刷新：新商品进列表，失败的草稿和原因进发布页。
 */
export function useRelist(account: string) {
  const client = useQueryClient()
  const refresh = useRefresh(account)
  return useMutation({
    mutationFn: (v: { itemId: string; publish: boolean }) =>
      api.relistItem(account, v.itemId, v.publish),
    onSuccess: (draft) =>
      client.setQueryData<Draft[]>(draftKeys.list(account), (old) =>
        old ? [draft, ...old.filter((d) => d.id !== draft.id)] : old,
      ),
    onSettled: (_data, _error, v) => {
      if (!v.publish) return
      refresh()
      client.invalidateQueries({ queryKey: draftKeys.list(account) })
    },
  })
}

export function useDeleteItem(account: string) {
  const refresh = useRefresh(account)
  return useMutation({
    mutationFn: (itemId: string) => api.remove(account, itemId),
    onSettled: (_data, _error, itemId) => refresh(itemId),
  })
}
