import { StatusPill } from "@/components/status-pill"
import type { FeedbackStatus } from "@/lib/api"

/**
 * 待处理：管理员看是轮到自己做（action），提交人看是在等管理员（warning）。已处理是成了（positive）。
 */
export function FeedbackStatusPill({
  status,
  isAdmin,
}: {
  status: FeedbackStatus
  isAdmin: boolean
}) {
  if (status === "done") return <StatusPill tone="positive">已处理</StatusPill>
  return <StatusPill tone={isAdmin ? "action" : "warning"}>待处理</StatusPill>
}
