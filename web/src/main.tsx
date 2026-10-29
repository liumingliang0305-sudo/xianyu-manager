import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { ThemeProvider } from "next-themes"
import { StrictMode } from "react"
import { createRoot } from "react-dom/client"
import { BrowserRouter } from "react-router"

import { Toaster } from "@/components/ui/sonner"
import { TooltipProvider } from "@/components/ui/tooltip"
import { ApiError } from "@/lib/api"

import App from "./App"
import "./index.css"

// 碰到风控或熔断时重新拉账号列表，页面顶部的熔断提示马上出来
const refreshBlocks = (error: unknown) => {
  if (error instanceof ApiError && ["risk_control", "circuit_open"].includes(error.code)) {
    queryClient.invalidateQueries({ queryKey: ["accounts"] })
  }
}

const queryClient: QueryClient = new QueryClient({
  queryCache: new QueryCache({ onError: refreshBlocks }),
  mutationCache: new MutationCache({ onError: refreshBlocks }),
  defaultOptions: {
    queries: {
      // 商品详情会实时请求闲鱼，切回窗口时不要自动重新拉
      refetchOnWindowFocus: false,
      // 后端明确返回的错误（登录失效、风控等）重试也没用
      retry: (failures, error) => !(error instanceof ApiError && error.status > 0) && failures < 2,
    },
  },
})

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    {/* next-themes 内联的防闪烁脚本只对服务端渲染有用，纯前端页面里不会执行，标成数据块免得 React 报警；
        首屏主题由 index.html 里的脚本先设好 */}
    <ThemeProvider
      attribute="class"
      defaultTheme="system"
      enableSystem
      disableTransitionOnChange
      scriptProps={{ type: "application/json" }}
    >
      <QueryClientProvider client={queryClient}>
        <TooltipProvider>
          <BrowserRouter>
            <App />
          </BrowserRouter>
          <Toaster position="top-center" richColors />
        </TooltipProvider>
      </QueryClientProvider>
    </ThemeProvider>
  </StrictMode>,
)
