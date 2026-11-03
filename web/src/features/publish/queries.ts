import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { api, draftImage, type Draft, type DraftForm } from "@/lib/api"

export const draftKeys = {
  list: (account: string) => ["drafts", account] as const,
  image: (account: string, sha: string) => ["draft-image", account, sha] as const,
  addresses: (account: string) => ["publish-addresses", account] as const,
}

/** 本地的草稿，没发的在前。读本地，不请求闲鱼。 */
export function useDrafts(account: string) {
  return useQuery({ queryKey: draftKeys.list(account), queryFn: () => api.drafts(account) })
}

/**
 * 草稿里的一张图，返回 blob: 地址。内容按哈希存的，取过一次这一页就一直用这个地址，
 * 不释放：一个草稿最多 9 张，开着页面看过的图留在内存里也不多。
 */
export function useDraftImage(account: string, draftId: number, sha: string | undefined) {
  return useQuery({
    queryKey: draftKeys.image(account, sha ?? ""),
    queryFn: async () => URL.createObjectURL(await draftImage(account, draftId, sha!)),
    enabled: !!sha,
    staleTime: Infinity,
    gcTime: Infinity,
  })
}

/** 可选的发货地，会请求一次闲鱼；十分钟内不再查。 */
export function useAddresses(account: string) {
  return useQuery({
    queryKey: draftKeys.addresses(account),
    queryFn: () => api.addresses(account),
    staleTime: 10 * 60_000,
  })
}

/** 接口返回的草稿换进列表缓存里。 */
function usePutDraft(account: string) {
  const client = useQueryClient()
  return (draft: Draft) =>
    client.setQueryData<Draft[]>(draftKeys.list(account), (old) =>
      old?.map((d) => (d.id === draft.id ? draft : d)),
    )
}

export function useCreateDraft(account: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.createDraft(account),
    onSuccess: (draft) =>
      client.setQueryData<Draft[]>(draftKeys.list(account), (old) => [draft, ...(old ?? [])]),
  })
}

export function useSaveDraft(account: string, draftId: number) {
  const put = usePutDraft(account)
  return useMutation({
    mutationFn: (form: DraftForm) => api.updateDraft(account, draftId, form),
    onSuccess: put,
  })
}

export function useDeleteDraft(account: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (draftId: number) => api.deleteDraft(account, draftId),
    onSuccess: (_data, draftId) =>
      client.setQueryData<Draft[]>(draftKeys.list(account), (old) =>
        old?.filter((d) => d.id !== draftId),
      ),
  })
}

export function useAddImage(account: string, draftId: number) {
  const put = usePutDraft(account)
  return useMutation({
    mutationFn: (file: File) => api.addDraftImage(account, draftId, file),
    onSuccess: put,
  })
}

export function useRemoveImage(account: string, draftId: number) {
  const put = usePutDraft(account)
  return useMutation({
    mutationFn: (sha: string) => api.removeDraftImage(account, draftId, sha),
    onSuccess: put,
  })
}

export function useRecommend(account: string, draftId: number) {
  const put = usePutDraft(account)
  return useMutation({
    mutationFn: () => api.recommend(account, draftId),
    onSuccess: put,
  })
}

export function useChooseLabel(account: string, draftId: number) {
  const put = usePutDraft(account)
  return useMutation({
    mutationFn: (v: { propertyId: string; keys: string[] }) =>
      api.chooseLabel(account, draftId, v.propertyId, v.keys),
    onSuccess: put,
  })
}

/** 发布成功或失败都刷新：失败原因记在草稿上，商品列表和改动记录也会多一条。 */
export function usePublish(account: string, draftId: number) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.publish(account, draftId),
    onSettled: () => {
      client.invalidateQueries({ queryKey: draftKeys.list(account) })
      client.invalidateQueries({ queryKey: ["items", account] })
      client.invalidateQueries({ queryKey: ["item-ops", account] })
    },
  })
}
