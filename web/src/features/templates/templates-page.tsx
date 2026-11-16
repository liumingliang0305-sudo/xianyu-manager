import { EllipsisIcon, EyeIcon, Loader2Icon, PlusIcon, Trash2Icon } from "lucide-react"
import { useState } from "react"
import { useSearchParams } from "react-router"
import { toast } from "sonner"

import { PageHeader } from "@/components/page-header"
import { EmptyState, LoadError, RowsSkeleton, SearchInput } from "@/components/states"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog"
import { Button } from "@/components/ui/button"
import { Card } from "@/components/ui/card"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
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
import { errorMessage, type PromptTemplateSummary } from "@/lib/api"
import { count, dateTime, timeAgo } from "@/lib/format"

import { useDeletePromptTemplate, usePromptTemplates } from "./queries"
import { TemplateDetail } from "./template-detail"
import { TemplateEditor } from "./template-editor"

export function TemplatesPage() {
  const templates = usePromptTemplates()
  const [search, setSearch] = useState("")
  const [editing, setEditing] = useState<number | "new" | null>(null)
  const [removing, setRemoving] = useState<PromptTemplateSummary | null>(null)
  const remove = useDeletePromptTemplate()
  const [params, setParams] = useSearchParams()
  const rawId = Number(params.get("template"))
  const openId = Number.isSafeInteger(rawId) && rawId > 0 ? rawId : null
  const open = (id: number | null) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev)
        if (id === null) next.delete("template")
        else next.set("template", String(id))
        return next
      },
      { replace: true },
    )
  const all = templates.data ?? []
  const query = search.trim().toLowerCase()
  const visible = all.filter((template) => template.name.toLowerCase().includes(query))

  let body
  if (templates.isPending) body = <RowsSkeleton height="h-16" />
  else if (templates.isError)
    body = (
      <LoadError what="读取模板" error={templates.error} onRetry={() => void templates.refetch()} />
    )
  else if (all.length === 0)
    body = (
      <EmptyState title="还没有模板">把常用的文案结构、写作要求和参考示例保存在这里。</EmptyState>
    )
  else if (visible.length === 0)
    body = <EmptyState>没有名称里带「{search.trim()}」的模板</EmptyState>
  else
    body = (
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead className="w-full">模板</TableHead>
            <TableHead className="text-right">字数</TableHead>
            <TableHead className="text-right">最近修改</TableHead>
            <TableHead>
              <span className="sr-only">操作</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody>
          {visible.map((template) => (
            <TableRow
              key={template.id}
              className={
                visible.length > 50
                  ? "[content-visibility:auto] [contain-intrinsic-size:auto_84px]"
                  : undefined
              }
            >
              <TableCell className="whitespace-normal">
                <button
                  type="button"
                  onClick={() => open(template.id)}
                  className="group grid w-full gap-1 rounded-md text-left outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
                >
                  <span className="line-clamp-1 font-medium break-all group-hover:underline">
                    {template.name}
                  </span>
                  <span className="line-clamp-2 text-[13px] leading-5 break-all text-muted-foreground">
                    {template.preview}
                  </span>
                </button>
              </TableCell>
              <TableCell className="text-right tabular-nums text-muted-foreground">
                {count(template.content_length)}
              </TableCell>
              <TableCell
                className="text-right text-xs tabular-nums text-muted-foreground"
                title={dateTime(template.updated_at)}
              >
                {timeAgo(template.updated_at)}
              </TableCell>
              <TableCell>
                <div className="flex items-center justify-end gap-1">
                  <Button variant="outline" size="sm" onClick={() => setEditing(template.id)}>
                    编辑
                  </Button>
                  <DropdownMenu>
                    <DropdownMenuTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label={`${template.name} 的更多操作`}
                      >
                        <EllipsisIcon aria-hidden />
                      </Button>
                    </DropdownMenuTrigger>
                    <DropdownMenuContent align="end">
                      <DropdownMenuItem onSelect={() => open(template.id)}>
                        <EyeIcon aria-hidden />
                        查看内容
                      </DropdownMenuItem>
                      <DropdownMenuItem
                        variant="destructive"
                        onSelect={() => setRemoving(template)}
                      >
                        <Trash2Icon aria-hidden />
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
        title="模板管理"
        description={
          templates.data
            ? `${count(all.length)} 份模板${all[0] ? ` · 最近修改 ${timeAgo(all[0].updated_at)}` : ""}`
            : "常用的文案结构和写作要求"
        }
        actions={
          <Button onClick={() => setEditing("new")}>
            <PlusIcon aria-hidden />
            新建模板
          </Button>
        }
      />
      <Card className="gap-0 overflow-hidden py-0">
        <div className="flex items-center justify-between gap-4 border-b p-4">
          <SearchInput
            className="w-72"
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="按模板名称筛…"
            aria-label="筛选模板名称"
          />
          {query && templates.data && (
            <span className="text-xs text-muted-foreground tabular-nums">
              匹配 {count(visible.length)} 份
            </span>
          )}
        </div>
        {body}
      </Card>
      {openId !== null && (
        <TemplateDetail
          templateId={openId}
          onClose={() => open(null)}
          onEdit={() => {
            open(null)
            setEditing(openId)
          }}
        />
      )}
      {editing !== null && (
        <TemplateEditor
          key={editing}
          templateId={editing === "new" ? null : editing}
          onClose={() => setEditing(null)}
        />
      )}
      <AlertDialog
        open={removing !== null}
        onOpenChange={(value) => !value && !remove.isPending && setRemoving(null)}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>删除这个模板？</AlertDialogTitle>
            <AlertDialogDescription className="break-words">
              「{removing?.name}」的名称和内容都会删除，无法恢复。
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={remove.isPending}>取消</AlertDialogCancel>
            <AlertDialogAction
              variant="destructive"
              disabled={remove.isPending}
              onClick={(event) => {
                event.preventDefault()
                if (!removing || remove.isPending) return
                remove.mutate(removing.id, {
                  onSuccess: () => {
                    if (openId === removing.id) open(null)
                    setRemoving(null)
                    toast.success("已删除模板")
                  },
                  onError: (error) => toast.error(errorMessage(error)),
                })
              }}
            >
              {remove.isPending && <Loader2Icon className="animate-spin" aria-hidden />}删除
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  )
}
