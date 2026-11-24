import {
  BotIcon,
  EllipsisIcon,
  ExternalLinkIcon,
  EyeIcon,
  ImageDownIcon,
  Loader2Icon,
  PlusIcon,
  SquarePenIcon,
  Trash2Icon,
} from "lucide-react"
import { useState } from "react"
import { useNavigate, useSearchParams } from "react-router"
import { toast } from "sonner"

import { PageHeader } from "@/components/page-header"
import { EmptyState, LoadError, RowsSkeleton, SearchInput } from "@/components/states"
import { StatusPill } from "@/components/status-pill"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu"
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table"
import { useCreateAgentChat } from "@/features/agent/queries"
import { useAccount } from "@/hooks/use-account"
import { errorMessage, type SourceSummary } from "@/lib/api"
import { count, dateTime, timeAgo } from "@/lib/format"

import { DeleteSourceDialog } from "./delete-dialog"
import { SourceDraftPicker } from "./draft-picker"
import { ImportSourceDialog } from "./import-dialog"
import { useDraftFromSource, useFetchSourceImages, useSources } from "./queries"
import { SourceDetailSheet } from "./source-detail-sheet"
import { SourcePhoto } from "./source-photo"

export function SourcesPage() {
  const sources = useSources()
  const [search, setSearch] = useState("")
  const [adding, setAdding] = useState(false)
  const [removing, setRemoving] = useState<SourceSummary | null>(null)
  // 正在挑图建草稿的货源
  const [picking, setPicking] = useState<string | null>(null)
  const [params, setParams] = useSearchParams()
  const openId = params.get("source")
  const open = (id: string | null) =>
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous)
        if (id) next.set("source", id)
        else next.delete("source")
        next.delete("media")
        return next
      },
      { replace: true },
    )
  const navigate = useNavigate()
  const createChat = useCreateAgentChat()
  const { account } = useAccount()
  const draft = useDraftFromSource(account)
  const fetchImages = useFetchSourceImages()
  const all = sources.data ?? []
  const text = search.trim().toLowerCase()
  const visible = all.filter(
    (source) =>
      !text ||
      [
        source.title,
        source.author,
        source.platform,
        source.parser_name,
        source.url,
        source.preview,
      ].some((part) => part.toLowerCase().includes(text)),
  )
  const incomplete = all.filter((source) => source.missing_images > 0).length

  const chat = (id: string) => {
    if (createChat.isPending) return
    createChat.mutate(
      { pdd_goods_id: null, stash_item_id: null, source_id: id },
      {
        onSuccess: (result) => navigate(`/agent?c=${result.id}`),
        onError: (error) => toast.error(`创建对话失败：${errorMessage(error)}`),
      },
    )
  }
  const makeDraft = (id: string, images?: string[]) => {
    if (!account || draft.isPending) return
    draft.mutate(
      { id, images },
      {
        onSuccess: (result) => {
          setPicking(null)
          toast.success(
            images
              ? `已建好草稿，描述和选的 ${images.length} 张图已带入，请核对后填写价格`
              : "已建好草稿，描述已带入；货源没有保存下来的图片，请在发布页加图",
          )
          navigate(`/publish?draft=${result.id}`)
        },
        onError: (error) => toast.error(`建草稿失败：${errorMessage(error)}`),
      },
    )
  }
  // 先挑图；一张保存下来的图都没有时没得挑，直接建（草稿先不带图）
  const startDraft = (id: string) => {
    const summary = all.find((source) => source.source_id === id)
    if (summary && summary.image_count === summary.missing_images) makeDraft(id)
    else setPicking(id)
  }
  const refetchImages = (id: string) => {
    if (fetchImages.isPending) return
    fetchImages.mutate(id, {
      onSuccess: (result) =>
        result.missing_images
          ? toast.error(`还有 ${result.missing_images} 张图片未下载，可以稍后重试`)
          : toast.success(`图片已全部保存（${result.image_count} 张）`),
      onError: (error) => toast.error(`下载失败：${errorMessage(error)}`),
    })
  }

  let body
  if (sources.isPending) body = <RowsSkeleton height="h-16" />
  else if (sources.isError)
    body = (
      <LoadError
        what="读取第三方货源"
        error={sources.error}
        onRetry={() => void sources.refetch()}
      />
    )
  else if (all.length === 0)
    body = (
      <EmptyState
        title="还没有第三方货源"
        action={
          <Button variant="outline" onClick={() => setAdding(true)}>
            添加货源链接
          </Button>
        }
      >
        粘贴简篇、秀米、135编辑器、自我游商城等网页链接，自动识别网站和解析方案，保存正文和图片，写文案时可在
        AI 对话中选择。
      </EmptyState>
    )
  else if (visible.length === 0) body = <EmptyState>没有匹配「{search.trim()}」的货源</EmptyState>
  else
    body = (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-full min-w-64">货源</TableHead>
            <TableHead>网站 · 解析方案</TableHead>
            <TableHead className="text-right">正文</TableHead>
            <TableHead className="text-right">图片</TableHead>
            <TableHead>保存时间</TableHead>
            <TableHead>
              <span className="sr-only">操作</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {visible.map((source) => (
            <TableRow key={source.source_id}>
              <TableCell className="whitespace-normal">
                <button
                  type="button"
                  onClick={() => open(source.source_id)}
                  className="group flex w-full min-w-0 items-center gap-3 rounded-md text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  <SourcePhoto sourceId={source.source_id} sha={source.cover_sha} />
                  <span className="min-w-0">
                    <span className="line-clamp-2 font-medium group-hover:underline">
                      {source.title}
                    </span>
                    <span className="mt-0.5 line-clamp-1 text-xs text-muted-foreground">
                      {[source.author, source.host].filter(Boolean).join(" · ")}
                    </span>
                  </span>
                </button>
              </TableCell>
              <TableCell>
                <span className="block text-sm">{source.platform}</span>
                <span className="block text-xs text-muted-foreground">{source.parser_name}</span>
              </TableCell>
              <TableCell className="text-right text-muted-foreground tabular-nums">
                {count(source.content_length)} 字
              </TableCell>
              <TableCell className="text-right tabular-nums">
                <span>{count(source.image_count)} 张</span>
                {source.missing_images > 0 && (
                  <StatusPill tone="warning" className="mt-1">
                    {source.missing_images} 张未下载
                  </StatusPill>
                )}
              </TableCell>
              <TableCell className="text-xs text-muted-foreground">
                <span title={dateTime(source.updated_at)}>{timeAgo(source.updated_at)}</span>
              </TableCell>
              <TableCell>
                <div className="flex items-center justify-end gap-1">
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={createChat.isPending}
                    onClick={() => chat(source.source_id)}
                  >
                    {createChat.isPending &&
                    createChat.variables?.source_id === source.source_id ? (
                      <Loader2Icon className="animate-spin" />
                    ) : (
                      <BotIcon />
                    )}
                    AI 对话
                  </Button>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`${source.title} 的更多操作`}
                      >
                        <EllipsisIcon />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onSelect={() => open(source.source_id)}>
                        <EyeIcon />
                        查看详情
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        disabled={!account || draft.isPending}
                        onSelect={() => startDraft(source.source_id)}
                      >
                        <SquarePenIcon />
                        建发布草稿
                      </DropdownMenuItem>
                      <DropdownMenuItem asChild>
                        <a href={source.url} target="_blank" rel="noreferrer">
                          <ExternalLinkIcon />
                          打开原网页
                        </a>
                      </DropdownMenuItem>
                      {source.missing_images > 0 && (
                        <DropdownMenuItem
                          disabled={fetchImages.isPending}
                          onSelect={() => refetchImages(source.source_id)}
                        >
                          <ImageDownIcon />
                          重新下载图片
                        </DropdownMenuItem>
                      )}
                      <DropdownMenuSeparator />
                      <DropdownMenuItem variant="destructive" onSelect={() => setRemoving(source)}>
                        <Trash2Icon />
                        删除
                      </DropdownMenuItem>
                    </DropdownMenuContent>
                  </DropdownMenu>
                </div>
              </TableCell>
            </TableRow>
          ))}
        </TableBody>
      </Table>
    )

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="第三方货源"
        description={
          all.length
            ? `${count(all.length)} 条货源${incomplete ? ` · ${incomplete} 条图片未下全` : ""} · 最近保存 ${timeAgo(all[0].updated_at)}`
            : "简篇、秀米、135编辑器、自我游商城等网页的正文与图片，名下所有闲鱼账号共用"
        }
        actions={
          <Button onClick={() => setAdding(true)}>
            <PlusIcon />
            添加货源
          </Button>
        }
      />
      <Card className="gap-0 py-0">
        <div className="flex items-center justify-between gap-4 border-b px-4 py-3">
          <SearchInput
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="搜标题、作者、网站或解析方案…"
            aria-label="搜索第三方货源"
            className="w-72"
          />
          <span className="text-xs text-muted-foreground tabular-nums">
            {text ? `${visible.length} / ${all.length} 条` : "正文和图片保存在后台"}
          </span>
        </div>
        {body}
      </Card>
      {adding && (
        <ImportSourceDialog
          onClose={() => setAdding(false)}
          onImported={(id) => {
            setAdding(false)
            open(id)
          }}
        />
      )}
      {openId && (
        <SourceDetailSheet
          key={openId}
          sourceId={openId}
          summary={all.find((source) => source.source_id === openId)}
          onClose={() => open(null)}
          onChat={() => chat(openId)}
          chatting={createChat.isPending}
          onDraft={() => startDraft(openId)}
          drafting={draft.isPending}
          canDraft={!!account}
          onFetchImages={() => refetchImages(openId)}
          fetching={fetchImages.isPending}
        />
      )}
      {picking && (
        <SourceDraftPicker
          sourceId={picking}
          pending={draft.isPending}
          onConfirm={(images) => makeDraft(picking, images)}
          onClose={() => setPicking(null)}
        />
      )}
      {removing && (
        <DeleteSourceDialog
          source={removing}
          onClose={() => setRemoving(null)}
          onDeleted={() => {
            if (openId === removing.source_id) open(null)
          }}
        />
      )}
    </div>
  )
}
