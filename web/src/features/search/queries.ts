import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { api, type SearchConditions, type SearchItem, type StashItem } from "@/lib/api"

/**
 * 搜索结果，一页一页往后加载。每页都会请求一次闲鱼，算当前账号的请求：
 * 同样的条件十分钟内再看用缓存，不重新搜。没有搜索词时不搜。
 */
export function useSearch(account: string, conditions: SearchConditions | null) {
  return useInfiniteQuery({
    queryKey: ["search", account, conditions] as const,
    queryFn: ({ pageParam }) => api.search(account, conditions!, pageParam),
    initialPageParam: 1,
    getNextPageParam: (last) => (last.has_next ? last.page + 1 : undefined),
    enabled: conditions != null,
    staleTime: 10 * 60_000,
    gcTime: 10 * 60_000,
  })
}

// 暂存按用户存，不分账号，所以 key 里没有账号
export const stashKey = ["search-stash"] as const

/** 暂存的商品，最近暂存的在前。只存在本地库，不请求闲鱼 */
export function useStash() {
  return useQuery({ queryKey: stashKey, queryFn: api.stash })
}

const without = (itemId: string) => (items: StashItem[]) =>
  items.filter((it) => it.item_id !== itemId)

const withItem = (item: StashItem) => (items: StashItem[]) =>
  [item, ...without(item.item_id)(items)].sort((a, b) => b.saved_at - a.saved_at)

/**
 * 暂存和移出都先改缓存里的列表（按钮马上变），失败了只撤回这一件，不影响同时点的别的。
 * 列表还没拉下来时不猜，改完重新拉。
 */
function useEditStash() {
  const client = useQueryClient()
  return async (change: (items: StashItem[]) => StashItem[]) => {
    await client.cancelQueries({ queryKey: stashKey })
    if (client.getQueryData(stashKey) === undefined) {
      void client.invalidateQueries({ queryKey: stashKey })
      return
    }
    client.setQueryData<StashItem[]>(stashKey, (old) => old && change(old))
  }
}

export function useStashItem() {
  const edit = useEditStash()
  return useMutation({
    mutationFn: ({ item, keyword }: { item: SearchItem; keyword: string }) =>
      api.stashItem(item, keyword),
    onMutate: ({ item, keyword }) => {
      const now = Date.now() / 1000
      return edit(withItem({ ...item, keyword, saved_at: now, updated_at: now }))
    },
    onSuccess: (saved) => edit(withItem(saved)),
    onError: (_, { item }) => edit(without(item.item_id)),
  })
}

export function useUnstashItem() {
  const edit = useEditStash()
  return useMutation({
    mutationFn: (item: StashItem) => api.unstashItem(item.item_id),
    onMutate: (item) => edit(without(item.item_id)),
    onError: (_, item) => edit(withItem(item)),
  })
}
