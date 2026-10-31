import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect } from "react"

import { api, messageStreamUrl, type Conversation, type ImStatus } from "@/lib/api"

export const messageKeys = {
  conversations: (account: string) => ["conversations", account] as const,
  thread: (account: string, cid: string, limit: number) =>
    ["messages", account, cid, limit] as const,
}

/** 本地库里的会话和连接状态，读本地，不请求闲鱼。新消息靠 useMessageStream 通知刷新。 */
export function useConversations(account: string) {
  return useQuery({
    queryKey: messageKeys.conversations(account),
    queryFn: () => api.conversations(account),
  })
}

/** 一个会话本地最新的 limit 条。limit 变大（往前翻）时先显示原来的，不闪。 */
export function useThread(account: string, cid: string, limit: number) {
  return useQuery({
    queryKey: messageKeys.thread(account, cid, limit),
    queryFn: () => api.messages(account, cid, limit),
    placeholderData: keepPreviousData,
  })
}

/** 向平台拉消息。拉到的消息存进本地库后后台会推通知，这里不用自己刷新。 */
export function useFetchMessages(account: string) {
  return useMutation({
    mutationFn: (v: { cid: string; older: boolean }) => api.fetchMessages(account, v.cid, v.older),
  })
}

export function useReconnectIm(account: string) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: () => api.reconnectIm(account),
    onSuccess: (im) => setIm(client, account, im),
  })
}

function setIm(client: ReturnType<typeof useQueryClient>, account: string, im: ImStatus) {
  client.setQueryData<{ conversations: Conversation[]; im: ImStatus }>(
    messageKeys.conversations(account),
    (old) => (old ? { ...old, im } : old),
  )
}

/** 补拉时一下子来很多条通知，攒一小会儿再刷新 */
const REFRESH_DELAY = 200

/**
 * 订阅后台的消息推送：有新消息、补拉完就刷新会话和消息，连接状态直接写进缓存。
 * 断了浏览器会自己重连，重连上先刷新一次，补上断开期间漏掉的通知。
 */
export function useMessageStream(account: string) {
  const client = useQueryClient()
  useEffect(() => {
    const source = new EventSource(messageStreamUrl(account))
    let timer: ReturnType<typeof setTimeout> | undefined
    const refresh = () => {
      if (timer) return
      timer = setTimeout(() => {
        timer = undefined
        client.invalidateQueries({ queryKey: messageKeys.conversations(account) })
        client.invalidateQueries({ queryKey: ["messages", account] })
      }, REFRESH_DELAY)
    }
    const onStatus = (event: MessageEvent<string>) => {
      const { type: _type, ...im } = JSON.parse(event.data) as ImStatus & { type: string }
      setIm(client, account, im)
    }
    source.addEventListener("open", refresh)
    source.addEventListener("status", onStatus)
    for (const type of ["message", "messages", "conversations"]) {
      source.addEventListener(type, refresh)
    }
    return () => {
      source.close()
      clearTimeout(timer)
    }
  }, [account, client])
}
