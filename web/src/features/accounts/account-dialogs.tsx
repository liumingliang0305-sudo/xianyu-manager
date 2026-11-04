import { Loader2Icon } from "lucide-react"
import { useState, type FormEvent } from "react"
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
} from "@/components/ui/alert-dialog"
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
import { accountName } from "@/lib/account"
import { errorMessage, type Account } from "@/lib/api"
import { cn } from "@/lib/utils"

import { useRemoveAccount, useSetRemark } from "./queries"

const MAX_REMARK = 20

/** 改备注：给账号起个自己认得的名字（主号、数码号），只存在这个后台里 */
export function RemarkDialog({ account, onClose }: { account: Account; onClose: () => void }) {
  const [remark, setRemark] = useState(account.remark)
  const mutation = useSetRemark()
  const length = Array.from(remark.trim()).length
  const tooLong = length > MAX_REMARK
  const unchanged = remark.trim() === account.remark
  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (tooLong || unchanged || mutation.isPending) return
    mutation.mutate(
      { account: account.id, remark: remark.trim() },
      {
        onSuccess: (updated) => {
          toast.success(updated.remark ? `备注改成了「${updated.remark}」` : "已清掉备注")
          onClose()
        },
        onError: (error) => toast.error(errorMessage(error)),
      },
    )
  }
  return (
    <Dialog open onOpenChange={(open) => !open && !mutation.isPending && onClose()}>
      <DialogContent className="sm:max-w-sm">
        <form onSubmit={onSubmit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>改备注</DialogTitle>
            <DialogDescription>
              {accountName(account)}
              <span className="ml-1.5 font-mono text-xs" translate="no">
                {account.id}
              </span>
            </DialogDescription>
          </DialogHeader>
          <div className="grid gap-1.5">
            <Label htmlFor="account-remark">备注</Label>
            <Input
              id="account-remark"
              value={remark}
              autoFocus
              autoComplete="off"
              placeholder="比如：主号、数码号…"
              aria-invalid={tooLong || undefined}
              aria-describedby="account-remark-hint"
              onChange={(e) => setRemark(e.target.value)}
            />
            <p
              id="account-remark-hint"
              aria-live="polite"
              className={cn("text-xs text-muted-foreground", tooLong && "text-destructive")}
            >
              {tooLong
                ? `最多 ${MAX_REMARK} 个字，现在 ${length} 个`
                : "只存在这个后台里，闲鱼上看不到。留空是清掉备注。"}
            </p>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose} disabled={mutation.isPending}>
              取消
            </Button>
            <Button type="submit" disabled={tooLong || unchanged || mutation.isPending}>
              {mutation.isPending && <Loader2Icon className="animate-spin" />}
              保存
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

/** 移除账号：删后台存的登录凭证、断开消息，本地数据留着 */
export function RemoveAccountDialog({
  account,
  onClose,
}: {
  account: Account
  onClose: () => void
}) {
  const mutation = useRemoveAccount()
  // 要删东西，没有昵称时写完整的 ID，免得删错
  const name = account.profile.nick || account.id
  return (
    <AlertDialog open onOpenChange={(open) => !open && !mutation.isPending && onClose()}>
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>移除账号「{name}」？</AlertDialogTitle>
          <AlertDialogDescription>
            会删掉后台保存的这个账号的登录凭证，断开它的消息连接，账号矩阵和账号菜单里都不再有它。
            本地记下的商品、订单、消息留着，之后重新登录这个账号还能看到。闲鱼上的商品和订单不受影响。
          </AlertDialogDescription>
        </AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={mutation.isPending}>取消</AlertDialogCancel>
          <AlertDialogAction
            variant="destructive"
            disabled={mutation.isPending}
            onClick={(e) => {
              e.preventDefault()
              mutation.mutate(account.id, {
                onSuccess: () => {
                  toast.success(`已移除账号「${name}」`)
                  onClose()
                },
                onError: (error) => toast.error(errorMessage(error)),
              })
            }}
          >
            {mutation.isPending && <Loader2Icon className="animate-spin" />}
            移除
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  )
}
