import {
  useInfiniteQuery,
  useMutation,
  useMutationState,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query"
import { toast } from "sonner"

import {
  api,
  errorMessage,
  type SearchConditions,
  type SearchItem,
  type StashItem,
} from "@/lib/api"
import { ExtensionError, fetchItemDetailViaExtension } from "@/lib/extension"

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

/** 暂存的商品，最近暂存的在前。只读本地库，不请求闲鱼 */
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

/** 请求还没回来时先放进列表的样子 */
function pending(item: SearchItem, keyword: string): StashItem {
  const now = Date.now() / 1000
  return {
    ...item,
    keyword,
    description: "",
    images: [],
    browse_count: null,
    collect_count: null,
    status: null,
    status_text: "",
    detail_at: null,
    saved_at: now,
    updated_at: now,
  }
}

/**
 * 暂存。按钮马上变成"已暂存"，失败了撤回、提示。`onStashed`：存好以后（比如接着拿详情）。
 * 回调放在这里不放在 mutate 里：连着点好几件时，mutate 里的回调只有最后一次会被调
 */
export function useStashItem(onStashed?: (item: StashItem) => void) {
  const edit = useEditStash()
  return useMutation({
    mutationFn: ({ item, keyword }: { item: SearchItem; keyword: string }) =>
      api.stashItem(item, keyword),
    onMutate: ({ item, keyword }) => edit(withItem(pending(item, keyword))),
    onSuccess: async (saved) => {
      await edit(withItem(saved))
      onStashed?.(saved)
    },
    onError: async (error, { item }) => {
      await edit(without(item.item_id))
      toast.error(`暂存失败：${errorMessage(error)}`)
    },
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

const detailKey = ["stash-detail"] as const

/**
 * 拿详情：请浏览器插件在 Chrome 里打开这件商品的闲鱼页面拿（一件几秒，插件那边排队），
 * 拿到了存到后台。没拿到的提示原因（要验证时插件已经把那个闲鱼页面切到前面了）
 */
export function useFetchStashDetail() {
  const edit = useEditStash()
  return useMutation({
    mutationKey: detailKey,
    mutationFn: async ({ itemId }: { itemId: string; title: string }) =>
      api.saveStashDetail(itemId, await fetchItemDetailViaExtension(itemId)),
    onSuccess: (item) => edit(withItem(item)),
    onError: (error, { title }) => {
      const reason = error instanceof ExtensionError ? error.message : errorMessage(error)
      toast.error(`「${title.slice(0, 20)}」的详情没拿到：${reason}`, { duration: 8000 })
    },
  })
}

/** 正在拿详情的商品 ID（不管是哪个页面发起的） */
export function useFetchingDetails(): ReadonlySet<string> {
  const ids = useMutationState({
    filters: { mutationKey: detailKey, status: "pending" },
    select: (mutation) => (mutation.state.variables as { itemId: string } | undefined)?.itemId,
  })
  return new Set(ids.filter((id): id is string => Boolean(id)))
}
