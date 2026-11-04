import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query"
import { useEffect } from "react"

import { api, subscribeMessages, type SyncJob } from "@/lib/api"

export const autoSyncKeys = {
  status: (account: string) => ["autosync", account] as const,
}

// 每种同步跑完要刷新的本地列表，和 itemKeys.list、orderKeys.list 一样（不从那边引，免得互相引用）
const DATA_KEYS: Record<SyncJob, (account: string) => readonly unknown[]> = {
  items: (account) => ["items", account],
  sold: (account) => ["orders", account],
  bought: (account) => ["orders", account],
}

/** 自动同步的设置和每种同步上次的结果，读本地，不请求闲鱼。 */
export function useAutoSync(account: string) {
  return useQuery({
    queryKey: autoSyncKeys.status(account),
    queryFn: () => api.autoSync(account),
  })
}

/** 这种同步现在的状态，还没读到是 undefined */
export function useSyncJob(account: string, job: SyncJob) {
  return useAutoSync(account).data?.jobs.find((j) => j.job === job)
}

/**
 * 打开页面时告诉后台：这几种数据旧了就在后台同步一次。返回的状态直接写进缓存，同步完靠推送流刷新。
 * 换了账号、换了要的同步（订单页切卖出的、买到的）时再报一次；后台按上次同步的时间决定跑不跑，报多了也不会多同步。
 */
export function useAutoSyncVisit(account: string, jobs: SyncJob[]) {
  const client = useQueryClient()
  const key = jobs.join(",")
  useEffect(() => {
    api.visitAutoSync(account, key.split(",") as SyncJob[]).then(
      (state) => client.setQueryData(autoSyncKeys.status(account), state),
      () => {}, // 报不上就算了：数据还是本地的，定时同步照样跑
    )
  }, [account, key, client])
}

/** 推送流里的 sync 事件：刷新状态；跑完了的，对应的本地列表也刷新。订单页的 useOrderStream 也转到这里 */
export function onSyncEvent(client: QueryClient, account: string, data: string) {
  void client.invalidateQueries({ queryKey: autoSyncKeys.status(account) })
  const event = JSON.parse(data) as { job: SyncJob; running: boolean }
  if (!event.running) void client.invalidateQueries({ queryKey: DATA_KEYS[event.job](account) })
}

/**
 * 商品页用：订阅后台的推送，同步开始、结束时刷新。断了会自己重连，重连上先刷新一次，补上断开期间漏掉的。
 */
export function useSyncStream(account: string, jobs: SyncJob[]) {
  const client = useQueryClient()
  const key = jobs.join(",")
  useEffect(() => {
    const controller = new AbortController()
    const refresh = () => {
      void client.invalidateQueries({ queryKey: autoSyncKeys.status(account) })
      for (const job of key.split(",") as SyncJob[])
        void client.invalidateQueries({ queryKey: DATA_KEYS[job](account) })
    }
    const onEvent = (type: string, data: string) => {
      if (type === "sync") onSyncEvent(client, account, data)
    }
    void subscribeMessages(account, controller.signal, refresh, onEvent)
    return () => controller.abort()
  }, [account, key, client])
}

export function useConfigureAutoSync(account: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: (v: { job: SyncJob; enabled?: boolean; interval?: number }) =>
      api.configureAutoSync(account, v.job, { enabled: v.enabled, interval: v.interval }),
    onSuccess: (state) => client.setQueryData(autoSyncKeys.status(account), state),
  })
}
