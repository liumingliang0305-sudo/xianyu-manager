import {
  CircleCheckIcon,
  EllipsisIcon,
  ExternalLinkIcon,
  EyeIcon,
  Loader2Icon,
  PlusIcon,
  RotateCcwIcon,
  SendIcon,
  Trash2Icon,
} from "lucide-react"
import { useState } from "react"
import { useSearchParams } from "react-router"
import { toast } from "sonner"

import { Callout } from "@/components/callout"
import { CountFilter } from "@/components/count-filter"
import { PageHeader } from "@/components/page-header"
import { EmptyState, LoadError, RowsSkeleton, SearchInput } from "@/components/states"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useUser } from "@/hooks/use-auth"
import { errorMessage, type Feedback, type FeedbackStatus } from "@/lib/api"
import { count, dateTime, timeAgo } from "@/lib/format"

import { FeedbackDetail } from "./feedback-detail"
import { FeedbackDialog } from "./feedback-dialog"
import { FeedbackStatusPill } from "./feedback-status"
import { IssueDialog } from "./issue-dialog"
import {
  KIND_LABEL,
  useDeleteFeedback,
  useFeedbackList,
  useSetFeedbackStatus,
} from "./queries"

type Filter = "all" | FeedbackStatus

/**
 * 问题反馈：普通用户看自己提交的；管理员看所有人的，能标记处理、推成 GitHub issue。
 * 不分闲鱼账号，没有账号也能进。
 */
