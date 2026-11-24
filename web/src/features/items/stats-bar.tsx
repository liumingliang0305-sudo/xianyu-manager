import { Loader2Icon, PlayIcon, RefreshCwIcon, SquareIcon } from "lucide-react"
import { useState } from "react"
import { useQueryClient } from "@tanstack/react-query"

import { Button } from "@/components/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog"
import { Label } from "@/components/ui/label"
import { Switch } from "@/components/ui/switch"
import type { Item } from "@/lib/api"
import { QUIET_DETAIL_VERSION, useExtension, versionAtLeast } from "@/lib/extension"
import { timeAgo } from "@/lib/format"
import { cn } from "@/lib/utils"

import {
  autoPaused,
  refreshAllStats,
  resumeStats,
  setAutoStats,
  statsStale,
  stopStats,
  useAutoStatsRefresh,
  useStatsRefresh,
} from "./stats-refresh"

/** 怎么装浏览器插件、为什么浏览、收藏要靠它。步骤和 docs/extension.md 的「安装」一样 */
export function ExtensionHelpDialog({ onClose }: { onClose: () => void }) {
  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>装浏览器插件，拿浏览、收藏</DialogTitle>
          <DialogDescription>
            浏览、收藏只在闲鱼的商品页里有。管理后台的服务器自己去拿会被闲鱼要求过滑块、账号被限制，
            所以由插件在你自己的 Chrome 里打开商品页拿，拿完就关。
          </DialogDescription>
        </DialogHeader>
        <ol className="flex list-decimal flex-col gap-2 pl-5 text-sm">
          <li>
            Chrome 打开 <span className="font-mono text-xs" translate="no">chrome://extensions</span>
            ，右上角打开"开发者模式"，点"加载已解压的扩展程序"，选项目里的{" "}
            <span className="font-mono text-xs" translate="no">extension</span> 目录。
            装过旧版的，点一下插件上的刷新按钮，更新到 {QUIET_DETAIL_VERSION} 以上。
          </li>
          <li>左下角用户菜单里点「浏览器插件令牌」生成一个，填到插件弹窗的「插件令牌」里。</li>
          <li>
            插件弹窗的「管理后台地址」填这个后台的地址（
            <span className="font-mono text-xs" translate="no">
              {location.origin}
            </span>
            ），不是本机地址时 Chrome 会问一次，点允许。
          </li>
          <li>回到这个页面刷新一下，商品列表上面会出现「刷新浏览/收藏」。</li>
        </ol>
        <p className="text-xs text-muted-foreground">
          装好以后，开着商品页时没拿过、或者拿过超过 12 小时的会自动拿一小批，一件件慢慢来；
          想一次拿全点「刷新浏览/收藏」。Chrome 里会短暂多出一个闲鱼标签页，是插件在拿。
        </p>
        <DialogFooter>
          <Button type="button" onClick={onClose}>
            知道了
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

/** 浏览、收藏现在是什么样：几件还没拿过，或者最早的是什么时候拿的 */
function freshness(items: Item[]): string {
  const missing = items.filter((it) => it.browse_count == null).length
  if (missing === items.length) return "浏览、收藏还没拿过"
  if (missing > 0) return `${missing} 件还没拿过浏览、收藏`
  const oldest = Math.min(...items.map((it) => it.stats_at ?? 0))
  return oldest
    ? `浏览、收藏都拿过了，最早的一件是${timeAgo(oldest)}拿的`
    : "浏览、收藏都拿过了，有的不知道是什么时候拿的"
}

/**
 * 商品列表上面那一行右边：浏览、收藏从哪来、新不新，一键全部刷新（进度、停止、要验证时继续），
 * 自动刷新的开关。没装插件时说清楚为什么是 -，点「怎么装」看步骤。自动刷新也挂在这里
 */
export function StatsBar({
  account,
  items,
  syncing,
}: {
  account: string
  items: Item[] | undefined
  syncing: boolean
}) {
  const client = useQueryClient()
  const version = useExtension()
  const refresh = useStatsRefresh()
  const [help, setHelp] = useState(false)
  useAutoStatsRefresh(account, items, version, syncing)

  const live = (items ?? []).filter((it) => it.on_platform)
  if (live.length === 0) return null

  const mine = refresh.account === account
  const running = refresh.running && mine
  const quiet = versionAtLeast(version, QUIET_DETAIL_VERSION)
  const paused = autoPaused(refresh)

  let text: string
  let tone: "muted" | "warning" = "muted"
  let actions = null
  if (!version) {
    text = "浏览、收藏要装浏览器插件才拿得到，没拿过的显示 -"
    actions = (
      <Button variant="outline" size="sm" onClick={() => setHelp(true)}>
        怎么装
      </Button>
    )
  } else if (running && refresh.waiting) {
    tone = "warning"
    text = "闲鱼要验证：Chrome 已经切到那件商品的闲鱼页面，在上面过一下滑块，再回来点「继续」"
    actions = (
      <>
        <Button variant="outline" size="sm" onClick={resumeStats}>
          <PlayIcon />
          继续
        </Button>
        <Button variant="ghost" size="sm" onClick={stopStats}>
          停止
        </Button>
      </>
    )
  } else if (running) {
    const at = Math.min(refresh.done + 1, refresh.total)
    text = `${refresh.auto ? "正在自动刷新" : "正在从你的 Chrome 里拿"}浏览、收藏 ${at}/${refresh.total}…`
    actions = (
      <>
        {refresh.auto && (
          <Button variant="outline" size="sm" onClick={() => refreshAllStats(client, account, live)}>
            全部刷新
          </Button>
        )}
        <Button variant="ghost" size="sm" onClick={stopStats}>
          <SquareIcon />
          停止
        </Button>
      </>
    )
  } else {
    const stale = live.filter((it) => statsStale(it)).length
    if (paused && refresh.autoOn && stale > 0) {
      tone = "warning"
      text = "自动刷新碰到闲鱼要验证，先停了：点「刷新浏览/收藏」，在切出来的闲鱼页面上过一下滑块"
    } else if (mine && refresh.note) {
      tone = "warning"
      text = refresh.note
    } else {
      text = freshness(live)
      if (!quiet) text += `；插件更新到 ${QUIET_DETAIL_VERSION} 能自动刷新`
    }
    actions = (
      <Button
        variant="outline"
        size="sm"
        disabled={refresh.running}
        title={refresh.running ? "正在刷新另一个账号的浏览、收藏" : undefined}
        onClick={() => refreshAllStats(client, account, live)}
      >
        <RefreshCwIcon />
        刷新浏览/收藏
      </Button>
    )
  }

  return (
    <div className="ml-auto flex min-w-0 flex-wrap items-center justify-end gap-x-3 gap-y-2">
      <p
        aria-live="polite"
        className={cn(
          "flex min-w-0 items-center gap-1.5 text-xs tabular-nums",
          tone === "warning" ? "text-warning-ink" : "text-muted-foreground",
        )}
      >
        {running && !refresh.waiting && <Loader2Icon className="size-3.5 shrink-0 animate-spin" />}
        {text}
      </p>
      {version && quiet && !running && (
        <div className="flex items-center gap-1.5">
          <Switch
            id="auto-item-stats"
            size="sm"
            checked={refresh.autoOn}
            onCheckedChange={setAutoStats}
          />
          <Label htmlFor="auto-item-stats" className="text-xs font-normal text-muted-foreground">
            自动刷新
          </Label>
        </div>
      )}
      <div className="flex items-center gap-1">{actions}</div>
      {help && <ExtensionHelpDialog onClose={() => setHelp(false)} />}
    </div>
  )
}
