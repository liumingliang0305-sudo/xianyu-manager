import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { api, type PddGoods } from "@/lib/api"
import { draftKeys } from "@/features/publish/queries"

export const pddKeys = {
  list: ["pdd-goods"] as const,
  detail: (goodsId: string) => ["pdd-goods", goodsId] as const,
}

/** 拼多多货源列表。插件导入是在别的标签页里做的，回到这一页时重新拉一次 */
export function usePddGoods() {
  return useQuery({ queryKey: pddKeys.list, queryFn: api.pddGoods, refetchOnWindowFocus: true })
}

export function usePddGoodsDetail(goodsId: string) {
  return useQuery({ queryKey: pddKeys.detail(goodsId), queryFn: () => api.pddGoodsDetail(goodsId) })
}

function usePutGoods() {
  const client = useQueryClient()
  return (goods: PddGoods) => {
    client.setQueryData(pddKeys.detail(goods.goods_id), goods)
    void client.invalidateQueries({ queryKey: pddKeys.list, exact: true })
  }
}

export function useFetchPddImages() {
  const put = usePutGoods()
  return useMutation({
    mutationFn: api.fetchPddImages,
    onSuccess: (result) => put(result.goods),
  })
}

export function useDeletePddGoods() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.deletePddGoods,
    onSuccess: (_, goodsId) => {
      client.removeQueries({ queryKey: pddKeys.detail(goodsId) })
      void client.invalidateQueries({ queryKey: pddKeys.list, exact: true })
    },
  })
}

/** 照货源在当前账号建发布草稿；建好以后发布页的草稿列表要重新拉 */
export function useDraftFromPdd(account: string | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (goodsId: string) => api.draftFromPdd(account!, goodsId),
    onSuccess: () => {
      if (account) void client.invalidateQueries({ queryKey: draftKeys.list(account) })
    },
  })
}
