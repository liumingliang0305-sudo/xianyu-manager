import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { CopyIcon, Loader2Icon, TriangleAlertIcon } from "lucide-react"
import { useState } from "react"
import { toast } from "sonner"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { Skeleton } from "@/components/ui/skeleton"
import { api, errorMessage } from "@/lib/api"
import { dateTime } from "@/lib/format"

const KEY = ["plugin-token"]

/**
 * 浏览器插件令牌：登录同步插件推登录时带上，证明是这个用户推的。只能用来推登录，推过来的账号归这个用户。
 * 令牌只在生成时显示一次，库里只存哈希；忘了就重新生成，旧的作废。
 */
export function PluginTokenDialog({ onClose }: { onClose: () => void }) {
  const queryClient = useQueryClient()
  const info = useQuery({ queryKey: KEY, queryFn: api.pluginToken })
  const [token, setToken] = useState<string | null>(null)
  const create = useMutation({
    mutationFn: api.newPluginToken,
    onSuccess: (result) => {
      setToken(result.token)
      queryClient.setQueryData(KEY, { created_at: result.created_at })
    },
    onError: (error) => toast.error(errorMessage(error)),
  })
  const drop = useMutation({
    mutationFn: api.dropPluginToken,
    onSuccess: () => {
      setToken(null)
      queryClient.setQueryData(KEY, { created_at: null })
      toast.success("插件令牌已作废，插件推不过来了")
    },
    onError: (error) => toast.error(errorMessage(error)),
  })

  const createdAt = info.data?.created_at ?? null
  const copy = async () => {
    if (!token) return
    try {
      await navigator.clipboard.writeText(token)
      toast.success("已复制")
    } catch {
      toast.error("复制失败，手动选中复制")
    }
  }

  let status
  if (token) {
    status = (
      <div className="grid gap-1.5">
        <Label htmlFor="plugin-token">插件令牌</Label>
        <div className="flex gap-2">
          <Input
            id="plugin-token"
            value={token}
            readOnly
            translate="no"
            spellCheck={false}
            onFocus={(e) => e.target.select()}
            className="font-mono text-xs"
          />
          <Button type="button" variant="outline" size="icon" onClick={() => void copy()} aria-label="复制插件令牌">
            <CopyIcon />
          </Button>
        </div>
        <div
          role="status"
          className="mt-1 flex gap-2 rounded-lg border border-warning-line bg-warning-soft p-3 text-xs text-warning-ink"
        >
          <TriangleAlertIcon className="size-4 shrink-0" />
          <p>只显示这一次，关掉就看不到了。复制到插件弹窗的「插件令牌」里。</p>
        </div>
      </div>
    )
  } else if (info.isPending) {
    status = <Skeleton className="h-5 w-48" />
  } else if (info.isError) {
    status = (
      <p role="alert" className="text-sm text-destructive">
        读取失败：{errorMessage(info.error)}
      </p>
    )
  } else {
    status = (
      <p className="text-sm">
        {createdAt
          ? `${dateTime(createdAt)} 生成过一个。令牌只在生成时显示，忘了就重新生成，旧的会作废。`
          : "还没有生成过。"}
      </p>
    )
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>浏览器插件令牌</DialogTitle>
          <DialogDescription>
            登录同步插件把浏览器里的闲鱼登录推过来时用它。只能用来推登录，推过来的账号归到你名下。
          </DialogDescription>
        </DialogHeader>
        {status}
        <DialogFooter>
          {createdAt && !token && (
            <Button
              type="button"
              variant="outline"
              disabled={drop.isPending}
              onClick={() => drop.mutate()}
              className="text-destructive"
            >
              作废
            </Button>
          )}
          {token ? (
            <Button type="button" onClick={onClose}>
              完成
            </Button>
          ) : (
            <Button
              type="button"
              disabled={info.isPending || create.isPending}
              onClick={() => create.mutate()}
            >
              {create.isPending && <Loader2Icon className="animate-spin" />}
              {createdAt ? "重新生成" : "生成令牌"}
            </Button>
          )}
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
