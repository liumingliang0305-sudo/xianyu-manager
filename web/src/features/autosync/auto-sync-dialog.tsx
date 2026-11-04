import { ChevronDownIcon, Loader2Icon, TimerIcon } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { Callout } from "@/components/callout"
import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { Switch } from "@/components/ui/switch"
import {
  errorMessage,
  type AutoSyncState,
  type SyncJob,
  type SyncJobStatus,
  type SyncTrigger,
} from "@/lib/api"
import { shortTime } from "@/lib/format"
import { cn } from "@/lib/utils"

import { useAutoSync, useConfigureAutoSync } from "./queries"

const JOB_LABEL: Record<SyncJob, string> = {
  items: "商品",
  sold: "卖出的订单",
  bought: "买到的订单",
}

const JOB_HINT: Record<SyncJob, string> = {
  items: "主页上的商品列表。卖出的订单有变化（交易消息）时也同步一次；浏览数、收藏数只在手动同步时拉",
  sold: "逐个刷新还没结束的卖出订单。新订单靠交易消息自动记",
  bought: "闲鱼「我买到的」最新的 3 页，更早的在订单页手动同步",
}

const TRIGGER_LABEL: Record<SyncTrigger, string> = {
  schedule: "定时",
  visit: "打开页面时",
  message: "交易消息",
  manual: "手动",
}

// 上次结果里值得说的几项。商品的 updated 是全部商品、订单的 updated 是刷新了几笔，不说
const RESULT_LABEL: [string, string][] = [
  ["added", "新增"],
  ["changed", "状态有变化"],
  ["gone", "不在主页"],
  ["failed", "拉取失败"],
]

const every = (seconds: number) => `每 ${Math.round(seconds / 3600)} 小时`

function resultText(result: Record<string, number>) {
  const parts = RESULT_LABEL.filter(([key]) => result[key]).map(
    ([key, label]) => `${label} ${result[key]}`,
  )
  return parts.length ? parts.join("，") : "没有变化"
}

/** 下次定时同步：到期了（或者从没跑过）是马上，后台一分钟内就会跑 */
function nextText(at: number | null) {
  return at && at > Date.now() / 1000 ? `下次约 ${shortTime(at)}` : "下次：马上"
}

/** 一种同步上次怎么样、下次什么时候 */
function JobTimes({ status, scheduled }: { status: SyncJobStatus; scheduled: boolean }) {
  let last
  if (status.running) last = <span>正在同步…</span>
  else if (!status.last_run_at) last = <span>还没同步过</span>
  else {
    const when = `${shortTime(status.last_run_at)}${status.last_trigger ? ` ${TRIGGER_LABEL[status.last_trigger]}` : ""}`
    last = status.last_error ? (
      <span className="text-negative-ink">
        上次 {when}同步失败：{status.last_error}
      </span>
    ) : (
      <span>
        上次 {when}同步，{resultText(status.last_result)}
      </span>
    )
  }
  const next = scheduled && !status.running ? nextText(status.next_run_at) : null
  return (
    <p className="min-w-0 text-xs text-muted-foreground tabular-nums">
      {last}
      {next && <span> · {next}</span>}
    </p>
  )
}

function JobRow({
  account,
  status,
  state,
}: {
  account: string
  status: SyncJobStatus
  state: AutoSyncState
}) {
  const configure = useConfigureAutoSync(account)
  const change = (body: { enabled?: boolean; interval?: number }) =>
    configure.mutate(
      { job: status.job, ...body },
      { onError: (error) => toast.error(errorMessage(error)) },
    )
  const id = `auto-sync-${status.job}`
  return (
    <div className="flex flex-col gap-2 px-4 py-3">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <Label htmlFor={id} className="font-medium">
            {JOB_LABEL[status.job]}
          </Label>
          <p className="mt-0.5 text-xs text-muted-foreground">{JOB_HINT[status.job]}</p>
        </div>
        <Switch
          id={id}
          checked={status.enabled}
          disabled={configure.isPending}
          onCheckedChange={(enabled) => change({ enabled })}
          className="mt-0.5"
        />
      </div>
      <div className="flex items-center justify-between gap-4">
        <JobTimes status={status} scheduled={state.active && !state.paused && status.enabled} />
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="xs"
              disabled={!status.enabled || configure.isPending}
              aria-label={`${JOB_LABEL[status.job]}定时同步的间隔`}
              className="shrink-0 text-muted-foreground tabular-nums"
            >
              定时 {every(status.interval)}
              <ChevronDownIcon data-icon="inline-end" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuRadioGroup
              value={String(status.interval)}
              onValueChange={(value) => change({ interval: Number(value) })}
            >
              {state.intervals.map((seconds) => (
                <DropdownMenuRadioItem key={seconds} value={String(seconds)}>
                  {every(seconds)}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  )
}

function AutoSyncDialog({ account, onClose }: { account: string; onClose: () => void }) {
  const state = useAutoSync(account)
  let body
  if (state.isPending) body = <Skeleton className="h-48 w-full" />
  else if (state.isError)
    body = (
      <p role="alert" className="text-sm text-destructive">
        读取失败：{errorMessage(state.error)}
      </p>
    )
  else
    body = (
      <>
        {!state.data.active && (
          <Callout tone="warning" title="后台没开自动同步">
            只有用 xym serve 启动的后台会自动同步，现在只能在页面上手动同步。
          </Callout>
        )}
        {state.data.active && state.data.paused && (
          <Callout tone="warning" title="自动同步暂停了">
            {state.data.paused}
          </Callout>
        )}
        <div className="divide-y rounded-lg border">
          {state.data.jobs.map((status) => (
            <JobRow key={status.job} account={account} status={status} state={state.data} />
          ))}
        </div>
      </>
    )
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle>自动同步</DialogTitle>
          <DialogDescription>
            交易消息来了、打开页面时数据超过 15 分钟没更新、到了定时的时间，后台都会同步这个账号。
            手动同步随时可以点，点过以后定时的往后顺延。
          </DialogDescription>
        </DialogHeader>
        {body}
      </DialogContent>
    </Dialog>
  )
}

/** 标题行里的入口：说清楚这一页的数据现在是不是自动同步，点开改设置 */
export function AutoSyncButton({ account, job }: { account: string; job: SyncJob }) {
  const [open, setOpen] = useState(false)
  const state = useAutoSync(account).data
  const status = state?.jobs.find((j) => j.job === job)
  let label = "自动同步"
  let warn = false
  if (state && status) {
    if (!state.active) label = "自动同步：未开"
    else if (!status.enabled) label = "自动同步：关"
    else if (state.paused) {
      label = "自动同步：暂停"
      warn = true
    } else label = `自动同步：${every(status.interval)}`
  }
  return (
    <>
      <Button
        variant="ghost"
        onClick={() => setOpen(true)}
        className={cn("text-muted-foreground tabular-nums", warn && "text-warning-ink")}
      >
        {status?.running ? <Loader2Icon className="animate-spin" /> : <TimerIcon />}
        {label}
      </Button>
      {open && <AutoSyncDialog account={account} onClose={() => setOpen(false)} />}
    </>
  )
}
