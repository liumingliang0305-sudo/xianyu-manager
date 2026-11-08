import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"

import {
  api,
  ApiError,
  errorMessage,
  streamAgentReply,
  type AgentChat,
  type AgentEvent,
  type AgentChatList,
  type AgentMessage,
} from "@/lib/api"

export const agentKeys = {
  list: ["agent-chats"] as const,
  detail: (chatId: number) => ["agent-chats", chatId] as const,
}

export function useAgentChats() {
  return useQuery({ queryKey: agentKeys.list, queryFn: api.agentChats })
}

export function useAgentChat(chatId: number | null) {
  return useQuery({
    queryKey: agentKeys.detail(chatId ?? 0),
    queryFn: () => api.agentChat(chatId!),
    enabled: chatId != null,
  })
}

export function useCreateAgentChat() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.createAgentChat,
    onSuccess: (chat) => {
      client.setQueryData(agentKeys.detail(chat.id), chat)
      // 列表里只放概况，不带消息
      const { messages: _, ...summary } = chat
      client.setQueryData<AgentChatList>(agentKeys.list, (old) =>
        old ? { ...old, chats: [summary, ...old.chats] } : old,
      )
    },
  })
}

export function useDeleteAgentChat() {
  const client = useQueryClient()
  return useMutation({
    mutationFn: api.deleteAgentChat,
    onSuccess: (_, chatId) => {
      client.removeQueries({ queryKey: agentKeys.detail(chatId) })
      client.setQueryData<AgentChatList>(agentKeys.list, (old) =>
        old ? { ...old, chats: old.chats.filter((c) => c.id !== chatId) } : old,
      )
    },
  })
}

/** 正在回的这一轮：卖家刚发的话，和模型已经说出来的部分（还没开口是空串） */
export interface PendingTurn {
  prompt: string
  reply: string
}

/**
 * 在一个对话里发消息、收流式回复。回复完了把这一轮放进对话的缓存、对话挪到列表最前。
 * send 返回出错的原因（这一轮不算，后台也没存，好把话放回输入框），成功或者中途停止是 null。
 * 换对话、离开页面时停止接收。
 */
export function useAgentReply(chatId: number) {
  const client = useQueryClient()
  const [pending, setPending] = useState<PendingTurn | null>(null)
  const abort = useRef<AbortController | null>(null)

  useEffect(() => () => abort.current?.abort(), [chatId])

  const finish = (prompt: string, sentAt: number, done: Extract<AgentEvent, { type: "done" }>) => {
    const turn: AgentMessage[] = [{ role: "user", text: prompt, at: sentAt }]
    if (done.message) turn.push(done.message)
    client.setQueryData<AgentChat>(agentKeys.detail(chatId), (old) =>
      old ? { ...old, title: done.title, updated_at: done.updated_at, messages: [...old.messages, ...turn] } : old,
    )
    client.setQueryData<AgentChatList>(agentKeys.list, (old) => {
      const chat = old?.chats.find((c) => c.id === chatId)
      if (!old || !chat) return old
      const moved = { ...chat, title: done.title, updated_at: done.updated_at }
      return { ...old, chats: [moved, ...old.chats.filter((c) => c.id !== chatId)] }
    })
  }

  const send = async (prompt: string): Promise<string | null> => {
    if (abort.current) return null
    const controller = new AbortController()
    abort.current = controller
    const sentAt = Date.now() / 1000
    setPending({ prompt, reply: "" })
    let outcome: string | null = "回复断了，没有收完，这一条没有存下"
    try {
      await streamAgentReply(chatId, prompt, controller.signal, (event) => {
        if (event.type === "delta") {
          setPending((turn) => (turn ? { ...turn, reply: turn.reply + event.text } : turn))
        } else if (event.type === "done") {
          outcome = null
          finish(prompt, sentAt, event)
        } else {
          outcome = event.message
        }
      })
    } catch (error) {
      if (!(error instanceof ApiError) && controller.signal.aborted) outcome = null
      else outcome = errorMessage(error)
    } finally {
      if (abort.current === controller) abort.current = null
      setPending(null)
    }
    return controller.signal.aborted ? null : outcome
  }

  return { pending, send, busy: pending !== null }
}
