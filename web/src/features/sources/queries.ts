import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { agentKeys } from "@/features/agent/queries"
import { draftKeys } from "@/features/publish/queries"
import { api, sourceImage, type ThirdPartySource } from "@/lib/api"

export const sourceKeys = {
  list: ["third-party-sources"] as const,
  detail: (id: string) => ["third-party-sources", id] as const,
  image: (id: string, sha: string | null) => ["source-image", id, sha] as const,
  parsers: ["source-parsers"] as const,
  detect: (url: string) => ["source-detect", url] as const,
}

/** 解析方案列表是写死在后台的，取一次就够 */
export function useSourceParsers() {
  return useQuery({ queryKey: sourceKeys.parsers, queryFn: api.sourceParsers, staleTime: Infinity })
}

/** 识别链接：短链接要跟一次跳转，结果几分钟内复用；识别失败不自动重试 */
export function useDetectSource(url: string) {
  return useQuery({
    queryKey: sourceKeys.detect(url),
    queryFn: () => api.detectSource(url),
    enabled: !!url,
    staleTime: 5 * 60_000,
    retry: false,
  })
}

export function useSources() {
  return useQuery({ queryKey: sourceKeys.list, queryFn: api.sources, refetchOnWindowFocus: true })
}

export function useSource(id: string) {
  return useQuery({ queryKey: sourceKeys.detail(id), queryFn: () => api.source(id) })
}

export function useSourceImage(id: string, sha: string | null, enabled: boolean) {
  return useQuery({
    queryKey: sourceKeys.image(id, sha),
    queryFn: () => sourceImage(id, sha!),
    enabled: !!sha && enabled,
    staleTime: Infinity,
  })
}

function usePutSource() {
  const client = useQueryClient()
  return (source: ThirdPartySource) => {
    client.setQueryData(sourceKeys.detail(source.source_id), source)
    void client.invalidateQueries({ queryKey: sourceKeys.list, exact: true })
    void client.invalidateQueries({ queryKey: agentKeys.list })
  }
}

export function useImportSource() {
  const put = usePutSource()
  return useMutation({ mutationFn: api.importSource, onSuccess: (result) => put(result.source) })
}

export function useFetchSourceImages() {
  const put = usePutSource()
  return useMutation({ mutationFn: api.fetchSourceImages, onSuccess: put })
}

export function useDeleteSource() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.deleteSource,
    onSuccess: (_, id) => {
      client.removeQueries({ queryKey: sourceKeys.detail(id) })
      client.removeQueries({ queryKey: ["source-image", id] })
      void client.invalidateQueries({ queryKey: sourceKeys.list, exact: true })
      void client.invalidateQueries({ queryKey: agentKeys.list })
    },
  })
}

/** 照第三方货源建发布草稿，images 是挑的图（sha，按顺序）；货源没有下载下来的图时不传 */
export function useDraftFromSource(account: string | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ id, images }: { id: string; images?: string[] }) =>
      api.draftFromSource(account!, id, images),
    onSuccess: () => {
      if (account) void client.invalidateQueries({ queryKey: draftKeys.list(account) })
    },
  })
}
