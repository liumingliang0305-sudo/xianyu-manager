import { useMutation, useQueryClient } from "@tanstack/react-query"
import { Loader2Icon, ShieldAlertIcon } from "lucide-react"
import { useEffect, useState } from "react"
import { toast } from "sonner"

import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { useAccount } from "@/hooks/use-account"
import { api, errorMessage } from "@/lib/api"
import { dateTime } from "@/lib/format"

/** 每 30 秒走一次，让剩余时间和到期自动消失跟得上 */
function useNow(): number {
  const [now, setNow] = useState(() => Date.now() / 1000)
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now() / 1000), 30_000)
    return () => clearInterval(timer)
  }, [])
  return now
}

/** 当前账号在风控熔断期时，页面顶部的提示。熔断期间本系统不会替这个账号发任何请求。 */
export function GuardBanner() {
  const { accounts, account } = useAccount()
  const now = useNow()
  const client = useQueryClient()
  const unblock = useMutation({
    mutationFn: () => api.unblock(account!),
    onSuccess: () => {
      toast.success("已解除熔断")
      client.invalidateQueries({ queryKey: ["accounts"] })
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  const block = accounts.find((a) => a.id === account)?.block
  if (!block || block.until <= now) return null
  // now 最多晚 30 秒更新，取整用四舍五入，免得刚触发时显示成 31 分钟
  const minutes = Math.max(1, Math.round((block.until - now) / 60))

  return (
    <div className="mb-6 flex flex-wrap items-start gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-4">
      <ShieldAlertIcon className="mt-0.5 size-5 shrink-0 text-destructive" />
      <div className="min-w-0 flex-1">
        <p className="font-medium">账号触发了闲鱼风控，已暂停所有请求</p>
        <p className="mt-1 text-sm text-muted-foreground">
          {dateTime(block.since)} 起暂停，{minutes} 分钟后自动恢复。先到闲鱼 App
          或网页完成验证（比如滑块），完成后可以提前解除。
        </p>
        <p className="mt-1 truncate text-xs text-muted-foreground" title={block.reason}>
          原因：{block.reason}
        </p>
      </div>
      <AlertDialog>
        <AlertDialogTrigger asChild>
          <Button variant="outline" size="sm" disabled={unblock.isPending}>
            {unblock.isPending && <Loader2Icon className="animate-spin" />}
            已完成验证，解除
          </Button>
        </AlertDialogTrigger>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>提前解除熔断？</AlertDialogTitle>
            <AlertDialogDescription>
              确认已经在闲鱼 App 或网页上完成了验证。还没验证就解除，接着发请求很可能再次触发风控，严重的会被限制账号。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>取消</AlertDialogCancel>
            <AlertDialogAction onClick={() => unblock.mutate()}>解除</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
