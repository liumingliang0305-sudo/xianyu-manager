import { useMutation } from "@tanstack/react-query"
import { Loader2Icon } from "lucide-react"
import { useState, type FormEvent } from "react"
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
import { useUser } from "@/hooks/use-auth"
import { api, errorMessage } from "@/lib/api"

const MIN_PASSWORD = 8

/** 改密码。改完别处的登录都退出，这个页面不用重新登录。 */
export function PasswordDialog({ onClose }: { onClose: () => void }) {
  const { username } = useUser()
  const [old, setOld] = useState("")
  const [next, setNext] = useState("")
  const [again, setAgain] = useState("")
  const change = useMutation({
    mutationFn: () => api.changePassword(old, next),
    onSuccess: () => {
      toast.success("密码改好了，别处的登录都已经退出")
      onClose()
    },
  })

  const tooShort = next !== "" && next.length < MIN_PASSWORD
  const mismatch = again !== "" && again !== next
  const canSubmit = old !== "" && next.length >= MIN_PASSWORD && again === next && !change.isPending
  const error = change.error ? errorMessage(change.error) : ""

  const onSubmit = (e: FormEvent) => {
    e.preventDefault()
    if (canSubmit) change.mutate()
  }

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-sm">
        <form onSubmit={onSubmit} className="grid gap-4">
          <DialogHeader>
            <DialogTitle>修改密码</DialogTitle>
            <DialogDescription>改完以后，别的浏览器上的登录都要重新登录。</DialogDescription>
          </DialogHeader>

          {/* 帮密码管理器认出这是哪个账号的密码 */}
          <input type="text" name="username" autoComplete="username" value={username} hidden readOnly />
          <div className="grid gap-1.5">
            <Label htmlFor="old-password">原密码</Label>
            <Input
              id="old-password"
              type="password"
              autoComplete="current-password"
              autoFocus
              value={old}
              onChange={(e) => setOld(e.target.value)}
            />
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="new-password">新密码</Label>
            <Input
              id="new-password"
              type="password"
              autoComplete="new-password"
              value={next}
              aria-invalid={tooShort || undefined}
              aria-describedby="new-password-hint"
              onChange={(e) => setNext(e.target.value)}
            />
            <p
              id="new-password-hint"
              aria-live="polite"
              className={tooShort ? "text-xs text-destructive" : "text-xs text-muted-foreground"}
            >
              至少 {MIN_PASSWORD} 位
            </p>
          </div>
          <div className="grid gap-1.5">
            <Label htmlFor="new-password-again">再输一遍新密码</Label>
            <Input
              id="new-password-again"
              type="password"
              autoComplete="new-password"
              value={again}
              aria-invalid={mismatch || undefined}
              aria-describedby={mismatch ? "new-password-again-error" : undefined}
              onChange={(e) => setAgain(e.target.value)}
            />
            {mismatch && (
              <p id="new-password-again-error" role="alert" className="text-xs text-destructive">
                两次输的不一样
              </p>
            )}
          </div>
          {error && (
            <p role="alert" className="text-xs text-destructive">
              {error}
            </p>
          )}

          <DialogFooter>
            <Button type="button" variant="outline" onClick={onClose}>
              取消
            </Button>
            <Button type="submit" disabled={!canSubmit}>
              {change.isPending && <Loader2Icon className="animate-spin" />}
              改密码
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
