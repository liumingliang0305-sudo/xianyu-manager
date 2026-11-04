import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"

import { api, type Account } from "@/lib/api"

/**
 * 账号矩阵页用的账号列表：和账号菜单是同一份缓存，开着这页时每分钟刷新一次，
 * 待发货、未读这些数跟得上。后端记下的登录态还能用就不请求闲鱼。
 */
export function useAccountsLive() {
  return useQuery({
    queryKey: ["accounts"],
    queryFn: api.accounts,
    refetchOnMount: "always",
    refetchInterval: 60_000,
  })
}

/** 把接口返回的一个账号换进列表缓存里 */
function useReplaceAccount() {
  const client = useQueryClient()
  return (updated: Account) =>
    client.setQueryData<Account[]>(["accounts"], (old) =>
      old?.map((a) => (a.id === updated.id ? updated : a)),
    )
}

export function useCheckAccount() {
  const replace = useReplaceAccount()
  return useMutation({ mutationFn: api.checkAccount, onSuccess: replace })
}

export function useSetRemark() {
  const replace = useReplaceAccount()
  return useMutation({
    mutationFn: ({ account, remark }: { account: string; remark: string }) =>
      api.setRemark(account, remark),
    onSuccess: replace,
  })
}

export function useRemoveAccount() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.removeAccount,
    onSuccess: (_result, account) =>
      client.setQueryData<Account[]>(["accounts"], (old) => old?.filter((a) => a.id !== account)),
  })
}
