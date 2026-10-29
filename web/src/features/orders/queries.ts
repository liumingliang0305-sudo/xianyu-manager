import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { api } from "@/lib/api"

export const orderKeys = {
  list: (account: string) => ["orders", account] as const,
  detail: (account: string, orderId: string) => ["order-detail", account, orderId] as const,
  priceFields: (account: string, orderId: string) =>
    ["order-price-fields", account, orderId] as const,
  ops: (account: string, orderId?: string) => ["order-ops", account, orderId ?? "all"] as const,
}

/** 本地库里的订单，读本地，不请求闲鱼。 */
export function useOrders(account: string) {
  return useQuery({ queryKey: orderKeys.list(account), queryFn: () => api.orders(account) })
}

/** 实时详情，会请求一次闲鱼；一分钟内重复打开用缓存。 */
export function useOrderDetail(account: string, orderId: string) {
  return useQuery({
    queryKey: orderKeys.detail(account, orderId),
    queryFn: () => api.orderDetail(account, orderId),
    staleTime: 60_000,
  })
}

/** 改价弹窗打开时拉一次当前价格，每次打开都重新拉，不用缓存。 */
export function useOrderPriceFields(account: string, orderId: string) {
  return useQuery({
    queryKey: orderKeys.priceFields(account, orderId),
    queryFn: () => api.orderPriceFields(account, orderId),
    gcTime: 0,
  })
}

export function useOrderOps(account: string, orderId?: string) {
  return useQuery({
    queryKey: orderKeys.ops(account, orderId),
    queryFn: () => api.orderOps(account, orderId),
  })
}

/** 改动成功或失败后都要刷新：失败也会记一条改动记录。 */
function useRefresh(account: string) {
  const client = useQueryClient()
  return (orderId?: string) => {
    client.invalidateQueries({ queryKey: orderKeys.list(account) })
    client.invalidateQueries({ queryKey: ["order-ops", account] })
    if (orderId) client.invalidateQueries({ queryKey: orderKeys.detail(account, orderId) })
  }
}

export function useSyncOrders(account: string) {
  const refresh = useRefresh(account)
  return useMutation({
    mutationFn: () => api.syncOrders(account),
    onSettled: () => refresh(),
  })
}

export function useAddOrder(account: string) {
  const refresh = useRefresh(account)
  return useMutation({
    mutationFn: (order: string) => api.addOrder(account, order),
    onSuccess: (r) => refresh(r.order.order_id),
  })
}

export function useRemoveOrder(account: string) {
  const refresh = useRefresh(account)
  return useMutation({
    mutationFn: (orderId: string) => api.removeOrder(account, orderId),
    onSettled: () => refresh(),
  })
}

export function useChangeOrderPrice(account: string) {
  const refresh = useRefresh(account)
  return useMutation({
    mutationFn: (v: { orderId: string; fees: Record<string, string>; force?: boolean }) =>
      api.changeOrderPrice(account, v.orderId, { fees: v.fees, force: v.force }),
    onSettled: (_data, _error, v) => refresh(v.orderId),
  })
}
