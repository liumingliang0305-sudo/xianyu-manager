import { Loader2Icon, RotateCwIcon } from "lucide-react"

import { Callout } from "@/components/callout"
import { StatusPill, type Tone } from "@/components/status-pill"
import { Button } from "@/components/ui/button"
import type { Draft, WordBasis, WordHit, WordLevel } from "@/lib/api"
import { timeAgo } from "@/lib/format"

const LEVELS: Record<WordLevel, { label: string; tone: Tone }> = {
  strong: { label: "强提醒", tone: "warning" },
  weak: { label: "弱提醒", tone: "neutral" },
  info: { label: "告知", tone: "outline" },
}
const LEVEL_ORDER: WordLevel[] = ["strong", "weak", "info"]
const BASES: Record<WordBasis, string> = {
  official: "闲鱼规则",
  law: "法律",
  opensource: "开源项目，没写出处",
  experience: "卖家经验",
}
/** 上下文前后各带几个字 */
const AROUND = 8

/** 平台检查现在是什么情况，页面上一行字说清楚 */
export type PlatformState =
  | { kind: "idle" }
  | { kind: "typing" }
  | { kind: "waiting" }
  | { kind: "checking" }
  | { kind: "skipped"; reason: string }
  | { kind: "failed"; error: string }

/** 同一个词命中好几处时并成一行，给第一处的上下文 */
interface HitGroup {
  key: string
  first: WordHit
  count: number
}

function groupHits(hits: WordHit[]): HitGroup[] {
  const groups = new Map<string, HitGroup>()
  for (const hit of hits) {
    const key = `${hit.category}|${hit.rule}|${hit.text.toLowerCase()}`
    const group = groups.get(key)
    if (group) group.count += 1
    else groups.set(key, { key, first: hit, count: 1 })
  }
  return [...groups.values()].sort(
    (a, b) =>
      LEVEL_ORDER.indexOf(a.first.level) - LEVEL_ORDER.indexOf(b.first.level) ||
      a.first.start - b.first.start,
  )
}

function Context({ text, hit }: { text: string; hit: WordHit }) {
  const from = Math.max(0, hit.start - AROUND)
  const to = Math.min(text.length, hit.end + AROUND)
  const flat = (s: string) => s.replace(/\s+/g, " ")
  return (
    <span className="min-w-0 truncate text-muted-foreground">
      {from > 0 && "…"}
      {flat(text.slice(from, hit.start))}
      <mark className="rounded-sm bg-warning-soft px-0.5 text-warning-ink">{flat(hit.text)}</mark>
      {flat(text.slice(hit.end, to))}
      {to < text.length && "…"}
    </span>
  )
}

function HitRow({ group, description }: { group: HitGroup; description: string }) {
  const { first: hit, count } = group
  const level = LEVELS[hit.level]
  return (
    <li className="flex flex-col gap-0.5 py-2 first:pt-0 last:pb-0">
      <div className="flex min-w-0 items-center gap-2 text-[13px]">
        <StatusPill tone={level.tone} className="shrink-0">
          {level.label}
        </StatusPill>
        <span className="shrink-0 font-medium">{hit.rule}</span>
        {count > 1 && <span className="shrink-0 text-xs text-muted-foreground tabular-nums">{count} 处</span>}
        <Context text={description} hit={hit} />
      </div>
      <p className="text-xs text-muted-foreground">
        <span title={hit.note ?? undefined} className={hit.note ? "cursor-help underline decoration-dotted underline-offset-2" : undefined}>
          {hit.category_name}
        </span>
        {" · "}依据：{BASES[hit.basis]}
        {hit.suggest && (
          <>
            {" · "}建议：<span className="text-foreground">{hit.suggest}</span>
          </>
        )}
      </p>
    </li>
  )
}

function platformLine(draft: Draft, state: PlatformState): string | null {
  const result = draft.platform_words.result
  if (!draft.description.trim() && state.kind !== "typing") return null
  switch (state.kind) {
    case "typing":
      return "闲鱼检查：描述改了，停下来以后再查"
    case "checking":
      return "闲鱼检查：正在查…"
    case "waiting":
      return "闲鱼检查：停下来几秒后查"
    case "skipped":
      return `闲鱼检查没跑：${state.reason}，只查了本地词表。发布时闲鱼还会查`
    case "failed":
      return `闲鱼检查没查成：${state.error}。发布时还会再查`
  }
  if (result?.current) {
    if (!result.ok) return `闲鱼检查没查成：${result.error ?? "原因不明"}。发布时还会再查`
    if (result.blocked) return `闲鱼检查 · ${timeAgo(result.checked_at)}`
    return `闲鱼检查：没发现不让用的词 · ${timeAgo(result.checked_at)}`
  }
  if (draft.edit_item_id && !draft.platform_words.needed) return "描述和闲鱼上的一样，没查平台。改了描述会查"
  return null
}

/**
 * 描述框下面的违规词提醒：上面是闲鱼平台的检查（拦发布），下面是本地词表的提醒（不拦）。
 * 本地词表查的是保存了的描述；平台检查的时机由表单（EditableDraft）定，这里只显示。
 */
export function WordCheck({
  draft,
  state,
  onRecheck,
}: {
  draft: Draft
  state: PlatformState
  onRecheck: () => void
}) {
  const result = draft.platform_words.result
  const blocked = result?.current && result.ok && result.blocked && state.kind !== "typing"
  const groups = groupHits(draft.word_hits)
  const line = platformLine(draft, state)
  const canRecheck =
    !!draft.description.trim() &&
    state.kind !== "checking" &&
    state.kind !== "typing" &&
    (state.kind === "skipped" || state.kind === "failed" || !!result?.current)

  return (
    <div className="flex flex-col gap-3">
      {blocked && (
        <Callout
          tone="negative"
          title={result.forbid ? "闲鱼不让用这些词，发布会被拦" : "闲鱼标出了这些词，发布会被拦"}
        >
          <p className="flex flex-wrap gap-1.5 py-0.5">
            {(result.words ?? []).map((word) => (
              <span key={word} className="rounded-md border border-negative-line bg-background px-1.5 font-medium">
                {word}
              </span>
            ))}
          </p>
          <p className="text-[13px]">
            {result.forbid
              ? "删掉或换个说法，停下来以后会自动再查。"
              : "平台没说不让发，但现在只要命中就不发（还没见过平台分等级的返回）。删掉或换个说法再发。"}
            {result.msg && ` 平台的说法：${result.msg}`}
          </p>
        </Callout>
      )}

      {groups.length > 0 && (
        <div className="rounded-lg border px-3 py-2.5">
          <p className="pb-2 text-xs text-muted-foreground">
            词表提醒 · {draft.word_hits.length} 处。只是提醒，不拦发布；能不能发以闲鱼的检查为准
          </p>
          <ul className="divide-y divide-rule">
            {groups.map((g) => (
              <HitRow key={g.key} group={g} description={draft.description} />
            ))}
          </ul>
        </div>
      )}

      {line && (
        <div className="flex min-h-6 items-center gap-2 text-xs text-muted-foreground">
          {state.kind === "checking" && <Loader2Icon className="size-3.5 animate-spin" />}
          <span aria-live="polite">{line}</span>
          {canRecheck && (
            <Button variant="ghost" size="xs" onClick={onRecheck} className="text-muted-foreground">
              <RotateCwIcon />
              再查一次
            </Button>
          )}
        </div>
      )}
    </div>
  )
}
