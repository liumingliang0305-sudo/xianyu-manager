import { useQueryClient, type QueryClient } from "@tanstack/react-query"
import { useEffect, useSyncExternalStore } from "react"
import { toast } from "sonner"

import { api, type Item } from "@/lib/api"
import {
  ExtensionError,
  QUIET_DETAIL_VERSION,
  fetchItemDetailViaExtension,
  versionAtLeast,
} from "@/lib/extension"
import { itemState } from "@/lib/format"

import { itemKeys, putItemInList, statsErrorMessage } from "./queries"

/*
 * 商品页上成批刷新想要、浏览、收藏。
 *
 * 浏览、收藏只有买家看的商品详情（pc.detail）里有，后台调它会被闲鱼要求过滑块、账号熔断，只能请浏览器插件
 * 在用户自己的 Chrome 里一件件打开商品页拿（extension/background.js）。以前只能在详情里一件件点，现在：
 * - 手动：列表上面点「刷新浏览/收藏」，当前账号还在主页上的商品全排上，拿完一件再要下一件，随时能停；
 *   闲鱼要验证时插件把那个闲鱼页面切到前面，这里停下来等人过完滑块点「继续」；
 * - 自动：开着商品页时，没拿过、或者拿过超过 12 小时的，悄悄拿一小批（插件 0.5.0 起才自动，
 *   要验证时插件不抢前台，这里停下来，提示人去手动刷新）。
 * 跑在模块里不跟着组件走：手动刷新时切到别的页面也接着跑完。同一时间只跑一批（插件那边本来也是一次一件）。
 *
 * 节奏按"别让用户自己的 Chrome 被闲鱼盯上"来定：插件每件之间自己停 1.5 秒，加上打开商品页的几秒；
 * 自动刷新像人慢慢一件件点开看，两件之间再停 8 秒，一批最多 10 件，同一个账号两批之间至少隔 15 分钟，
 * 一件商品最多每 12 小时自动拿一次。这样一个账号自动刷新每小时最多四十来件，7 件商品的店一天十几次。
 * 自动刷新的记录（上次什么时候跑、哪件试过）记在这个浏览器里，因为开商品页的就是这个浏览器。
 */

// 手动刷新两件之间页面再停 3 秒（人点的，想快点看到；插件另外停 1.5 秒）
const MANUAL_GAP_MS = 3_000
// 自动刷新两件之间停 8 秒
const AUTO_GAP_MS = 8_000
// 自动刷新一批最多几件，剩下的等下一批
const AUTO_BATCH = 10
// 同一个账号两批自动刷新之间至少隔多久（从上一批开始、结束都算）
const AUTO_COOLDOWN_MS = 15 * 60_000
// 浏览、收藏拿过超过这么久算旧了，自动再拿
export const STATS_STALE_MS = 12 * 3600_000
// 自动试过的（成没成都算）这么久之内不再自动试：删掉了、拿不到的不会每批都占名额
const AUTO_RETRY_MS = 6 * 3600_000
// 自动刷新碰到闲鱼要验证：这个浏览器这么久之内不再自动刷新，等人手动刷新过了滑块
const AUTO_VALIDATE_PAUSE_MS = 6 * 3600_000
// 打开商品页以后等一会儿再自动开始，只是路过的不打扰
const AUTO_DELAY_MS = 5_000
// 页面一直开着时，隔多久再看一次要不要自动刷新
const AUTO_RECHECK_MS = 5 * 60_000
// 连着这么多件没拿到就先停（多半是插件或者闲鱼出了问题，接着开商品页也是白开）
const MAX_FAILED_IN_ROW = 3
// 这几种错每件都会一样，碰到就停：没装插件、插件刚更新要刷新页面、插件没回应、闲鱼页面改版
const FATAL_CODES = new Set(["missing", "reload", "timeout"])

// ---- 这个浏览器里记的东西（读写不了时只记在内存里，关掉页面就忘） ----

const AUTO_KEY = "xym.item-stats.auto"
const PAUSE_KEY = "xym.item-stats.paused"
const accountKey = (account: string) => `xym.item-stats.${account}`
const memory = new Map<string, string>()

function readStored(key: string): string | null {
  try {
    return localStorage.getItem(key) ?? memory.get(key) ?? null
  } catch {
    return memory.get(key) ?? null
  }
}

function writeStored(key: string, value: string | null) {
  if (value == null) memory.delete(key)
  else memory.set(key, value)
  try {
    if (value == null) localStorage.removeItem(key)
    else localStorage.setItem(key, value)
  } catch {
    // 隐私模式等情况下存不了，记在内存里
  }
}

/** 一个账号的自动刷新记录：上次跑的时间、每件上次自动试的时间（毫秒） */
interface AccountMemory {
  ranAt: number
  tried: Record<string, number>
}

function loadAccount(account: string): AccountMemory {
  try {
    const raw = JSON.parse(readStored(accountKey(account)) ?? "{}")
    const tried = raw?.tried && typeof raw.tried === "object" ? raw.tried : {}
    return { ranAt: Number(raw?.ranAt) || 0, tried }
  } catch {
    return { ranAt: 0, tried: {} }
  }
}