export function FeedbackPage() {
  const { is_admin: isAdmin } = useUser()
  const list = useFeedbackList(isAdmin ? "all" : "mine")
  const [filter, setFilter] = useState<Filter>("all")
  const [search, setSearch] = useState("")
  const [writing, setWriting] = useState(false)
  const [pushingId, setPushingId] = useState<number | null>(null)
  const [removing, setRemoving] = useState<Feedback | null>(null)
  const remove = useDeleteFeedback()
  const setStatus = useSetFeedbackStatus()
  const [params, setParams] = useSearchParams()
  const rawId = Number(params.get("feedback"))
  const openId = Number.isSafeInteger(rawId) && rawId > 0 ? rawId : null
  const open = (id: number | null) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (id === null) next.delete("feedback")
        else next.set("feedback", String(id))
        return next
      },
      { replace: true },
    )

  const all = list.data?.feedback ?? []
  const github = list.data?.github
  const openCount = all.filter((f) => f.status === "open").length
  const query = search.trim().toLowerCase()
  const visible = all.filter(
    (f) =>
      (filter === "all" || f.status === filter) &&
      (!query ||
        f.title.toLowerCase().includes(query) ||
        f.content.toLowerCase().includes(query) ||
        (isAdmin && f.username.includes(query))),
  )
  const opened = openId === null ? undefined : all.find((f) => f.id === openId)
  const pushing = pushingId === null ? undefined : all.find((f) => f.id === pushingId)

  const toggleStatus = (feedback: Feedback) => {
    const done = feedback.status === "done"
    setStatus.mutate(
      { id: feedback.id, status: done ? "open" : "done" },
      {
        onSuccess: () => toast.success(done ? "已重新打开" : "已标记为处理完"),
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }

  let description
  if (!list.data) description = "用着哪里不对、想要什么功能，都可以写在这里"
  else if (isAdmin)
    description = [
      `${count(openCount)} 条待处理`,
      `共 ${count(all.length)} 条`,
      github?.repo && !github.missing ? `推送到 ${github.repo}` : null,
    ]
      .filter(Boolean)
      .join(" · ")
  else
    description =
      all.length === 0
        ? "用着哪里不对、想要什么功能，都可以写在这里"
        : `你提交的 ${count(all.length)} 条 · ${count(openCount)} 条待处理`

  let body
  if (list.isPending) body = <RowsSkeleton height="h-16" />
  else if (list.isError)
    body = <LoadError what="读取反馈" error={list.error} onRetry={() => void list.refetch()} />
  else if (all.length === 0)
    body = (
      <EmptyState
        title={isAdmin ? "还没有人提交反馈" : "还没有提交过反馈"}
        action={
          <Button variant="outline" onClick={() => setWriting(true)}>
            <PlusIcon aria-hidden />
            反馈问题
          </Button>
        }
      >
        {isAdmin
          ? "大家提交的问题和建议会出现在这里，可以推到 GitHub 跟进。"
          : "提交后管理员会处理，处理进度在这里看。"}
      </EmptyState>
    )
  else if (visible.length === 0)
    body = (
      <EmptyState>
        {query ? `没有带「${search.trim()}」的反馈` : filter === "open" ? "没有待处理的反馈" : "还没有处理完的反馈"}
      </EmptyState>
    )
  else
    body = (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-full">反馈</TableHead>
            <TableHead>种类</TableHead>
            {isAdmin && <TableHead>提交人</TableHead>}
            <TableHead>状态</TableHead>
            <TableHead>GitHub</TableHead>
            <TableHead className="text-right">提交时间</TableHead>
            <TableHead>
              <span className="sr-only">操作</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {visible.map((feedback) => (
            <TableRow
              key={feedback.id}
              className={
                visible.length > 50
                  ? "[content-visibility:auto] [contain-intrinsic-size:auto_72px]"
                  : undefined
              }
            >
              <TableCell className="whitespace-normal">
                <button
                  type="button"
                  onClick={() => open(feedback.id)}
                  className="group grid w-full gap-1 rounded-md text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  <span className="line-clamp-1 font-medium break-all group-hover:underline">
                    {feedback.title}
                  </span>
                  <span className="line-clamp-1 text-[13px] leading-5 break-all text-muted-foreground">
                    {feedback.content}
                  </span>
                </button>
              </TableCell>
              <TableCell className="text-muted-foreground">{KIND_LABEL[feedback.kind]}</TableCell>
              {isAdmin && (
                <TableCell className="max-w-28 truncate text-muted-foreground">
                  {feedback.username}
                </TableCell>
              )}
              <TableCell>
                <FeedbackStatusPill status={feedback.status} isAdmin={isAdmin} />
              </TableCell>
              <TableCell className="tabular-nums">
                {feedback.issue_url ? (
                  <a
                    href={feedback.issue_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 rounded-sm underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
                    title={`在 GitHub 上看 issue #${feedback.issue_number}`}
                  >
                    #{feedback.issue_number}
                    <ExternalLinkIcon className="size-3 text-muted-foreground" aria-hidden />
                  </a>
                ) : (
                  <span className="text-muted-foreground">-</span>
                )}
              </TableCell>
              <TableCell
                className="text-right text-xs tabular-nums text-muted-foreground"
                title={dateTime(feedback.created_at)}
              >
                {timeAgo(feedback.created_at)}
              </TableCell>
              <TableCell>
                <div className="flex items-center justify-end gap-1">
                  {isAdmin && !feedback.issue_url && feedback.status === "open" && (
                    <Button variant="outline" size="sm" onClick={() => setPushingId(feedback.id)}>
                      推到 GitHub
                    </Button>
                  )}
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`${feedback.title} 的更多操作`}
                      >
                        <EllipsisIcon aria-hidden />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onSelect={() => open(feedback.id)}>
                        <EyeIcon aria-hidden />
                        查看详情
                      </DropdownMenuItem>
                      {isAdmin && (
                        <>
                          {!feedback.issue_url && feedback.status === "done" && (
                            <DropdownMenuItem onSelect={() => setPushingId(feedback.id)}>
                              <SendIcon aria-hidden />
                              推到 GitHub
                            </DropdownMenuItem>
                          )}
                          <DropdownMenuItem onSelect={() => toggleStatus(feedback)}>
                            {feedback.status === "done" ? (
                              <RotateCcwIcon aria-hidden />
                            ) : (
                              <CircleCheckIcon aria-hidden />
                            )}
                            {feedback.status === "done" ? "重新打开" : "标记已处理"}
                          </DropdownMenuItem>
                        </>
                      )}
                      <DropdownMenuSeparator />
                      <DropdownMenuItem
                        variant="destructive"
                        onSelect={() => setRemoving(feedback)}
                      >
                        <Trash2Icon aria-hidden />
                        删除
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    )

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="问题反馈"
        description={description}
        actions={
          <Button onClick={() => setWriting(true)}>
            <PlusIcon aria-hidden />
            反馈问题
          </Button>
        }
      />
      {isAdmin && github?.missing && (
        <Callout tone="warning" title="还推不了 GitHub">
          <p>
            {github.missing}。服务器上写在 /etc/xym.env，本机写在 .env，改完重启后台。反馈照样能看、能标记处理。
          </p>
        </Callout>
      )}
      <CountFilter
        label="反馈状态"
        value={filter}
        onChange={setFilter}
        options={[
          { value: "all", label: "全部", count: list.data ? all.length : null },
          { value: "open", label: "待处理", count: list.data ? openCount : null },
          { value: "done", label: "已处理", count: list.data ? all.length - openCount : null },
        ]}
      />
      <Card className="gap-0 overflow-hidden py-0">
        <div className="flex items-center justify-between gap-4 border-b p-4">
          <SearchInput
            className="w-72"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder={isAdmin ? "按标题、内容、提交人筛…" : "按标题、内容筛…"}
            aria-label="筛选反馈"
          />
          {list.data && (query || filter !== "all") && (
            <span className="text-xs text-muted-foreground tabular-nums">
              {count(visible.length)} / {count(all.length)} 条
            </span>
          )}
        </div>
        {body}
      </Card>
      {opened && (
        <FeedbackDetail
          feedback={opened}
          isAdmin={isAdmin}
          onClose={() => open(null)}
          onPush={() => setPushingId(opened.id)}
        />
      )}
      {writing && <FeedbackDialog page="" onClose={() => setWriting(false)} />}
      {pushing && <IssueDialog feedback={pushing} onClose={() => setPushingId(null)} />}
      <AlertDialog
        open={removing !== null}
        onOpenChange={(value) => !value && !remove.isPending && setRemoving(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除这条反馈？</AlertDialogTitle>
            <AlertDialogDescription className="break-words">
              「{removing?.title}」会从后台删除，无法恢复。
              {removing?.issue_url && ` 已经推到 GitHub 的 issue #${removing.issue_number} 不会跟着删。`}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={remove.isPending}>取消</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={remove.isPending}
              onClick={(event) => {
                event.preventDefault()
                if (!removing || remove.isPending) return
                remove.mutate(removing.id, {
                  onSuccess: () => {
                    if (openId === removing.id) open(null)
                    setRemoving(null)
                    toast.success("已删除反馈")
                  },
                  onError: (error) => toast.error(errorMessage(error)),
                })
              }}
            >
              {remove.isPending && <Loader2Icon className="animate-spin" aria-hidden />}删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
