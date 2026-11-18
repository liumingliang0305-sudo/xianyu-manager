import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import {
  api,
  feedbackImage,
  type Feedback,
  type FeedbackKind,
  type FeedbackList,
  type FeedbackStatus,
  type IssueInput,
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
    mutationFn: (body: IssueInput) => api.pushFeedbackIssue(id, body),
    onSuccess: replace,
  })
}

/**
 * 反馈里的一张截图，返回 blob: 地址。内容按哈希存的不会变，取过一次就一直用这个地址，
 * 不释放：一条反馈最多 6 张，看过的留在内存里也不多。不挂在 ["feedback"] 下面，
 * 列表刷新时不跟着重取。
 */
export function useFeedbackImage(feedbackId: number, sha: string) {
  return useQuery({
    queryKey: ["feedback-image", feedbackId, sha],
    queryFn: async () => URL.createObjectURL(await feedbackImage(feedbackId, sha)),
    staleTime: Infinity,
    gcTime: Infinity,
  })
}
