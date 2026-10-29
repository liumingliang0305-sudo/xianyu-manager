import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { api } from "@/lib/api"

export const itemKeys = {
  list: (account: string) => ["items", account] as const,
  detail: (account: string, itemId: string) => ["item-detail", account, itemId] as const,
  ops: (account: string, itemId?: string) => ["item-ops", account, itemId ?? "all"] as const,
}

/** 本地库里的商品（含已经不在主页上的），读本地，不请求闲鱼。 */
export function useItems(account: string) {
  return useQuery({ queryKey: itemKeys.list(account), queryFn: () => api.items(account) })
}

/** 实时详情，会请求一次闲鱼；一分钟内重复打开用缓存。 */
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
    mutationFn: (stats: boolean) => api.sync(account, stats),
    onSettled: () => refresh(),
  })
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

export function useChangeDesc(account: string) {
  const refresh = useRefresh(account)
  return useMutation({
    mutationFn: (v: { itemId: string; desc: string }) => api.changeDesc(account, v.itemId, v.desc),
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

export function useDeleteItem(account: string) {
  const refresh = useRefresh(account)
  return useMutation({
    mutationFn: (itemId: string) => api.remove(account, itemId),
    onSettled: (_data, _error, itemId) => refresh(itemId),
  })
}
