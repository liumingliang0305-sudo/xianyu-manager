import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query"

import { api, draftImage, type Draft, type DraftForm } from "@/lib/api"

export const draftKeys = {
  list: (account: string) => ["drafts", account] as const,
  /** 编辑一个已有商品的草稿，不在上面的列表里 */
  edit: (account: string, itemId: string) => ["edit-draft", account, itemId] as const,
  image: (account: string, sha: string) => ["draft-image", account, sha] as const,
  addresses: (account: string) => ["publish-addresses", account] as const,
  places: (keyword: string) => ["publish-places", keyword] as const,
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

/** 常用地址，会请求一次闲鱼；十分钟内不再查。 */
export function useAddresses(account: string) {
  return useQuery({
    queryKey: draftKeys.addresses(account),
    queryFn: () => api.addresses(account),
    staleTime: 10 * 60_000,
  })
}

/** 按名字搜发货地（高德）。同一个词十分钟内不再搜，空的不搜。 */
export function usePlaces(keyword: string) {
  return useQuery({
    queryKey: draftKeys.places(keyword),
    queryFn: () => api.searchPlaces(keyword),
    enabled: keyword.trim() !== "",
    staleTime: 10 * 60_000,
    retry: false,
  })
}

/**
 * 编辑一个已有商品：拿这个商品的编辑草稿，没有就按闲鱼上现在的样子建一个（请求一次闲鱼）。
 * 之后改动都存在草稿里，不再自己重新拉；失败了不自动重试，免得在风控时反复请求。
 */
export function useEditDraft(account: string, itemId: string) {
  return useQuery({
    queryKey: draftKeys.edit(account, itemId),
    queryFn: () => api.editItem(account, itemId),
    staleTime: Infinity,
    retry: false,
    refetchOnWindowFocus: false,
  })
}

/** 接口返回的草稿换进缓存：发布页的列表，或者编辑商品的那一个。 */
export function putDraft(client: QueryClient, account: string, draft: Draft) {
  if (draft.edit_item_id) {
    client.setQueryData(draftKeys.edit(account, draft.edit_item_id), draft)
    return
  }
  client.setQueryData<Draft[]>(draftKeys.list(account), (old) =>
    old?.map((d) => (d.id === draft.id ? draft : d)),
  )
}

function usePutDraft(account: string) {
  const client = useQueryClient()
  return (draft: Draft) => putDraft(client, account, draft)
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

/** 删草稿。编辑商品的草稿删了就是放弃改动，下次编辑重新从闲鱼拉。 */
export function useDeleteDraft(account: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (draft: Draft) => api.deleteDraft(account, draft.id),
    onSuccess: (_data, draft) => {
      if (draft.edit_item_id) {
        client.removeQueries({ queryKey: draftKeys.edit(account, draft.edit_item_id) })
        return
      }
      client.setQueryData<Draft[]>(draftKeys.list(account), (old) =>
        old?.filter((d) => d.id !== draft.id),
      )
    },
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

/**
 * 发布（编辑草稿是提交改动），成功或失败都刷新：失败原因记在草稿上，商品列表和改动记录也会多一条。
 * 编辑草稿提交成功以后就用完了，从缓存里拿掉，下次编辑重新从闲鱼拉。
 */
export function usePublish(account: string, draft: Draft) {
  const client = useQueryClient()
  const put = usePutDraft(account)
  return useMutation({
    mutationFn: (force: boolean = false) => api.publish(account, draft.id, force),
    onSuccess: (done) => {
      if (done.edit_item_id) client.removeQueries({ queryKey: draftKeys.edit(account, done.edit_item_id) })
    },
    onError: () => {
      // 失败原因记在草稿上，编辑草稿不在列表里，单独再取一次
      if (draft.edit_item_id) void api.getDraft(account, draft.id).then(put)
    },
    onSettled: () => {
      client.invalidateQueries({ queryKey: draftKeys.list(account) })
      client.invalidateQueries({ queryKey: ["items", account] })
      client.invalidateQueries({ queryKey: ["item-ops", account] })
    },
  })
}
