import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import {
  api,
  type Feedback,
  type FeedbackKind,
  type FeedbackList,
  type FeedbackStatus,
} from "@/lib/api"

export const KIND_LABEL: Record<FeedbackKind, string> = {
  bug: "问题",
  idea: "建议",
  other: "其他",
}

export type FeedbackScope = "mine" | "all"

const feedbackKeys = {
  all: ["feedback"] as const,
  list: (scope: FeedbackScope) => ["feedback", scope] as const,
  draft: (id: number | null) => ["feedback", "issue-draft", id] as const,
}

/** 管理员看所有人的（scope=all），别人看自己提交的 */
export function useFeedbackList(scope: FeedbackScope) {
  return useQuery({
    queryKey: feedbackKeys.list(scope),
    queryFn: () => api.feedbackList(scope),
    refetchOnWindowFocus: true,
  })
}

/** 一条反馈改了（改状态、推到 GitHub）：两份列表里都换成新的 */
function useReplaceFeedback() {
  const client = useQueryClient()
  return (updated: Feedback) => {
    client.setQueriesData<FeedbackList>({ queryKey: feedbackKeys.all }, (old) =>
      old && Array.isArray(old.feedback)
        ? { ...old, feedback: old.feedback.map((f) => (f.id === updated.id ? updated : f)) }
        : old,
    )
  }
}

export function useCreateFeedback() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.createFeedback,
    onSuccess: () => void client.invalidateQueries({ queryKey: feedbackKeys.all }),
  })
}

export function useDeleteFeedback() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.deleteFeedback,
    onSuccess: () => void client.invalidateQueries({ queryKey: feedbackKeys.all }),
  })
}

export function useSetFeedbackStatus() {
  const replace = useReplaceFeedback()
  return useMutation({
    mutationFn: ({ id, status }: { id: number; status: FeedbackStatus }) =>
      api.setFeedbackStatus(id, status),
    onSuccess: replace,
  })
}

export function useIssueDraft(id: number | null) {
  return useQuery({
    queryKey: feedbackKeys.draft(id),
    queryFn: () => api.feedbackIssueDraft(id!),
    enabled: id !== null,
    // 每次打开都按最新的反馈内容重新生成
    gcTime: 0,
  })
}

export function usePushIssue(id: number) {
  const replace = useReplaceFeedback()
  return useMutation({
    mutationFn: (body: { title: string; body: string }) => api.pushFeedbackIssue(id, body),
    onSuccess: replace,
  })
}
