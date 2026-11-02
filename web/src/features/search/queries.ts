import { useInfiniteQuery } from "@tanstack/react-query"

import { api, type SearchConditions } from "@/lib/api"

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
