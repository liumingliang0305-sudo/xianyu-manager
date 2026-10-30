import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CheckIcon, Loader2Icon, RefreshCwIcon, SmartphoneIcon } from "lucide-react"
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs"
import { Textarea } from "@/components/ui/textarea"
import { useAccount } from "@/hooks/use-account"
import { ApiError, api, errorMessage, type LoginPhase } from "@/lib/api"
import { cn } from "@/lib/utils"

const FINISHED: LoginPhase[] = ["done", "expired", "canceled", "verify", "failed"]

const PHASE_TEXT: Record<LoginPhase, string> = {
  waiting: "打开闲鱼 App，扫一扫上面的二维码",
  scanned: "已扫码，请在手机上确认登录",
  confirmed: "已确认，正在获取登录凭证…",
  done: "登录成功",
  expired: "二维码已过期",
  canceled: "已在手机上取消登录",
  verify: "平台要求额外身份验证",
  failed: "登录失败",
}

function svgUrl(svg: string) {
  return `data:image/svg+xml;charset=utf-8,${encodeURIComponent(svg)}`
}

/**
 * 登录弹窗：扫码登录，或者导入浏览器的登录（扫码登录平台不给开保持登录，浏览器里能开）。
 * 登录成功后切到这个账号、刷新账号列表，自己关掉。
 */
export function LoginDialog({
  open,
  onOpenChange,
}: {
  open: boolean
  onOpenChange: (open: boolean) => void
}) {
  const close = () => onOpenChange(false)
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>登录闲鱼</DialogTitle>
          <DialogDescription>登录凭证只保存在本机，不要外传。</DialogDescription>
        </DialogHeader>
        {/* 切走时扫码面板会卸载，后台跟着取消这张码；切回来换一张新的 */}
        <Tabs defaultValue="qr" className="gap-4">
          <TabsList className="w-full">
            <TabsTrigger value="qr">扫码登录</TabsTrigger>
            <TabsTrigger value="import">导入浏览器登录</TabsTrigger>
          </TabsList>
          <TabsContent value="qr">
            <LoginPanel onDone={close} />
          </TabsContent>
          <TabsContent value="import">
            <ImportPanel onDone={close} />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  )
}

/** 登录成功以后：切到这个账号、刷新账号列表、提示保持登录的情况 */
function useFinishLogin(onDone: () => void) {
  const queryClient = useQueryClient()
  const { setAccount } = useAccount()
  return useCallback(
    (accountId: string, message: string) => {
      setAccount(accountId)
      void queryClient.invalidateQueries({ queryKey: ["accounts"] })
      toast.success("登录成功", { description: message })
      onDone()
    },
    [setAccount, queryClient, onDone],
  )
}

function ImportPanel({ onDone }: { onDone: () => void }) {
  const finish = useFinishLogin(onDone)
  const [cookie, setCookie] = useState("")
  const save = useMutation({
    mutationFn: () => api.importLogin(cookie),
    onSuccess: (result) => finish(result.account_id, result.message),
  })

  let error = ""
  if (save.error) {
    error =
      save.error instanceof ApiError && save.error.code === "auth_required"
        ? "这份 cookie 的登录已经失效，在浏览器里刷新页面后重新复制"
        : errorMessage(save.error)
  }

  return (
    <form
      className="flex flex-col gap-3 pb-2"
      onSubmit={(e) => {
        e.preventDefault()
        save.mutate()
      }}
    >
      <p className="rounded-md bg-muted px-2.5 py-2 text-xs">
        装了浏览器插件“闲鱼管理 · 登录同步”（在项目的 extension 目录）就不用粘贴，
        浏览器里的登录会自动同步过来。下面是没装插件时的办法。
      </p>
      <ol className="list-decimal space-y-1 pl-4 text-xs text-muted-foreground">
        <li>在电脑浏览器登录 goofish.com，头像菜单里打开“保存登录信息”</li>
        <li>打开开发者工具（Mac 按 ⌥⌘I，其他电脑按 F12），切到“网络”，刷新页面</li>
        <li>点第一条请求（www.goofish.com），在“请求标头”里找到 Cookie，复制整个值</li>
      </ol>
      <Textarea
        value={cookie}
        onChange={(e) => setCookie(e.target.value)}
        aria-label="Cookie 请求头"
        aria-invalid={!!error}
        placeholder="unb=…; cookie2=…; havana_lgc_exp=…"
        autoComplete="off"
        spellCheck={false}
        className="h-24 resize-none font-mono text-xs break-all field-sizing-fixed"
      />
      {error && (
        <p role="alert" className="text-xs text-destructive">
          {error}
        </p>
      )}
      <p className="text-xs text-muted-foreground">
        导入后和浏览器共用这次登录：在浏览器里点“退出登录”，这里也会失效。
      </p>
      <Button type="submit" disabled={!cookie.trim() || save.isPending}>
        {save.isPending && <Loader2Icon className="animate-spin" />}
        {save.isPending ? "正在向闲鱼确认…" : "导入"}
      </Button>
    </form>
  )
}