function saveAccount(account: string, change: (mem: AccountMemory) => void) {
  const mem = loadAccount(account)
  change(mem)
  const now = Date.now()
  const tried = Object.fromEntries(
    Object.entries(mem.tried).filter(([, at]) => now - Number(at) < AUTO_RETRY_MS),
  )
  writeStored(accountKey(account), JSON.stringify({ ranAt: mem.ranAt, tried }))
}

// ---- 状态 ----

export interface StatsRefreshState {
  /** 正在跑（或者上一批）是哪个账号的 */
  account: string | null
  running: boolean
  /** 是自动的那一小批 */
  auto: boolean
  total: number
  /** 处理完几件（拿到的、没拿到的都算） */
  done: number
  failed: number
  /** 正在拿哪一件 */
  current: string | null
  /** 手动刷新停在闲鱼要验证，等人过完滑块点继续 */
  waiting: boolean
  /** 上一批自动刷新没跑完的原因 */
  note: string | null
  /** 这个浏览器开着自动刷新 */
  autoOn: boolean
  /** 碰到要验证以后自动刷新停到什么时候（毫秒），0 是没停 */
  pausedUntil: number
}

let state: StatsRefreshState = {
  account: null,
  running: false,
  auto: false,
  total: 0,
  done: 0,
  failed: 0,
  current: null,
  waiting: false,
  note: null,
  autoOn: readStored(AUTO_KEY) !== "off",
  pausedUntil: Number(readStored(PAUSE_KEY)) || 0,
}
const listeners = new Set<() => void>()

function update(change: Partial<StatsRefreshState>) {
  state = { ...state, ...change }
  for (const listener of listeners) listener()
}

function subscribe(listener: () => void) {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}

/** 成批刷新现在的状态 */
export function useStatsRefresh(): StatsRefreshState {
  return useSyncExternalStore(subscribe, () => state)
}

/** 开关这个浏览器里的自动刷新 */
export function setAutoStats(on: boolean) {
  writeStored(AUTO_KEY, on ? null : "off")
  update({ autoOn: on })
}

/** 手动刷新成了（人过了滑块），自动刷新不用再停着 */
export function clearAutoPause() {
  if (!state.pausedUntil) return
  writeStored(PAUSE_KEY, null)
  update({ pausedUntil: 0 })
}

/** 自动刷新因为碰到要验证正停着 */
export function autoPaused(s: StatsRefreshState, now = Date.now()): boolean {
  return s.pausedUntil > now
}

function pauseAuto() {
  const until = Date.now() + AUTO_VALIDATE_PAUSE_MS
  writeStored(PAUSE_KEY, String(until))
  update({ pausedUntil: until })
}

/** 浏览、收藏没拿过，或者拿得太早了 */
export function statsStale(item: Item, now = Date.now()): boolean {
  return item.browse_count == null || item.stats_at == null || now - item.stats_at * 1000 > STATS_STALE_MS
}

/** 先在售的，再按拿的时间从早到晚（没拿过的最前） */
function byPriority(items: Item[]): Item[] {
  const rank = (it: Item) => (itemState(it) === "online" ? 0 : 1)
  return [...items].sort((a, b) => rank(a) - rank(b) || (a.stats_at ?? 0) - (b.stats_at ?? 0))
}

// ---- 跑一批 ----

let queue: string[] = []
// 这一批已经拿过（成没成都算）的，自动改成手动时不再排一遍
const handled = new Set<string>()
let stopping = false
// 两件之间停着时，点"停止"马上醒
let wakeUp: (() => void) | null = null
// 等人过滑块时，"继续"给 true、"停止"给 false
let answer: ((go: boolean) => void) | null = null

function rest(ms: number) {
  return new Promise<void>((resolve) => {
    const done = () => {
      clearTimeout(timer)
      wakeUp = null
      resolve()
    }
    const timer = setTimeout(done, ms)
    wakeUp = done
  })
}

function waitForPerson() {
  return new Promise<boolean>((resolve) => {
    answer = (go) => {
      answer = null
      resolve(go)
    }
  })
}

