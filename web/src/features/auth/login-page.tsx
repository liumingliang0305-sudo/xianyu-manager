import { useMutation } from "@tanstack/react-query"
import { Loader2Icon } from "lucide-react"
import { useState } from "react"

import { Brand } from "@/components/brand"
import { Button } from "@/components/ui/button"
import { Input } from "@/components/ui/input"
import { Label } from "@/components/ui/label"
import { api, errorMessage } from "@/lib/api"

/**
 * 登录页：用户名和密码。没有注册入口，用户由管理员在命令行里建（xym users add）。
 * 登录成功后留在原来的网址上，直接进要去的那一页。
 */
export function LoginPage() {
  const [username, setUsername] = useState("")
  const [password, setPassword] = useState("")
  const signIn = useMutation({ mutationFn: () => api.signIn(username, password) })
  const error = signIn.error ? errorMessage(signIn.error) : ""

  return (
    <main className="flex min-h-svh flex-col items-center justify-center gap-4 bg-frame px-4 py-10">
      <div className="w-full max-w-[360px] rounded-2xl border bg-background px-8 pt-8 pb-7">
        <Brand />
        <h1 className="mt-6 text-[22px] font-semibold">登录</h1>
        <p className="mt-1 text-[13px] text-muted-foreground">用管理员给你建的用户名和密码登录。</p>
        <form
          className="mt-6 flex flex-col gap-4"
          onSubmit={(e) => {
            e.preventDefault()
            signIn.mutate()
          }}
        >
          <div className="flex flex-col gap-2">
            <Label htmlFor="login-username">用户名</Label>
            <Input
              id="login-username"
              name="username"
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              autoComplete="username"
              autoCapitalize="none"
              spellCheck={false}
              autoFocus
              required
              className="h-9"
            />
          </div>
          <div className="flex flex-col gap-2">
            <Label htmlFor="login-password">密码</Label>
            <Input
              id="login-password"
              name="password"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              autoComplete="current-password"
              aria-invalid={!!error}
              aria-describedby={error ? "login-error" : undefined}
              required
              className="h-9"
            />
          </div>
          {error && (
            <p id="login-error" role="alert" className="text-xs text-destructive">
              {error}
            </p>
          )}
          <Button type="submit" className="mt-1 h-9" disabled={signIn.isPending}>
            {signIn.isPending && <Loader2Icon className="animate-spin" />}
            {signIn.isPending ? "正在登录…" : "登录"}
          </Button>
        </form>
      </div>
      <p className="text-xs text-muted-foreground">
        还没有账号？找管理员用 <code className="font-mono">xym users add</code> 建一个。
      </p>
    </main>
  )
}
