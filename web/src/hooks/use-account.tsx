import { useQuery } from "@tanstack/react-query"
import { createContext, useContext, useState, type ReactNode } from "react"

import { api, type Account } from "@/lib/api"

const STORAGE_KEY = "xym.account"

interface AccountContextValue {
  accounts: Account[]
  /** 当前账号 unb；还没加载完或没有账号时是 null */
  account: string | null
  setAccount: (id: string) => void
  isLoading: boolean
  error: unknown
}

const AccountContext = createContext<AccountContextValue | null>(null)

function readStored(): string | null {
  try {
    return localStorage.getItem(STORAGE_KEY)
  } catch {
    return null
  }
}

export function AccountProvider({ children }: { children: ReactNode }) {
  const query = useQuery({ queryKey: ["accounts"], queryFn: api.accounts })
  const [selected, setSelected] = useState(readStored)
  const accounts = query.data ?? []
  const account = accounts.find((a) => a.id === selected)?.id ?? accounts[0]?.id ?? null

  const setAccount = (id: string) => {
    setSelected(id)
    try {
      localStorage.setItem(STORAGE_KEY, id)
    } catch {
      // 隐私模式等情况下存不了，只影响下次打开时默认选哪个账号
    }
  }

  return (
    <AccountContext.Provider
      value={{ accounts, account, setAccount, isLoading: query.isPending, error: query.error }}
    >
      {children}
    </AccountContext.Provider>
  )
}

export function useAccount(): AccountContextValue {
  const value = useContext(AccountContext)
  if (!value) throw new Error("useAccount 必须在 AccountProvider 里用")
  return value
}

/** 页面里用：外层 AppShell 已经保证有账号才渲染页面。 */
export function useCurrentAccount(): string {
  const { account } = useAccount()
  if (!account) throw new Error("当前没有选中的账号")
  return account
}
