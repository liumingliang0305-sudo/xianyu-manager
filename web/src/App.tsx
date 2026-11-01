import { Navigate, Route, Routes } from "react-router"

import { AppShell } from "@/components/app-shell"
import { EmptyState } from "@/components/states"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { LoginPage } from "@/features/auth/login-page"
import { ItemsPage } from "@/features/items/items-page"
import { MessagesPage } from "@/features/messages/messages-page"
import { OrdersPage } from "@/features/orders/orders-page"
import { AccountProvider } from "@/hooks/use-account"
import { useAuth } from "@/hooks/use-auth"
import { checkSession } from "@/lib/auth"

export default function App() {
  const auth = useAuth()
  if (auth.status === "checking") {
    // 打开页面时拿 cookie 换访问令牌，一般一眨眼就好
    return (
      <div className="flex min-h-svh items-center justify-center bg-frame" aria-busy="true">
        <Skeleton className="h-64 w-[360px] rounded-2xl" />
      </div>
    )
  }
  if (auth.status === "offline") {
    return (
      <main className="flex min-h-svh items-center justify-center bg-frame">
        <EmptyState
          title="连不上后台"
          action={<Button onClick={() => void checkSession()}>重试</Button>}
        >
          {auth.message}
        </EmptyState>
      </main>
    )
  }
  if (auth.status === "signed-out") return <LoginPage />
  return (
    // 换了用户整个重新挂载，上一个用户选的账号、页面状态不会带过来
    <AccountProvider key={auth.user.id}>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/items" replace />} />
          <Route path="items" element={<ItemsPage />} />
          <Route path="orders" element={<OrdersPage />} />
          <Route path="messages" element={<MessagesPage />} />
          <Route path="*" element={<Navigate to="/items" replace />} />
        </Route>
      </Routes>
    </AccountProvider>
  )
}
