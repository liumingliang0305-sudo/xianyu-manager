import { Navigate, Route, Routes } from "react-router"

import { AppShell } from "@/components/app-shell"
import { ItemsPage } from "@/features/items/items-page"
import { OrdersPage } from "@/features/orders/orders-page"
import { AccountProvider } from "@/hooks/use-account"

export default function App() {
  return (
    <AccountProvider>
      <Routes>
        <Route element={<AppShell />}>
          <Route index element={<Navigate to="/items" replace />} />
          <Route path="items" element={<ItemsPage />} />
          <Route path="orders" element={<OrdersPage />} />
          <Route path="*" element={<Navigate to="/items" replace />} />
        </Route>
      </Routes>
    </AccountProvider>
  )
}