function LoginPanel({ onDone }: { onDone: () => void }) {
  const finish = useFinishLogin(onDone)
  // 取码用查询而不是 mutation：开发模式下 StrictMode 会把挂载跑两遍，查询会合并成一个请求，
  // 不会一下取两张码、前一张马上作废
  const qr = useQuery({
    queryKey: ["login", "qr"],
    queryFn: api.startLogin,
    staleTime: Infinity,
    gcTime: 0,
    retry: false,
  })
  const id = qr.data?.id
  const progress = useQuery({
    queryKey: ["login", "progress", id],
    queryFn: () => api.loginProgress(id!),
    enabled: !!id && !qr.isFetching,
    gcTime: 0,
    refetchInterval: (query) =>
      query.state.status === "error" || FINISHED.includes(query.state.data?.phase ?? "waiting")
        ? false
        : 1500,
  })

  // 关掉弹窗或者换了一张码：后台不用再替这张码轮询
  useEffect(() => {
    if (!id) return
    return () => void api.cancelLogin(id).catch(() => {})
  }, [id])

  const done = progress.data?.phase === "done" ? progress.data : null
  const handled = useRef<string | null>(null)
  useEffect(() => {
    if (!done || handled.current === done.id) return
    handled.current = done.id
    finish(done.account_id, done.message)
  }, [done, finish])

  const phase: LoginPhase = progress.data?.phase ?? "waiting"
  const loading = qr.isFetching
  const broken = !loading && (qr.isError || progress.isError)
  const finished = !loading && (broken || FINISHED.includes(phase))

  let text = PHASE_TEXT[phase]
  let detail = phase === "verify" || phase === "failed" ? progress.data?.message : ""
  if (loading) {
    text = "正在获取二维码…"
    detail = ""
  } else if (qr.isError) {
    text = "获取二维码失败"
    detail = errorMessage(qr.error)
  } else if (progress.isError) {
    text = "二维码已失效"
    detail = errorMessage(progress.error)
  }

  let overlay: ReactNode = null
  if (loading || phase === "confirmed") {
    overlay = <Loader2Icon className="size-8 animate-spin" />
  } else if (finished && phase !== "done") {
    overlay = (
      // 码框不管什么主题都是白底，按钮固定深色
      <Button
        className="bg-neutral-900 text-white hover:bg-neutral-800"
        onClick={() => void qr.refetch()}
      >
        <RefreshCwIcon />
        {qr.isError ? "重试" : "换一张"}
      </Button>
    )
  } else if (phase === "scanned") {
    overlay = <SmartphoneIcon className="size-8" />
  } else if (phase === "done") {
    overlay = <CheckIcon className="size-8 text-emerald-600" />
  }

  return (
    <div className="flex flex-col items-center gap-3 pb-2">
      {/* 二维码要深色码、浅色底才扫得出来，深色主题下也用白底 */}
      <div className="relative size-52 overflow-hidden rounded-xl border bg-white p-2">
        {qr.data && !qr.isError && (
          <img
            src={svgUrl(qr.data.qr_svg)}
            alt="闲鱼登录二维码"
            className={cn("size-full transition-opacity", overlay !== null && "opacity-10")}
          />
        )}
        {overlay && (
          <div className="absolute inset-0 flex items-center justify-center text-neutral-900">
            {overlay}
          </div>
        )}
      </div>
      <p aria-live="polite" className="text-sm font-medium">
        {text}
      </p>
      {detail && <p className="text-center text-xs text-muted-foreground">{detail}</p>}
    </div>
  )
}