async function run(client: QueryClient, account: string, ids: string[], auto: boolean) {
  queue = ids
  handled.clear()
  stopping = false
  update({
    account,
    running: true,
    auto,
    total: ids.length,
    done: 0,
    failed: 0,
    current: null,
    waiting: false,
    note: null,
  })
  let saved = 0
  let failedInRow = 0
  let lastError: string | null = null
  let stopReason: string | null = null
  // 人点的：插件刚碰到过验证也照样打开（要验证就切到前面给人过）
  let resume = !auto

  while (queue.length > 0 && !stopping) {
    const itemId = queue[0]
    update({ current: itemId })
    const quiet = state.auto
    try {
      const detail = await fetchItemDetailViaExtension(itemId, { quiet, resume })
      resume = false
      putItemInList(client, account, await api.saveItemStats(account, itemId, detail))
      saved++
      failedInRow = 0
      if (!quiet) clearAutoPause()
    } catch (error) {
      if (error instanceof ExtensionError && error.code === "validate") {
        if (quiet) {
          pauseAuto()
          break
        }
        update({ current: null, waiting: true })
        const go = await waitForPerson()
        update({ waiting: false })
        if (!go) break
        resume = true
        continue // 过了滑块，这一件再拿一次
      }
      lastError = statsErrorMessage(error)
      if (error instanceof ExtensionError && FATAL_CODES.has(error.code)) {
        stopReason = lastError
        break
      }
      failedInRow++
      update({ failed: state.failed + 1 })
    }
    if (quiet)
      saveAccount(account, (mem) => {
        mem.tried[itemId] = Date.now()
      })
    handled.add(itemId)
    queue.shift()
    update({ done: state.done + 1 })
    if (failedInRow >= MAX_FAILED_IN_ROW) {
      stopReason = `连着 ${MAX_FAILED_IN_ROW} 件没拿到，先停了：${lastError}`
      break
    }
    if (queue.length > 0 && !stopping) await rest(state.auto ? AUTO_GAP_MS : MANUAL_GAP_MS)
  }

  const wasAuto = state.auto
  const failed = state.failed
  queue = []
  wakeUp = null
  answer = null
  if (wasAuto)
    saveAccount(account, (mem) => {
      mem.ranAt = Date.now()
    })
  void client.invalidateQueries({ queryKey: itemKeys.list(account) })
  update({
    running: false,
    current: null,
    waiting: false,
    note: wasAuto && stopReason ? `自动刷新停了：${stopReason}` : null,
  })
  if (wasAuto) return // 自动的不弹提示，要验证、停了的原因在列表上面那行
  if (stopReason) toast.error(`浏览、收藏没刷新完（刷新了 ${saved} 件）：${stopReason}`, { duration: 8000 })
  else if (stopping) toast(`已停止，刷新了 ${saved} 件的浏览、收藏`)
  else if (failed > 0)
    toast.error(`浏览、收藏刷新了 ${saved} 件，${failed} 件没刷新成：${lastError}`, { duration: 8000 })
  else toast.success(`浏览、收藏刷新完了，共 ${saved} 件`)
}

/** 手动刷新：这个账号还在主页上的商品全排上。正在自动刷新这个账号时，改成手动、剩下的全排上 */
export function refreshAllStats(client: QueryClient, account: string, items: Item[]) {
  const ids = byPriority(items.filter((it) => it.on_platform)).map((it) => it.item_id)
  if (state.running) {
    if (state.account !== account) {
      toast.error("正在刷新另一个账号的浏览、收藏，等它完了再点")
      return
    }
    const current = queue[0]
    const rest = ids.filter((id) => id !== current && !handled.has(id))
    queue = current ? [current, ...rest] : rest
    update({ auto: false, total: state.done + queue.length })
    return
  }
  if (ids.length > 0) void run(client, account, ids, false)
}

/** 停下：正在拿的那一件拿完（存下）就停 */
export function stopStats() {
  stopping = true
  wakeUp?.()
  answer?.(false)
}

/** 人过完滑块了，接着拿 */
export function resumeStats() {
  answer?.(true)
}

/** 自动刷新的这一批：没拿过、拿得太早的，6 小时内自动试过的不算，最多 AUTO_BATCH 件 */
function autoBatch(account: string, items: Item[]): string[] {
  if (state.running || !state.autoOn || autoPaused(state)) return []
  const mem = loadAccount(account)
  const now = Date.now()
  if (now - mem.ranAt < AUTO_COOLDOWN_MS) return []
  return byPriority(
    items.filter(
      (it) => it.on_platform && statsStale(it, now) && !(now - (mem.tried[it.item_id] ?? 0) < AUTO_RETRY_MS),
    ),
  )
    .slice(0, AUTO_BATCH)
    .map((it) => it.item_id)
}

/**
 * 开着商品页、插件够新（0.5.0 起）、没在同步时，看看有没有要自动刷新的，有就拿一小批。
 * 打开页面 5 秒后看一次，之后每 5 分钟看一次；页面在后台（看不见）时不开始
 */
export function useAutoStatsRefresh(
  account: string,
  items: Item[] | undefined,
  version: string | null,
  syncing: boolean,
) {
  const client = useQueryClient()
  const capable = versionAtLeast(version, QUIET_DETAIL_VERSION)
  useEffect(() => {
    if (!items || !capable || syncing) return
    const check = () => {
      if (document.hidden) return
      const ids = autoBatch(account, items)
      if (ids.length === 0) return
      // 开始前先记下，同时开着的别的商品页就不会也跑一批
      saveAccount(account, (mem) => {
        mem.ranAt = Date.now()
      })
      void run(client, account, ids, true)
    }
    const first = setTimeout(check, AUTO_DELAY_MS)
    const every = setInterval(check, AUTO_RECHECK_MS)
    return () => {
      clearTimeout(first)
      clearInterval(every)
    }
  }, [client, account, items, capable, syncing])
}
