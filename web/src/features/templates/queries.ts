import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { agentKeys } from "@/features/agent/queries"
import { api, type PromptTemplateInput } from "@/lib/api"

const templateKeys = {
  list: ["prompt-templates"] as const,
  detail: (id: number | null) => ["prompt-templates", id] as const,
}

export function usePromptTemplates() {
  return useQuery({
    queryKey: templateKeys.list,
    queryFn: api.promptTemplates,
    refetchOnWindowFocus: true,
  })
}

export function usePromptTemplate(id: number | null) {
  return useQuery({
    queryKey: templateKeys.detail(id),
    queryFn: () => api.promptTemplate(id!),
    enabled: id !== null,
  })
}

export function useSavePromptTemplate(id: number | null) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (body: PromptTemplateInput) =>
      id === null ? api.createPromptTemplate(body) : api.updatePromptTemplate(id, body),
    onSuccess: (template) => {
      client.setQueryData(templateKeys.detail(template.id), template)
      void client.invalidateQueries({ queryKey: templateKeys.list, exact: true })
      // AI 对话列表里显示带的模板名字
      void client.invalidateQueries({ queryKey: agentKeys.list, exact: true })
    },
  })
}

export function useDeletePromptTemplate() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.deletePromptTemplate,
    onSuccess: (_, id) => {
      client.removeQueries({ queryKey: templateKeys.detail(id) })
      void client.invalidateQueries({ queryKey: templateKeys.list, exact: true })
      void client.invalidateQueries({ queryKey: agentKeys.list, exact: true })
    },
  })
}
