"""自动同步：商品列表、卖出的进行中订单、买到的订单，不用每次手动点"同步"。

三条路，按发的请求从少到多：
1. 交易消息：`OrderFeed` 记下一笔新的卖出订单，或者卖出订单的状态变了，商品多半也变了（卖掉了、
   退款后回到在售），隔 `trigger_delay` 同步一次商品列表；这段时间里又来了卡片也只同步一次；
2. 打开页面：商品页、订单页打开时，上次同步已经超过 `stale_after`（或者从没同步过），
   就在后台同步一次；
3. 定时兜底：每个账号每种同步按间隔跑，接住在闲鱼 App 里直接做的改动。默认商品 3 小时、
   卖出的进行中订单 1 小时、买到的 12 小时，页面上能按账号改间隔、关掉。

每次同步（手动的也算）记进 sync_jobs，定时任务从这次往后顺延；跑之前、跑完都推一条
`{"type": "sync", "job": ..., "running": ...}` 给页面（走消息的推送流）。

不做的：
- 商品的浏览数、收藏数：只有买家看的商品详情接口（pc.detail）里有，从后台调它会被要滑块
  （见 app/items.py 开头），自动的、手动的同步都不拉，由浏览器插件在用户的 Chrome 里拿；
- 买到的、专业卖家卖出的订单列表，自动同步只翻前 `AUTO_PAGES` 页（最新的），更早的都结束了，
  第一次要全量拉就在订单页手动同步。

什么时候不跑：账号在熔断、登录失效、碰到过风控，一个请求都不发。风控以后要等人处理过
（解除熔断，或者在页面上做过一次操作确认登录有效）才接着跑，后台不自己试。

所有平台请求和页面上的操作一样拿账号的锁、过限流和熔断（docs/guard.md）。所以必须跑在 xym serve
进程里：限流计数和锁都在这个进程的内存里，另起一个进程定时跑等于每个账号的限流额度翻倍。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import random
import time
from collections import Counter
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from typing import Any

import httpx

from app.accounts import Accounts, LoginState
from app.db import Database, Row
from app.items import ItemService, SyncResult
from app.orders import OrderService, OrderSyncResult
from xianyu_core.errors import XianyuError
from xianyu_core.order import BOUGHT, SOLD

log = logging.getLogger(__name__)

ITEMS = "items"
JOBS = (ITEMS, SOLD, BOUGHT)

# 谁触发的
SCHEDULE = "schedule"
VISIT = "visit"
MESSAGE = "message"
MANUAL = "manual"

HOUR = 3600.0
DEFAULT_INTERVALS = {ITEMS: 3 * HOUR, SOLD: 1 * HOUR, BOUGHT: 12 * HOUR}
# 页面上能选的间隔
INTERVAL_CHOICES = (1 * HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR, 24 * HOUR)
# 失败以后最快隔多久再定时跑。风控、登录失效不在这里重试，见 usable()
RETRY_AFTER = 30 * 60.0
# 每次往后顺延时多等间隔的 0～10%，几个账号不会一直挤在同一分钟跑
JITTER = 0.1
# 自动同步时列表最多翻几页（买到的每页 10 条，卖家工作台每页 20 条）
AUTO_PAGES = 3

TICK = 60.0  # 多久看一次有没有到期的
STALE_AFTER = 15 * 60.0  # 打开页面时上次同步超过这么久就再同步
TRIGGER_DELAY = 90.0  # 交易消息来了等多久再同步商品


@dataclass
class JobStatus:
    job: str
    enabled: bool
    interval: float
    last_run_at: float | None
    last_ok_at: float | None
    last_trigger: str | None
    last_error: str | None
    last_result: dict[str, int]
    next_run_at: float | None
    running: bool


def counts(result: SyncResult | OrderSyncResult) -> dict[str, int]:
    """同步结果里每一项有几个，存进 last_result。"""
    return {key: len(value) for key, value in asdict(result).items() if isinstance(value, list)}


class AutoSync:
    """`publish(账号, 事件)`：推给页面，xym serve 里是 MessageHub.publish。
    `tick`、`stale_after`、`trigger_delay` 给测试调快。
    """

    def __init__(
        self,
        db: Database,
        accounts: Accounts,
        locks: Mapping[str, asyncio.Lock],
        items: ItemService,
        orders: OrderService,
        *,
        publish: Callable[[str, dict[str, Any]], None] | None = None,
        tick: float = TICK,
        stale_after: float = STALE_AFTER,
        trigger_delay: float = TRIGGER_DELAY,
    ):
        self._db = db
        self._accounts = accounts
        self._locks = locks
        self._items = items
        self._orders = orders
        self._publish = publish
        self._tick = tick
        self._stale_after = stale_after
        self._trigger_delay = trigger_delay
        self._running: Counter[tuple[str, str]] = Counter()
        self._delayed: dict[tuple[str, str], asyncio.Task[None]] = {}
        self._tasks: set[asyncio.Task[Any]] = set()
        self._loop: asyncio.Task[None] | None = None

    @property
    def active(self) -> bool:
        """开没开自动同步（xym serve 开）。没开时只记手动同步的结果。"""
        return self._loop is not None

    def start(self) -> None:
        if self._loop is None:
            self._loop = asyncio.create_task(self._run_forever())

    async def aclose(self) -> None:
        tasks = [t for t in [self._loop, *self._tasks] if t]
        self._loop = None
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await task
        self._tasks.clear()
        self._delayed.clear()
        self._running.clear()

    # ---- 设置和状态 ----

    def statuses(self, account_id: str) -> list[JobStatus]:
        rows = {
            row["job"]: row
            for row in self._db.execute(
                "SELECT * FROM sync_jobs WHERE account_id = %s", (account_id,)
            )
        }
        return [self._status(account_id, job, rows.get(job)) for job in JOBS]

    def status(self, account_id: str, job: str) -> JobStatus:
        return next(s for s in self.statuses(account_id) if s.job == job)

    def paused(self, account_id: str) -> str | None:
        """自动同步现在为什么不跑，能跑时返回 None。"""
        guard = self._accounts.guard
        if guard and guard.block(account_id):
            return "账号碰到风控，暂停中"
        status = self._accounts.login_status(account_id, max_age=math.inf)
        if status and status.state is LoginState.EXPIRED:
            return "登录失效了，重新登录后接着同步"
        if status and status.state is LoginState.RISK:
            return "碰到过风控，处理好以后接着同步"
        return None

    def configure(
        self,
        account_id: str,
        job: str,
        *,
        enabled: bool | None = None,
        interval: float | None = None,
    ) -> JobStatus:
        """开关这种同步、改定时的间隔。改了间隔从上次跑完的时间重新算下次。"""
        if job not in JOBS:
            raise ValueError(f"不认识的同步：{job}")
        if interval is not None and interval not in INTERVAL_CHOICES:
            raise ValueError("间隔只能选 1、3、6、12、24 小时")
        current = self.status(account_id, job)
        enabled = current.enabled if enabled is None else enabled
        next_run_at = current.next_run_at
        if interval is not None and current.last_run_at is not None:
            next_run_at = current.last_run_at + interval
        self._db.execute(
            """
            INSERT INTO sync_jobs (account_id, job, enabled, interval, next_run_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (account_id, job) DO UPDATE SET
                enabled = excluded.enabled,
                interval = COALESCE(excluded.interval, sync_jobs.interval),
                next_run_at = excluded.next_run_at
            """,
            (account_id, job, enabled, interval, next_run_at),
        )
        self._notify(account_id, job)
        return self.status(account_id, job)

    # ---- 跑 ----

    async def run(self, account_id: str, job: str, trigger: str) -> Any:
        """同步一次，拿账号的锁。结果记进 sync_jobs、推给页面；出错也记下，照样抛出。"""
        if job not in JOBS:
            raise ValueError(f"不认识的同步：{job}")
        self._mark(account_id, job)
        return await self._run_marked(account_id, job, trigger)

    def visit(self, account_id: str, jobs: Iterable[str]) -> None:
        """打开了页面：这几种同步上次跑完超过 stale_after（或者从没跑过）的，在后台跑一次。

        看的是上次跑完的时间，失败的也算，免得一直失败时每打开一次页面就请求一次。
        """
        if not self.active or self.paused(account_id):
            return
        now = time.time()
        wanted = set(jobs)
        for status in self.statuses(account_id):
            if status.job not in wanted or not status.enabled or status.running:
                continue
            if status.last_run_at and now - status.last_run_at < self._stale_after:
                continue
            self._start(account_id, status.job, VISIT)

    def later(self, account_id: str, job: str) -> None:
        """过 trigger_delay 再同步一次（交易消息触发的）。已经排着的不再排。"""
        key = (account_id, job)
        if not self.active or key in self._delayed:
            return
        self._delayed[key] = self._spawn(self._after_delay(account_id, job))

    async def run_due(self) -> list[tuple[str, str]]:
        """跑一轮到期的定时同步，一个账号一个账号、一种一种地跑。返回这一轮跑了的 (账号, 同步)。"""
        ran = []
        for account_id in self._accounts.ids():
            for status in self.statuses(account_id):
                if not status.enabled or status.running:
                    continue
                if status.next_run_at and status.next_run_at > time.time():
                    continue
                # 每跑一种都再看一眼：前一种碰到风控、登录失效了，后面的就别发了
                if self.paused(account_id):
                    break
                ran.append((account_id, status.job))
                self._mark(account_id, status.job)
                await self._quietly(account_id, status.job, SCHEDULE)
        return ran

    # ---- 内部 ----

    async def _sync(self, account_id: str, job: str, trigger: str) -> SyncResult | OrderSyncResult:
        if job == ITEMS:
            return await self._items.sync(account_id)
        pages = {} if trigger == MANUAL else {"max_pages": AUTO_PAGES}
        return await self._orders.sync(account_id, job, **pages)

    def _mark(self, account_id: str, job: str) -> None:
        """记下"在跑"，推给页面。要在真正开始跑之前同步地记，免得连着两次打开页面各起一个。"""
        self._running[(account_id, job)] += 1
        self._notify(account_id, job)

    async def _run_marked(self, account_id: str, job: str, trigger: str) -> Any:
        key = (account_id, job)
        try:
            async with self._locks[account_id]:
                result = await self._sync(account_id, job, trigger)
            self._record(account_id, job, trigger, result=counts(result))
            return result
        except Exception as exc:
            self._record(account_id, job, trigger, error=str(exc) or type(exc).__name__)
            raise
        finally:
            self._running[key] -= 1
            if self._running[key] <= 0:
                del self._running[key]
            self._notify(account_id, job)

    def _start(self, account_id: str, job: str, trigger: str) -> None:
        """在后台跑一次。"""
        self._mark(account_id, job)
        self._spawn(self._quietly(account_id, job, trigger))

    async def _quietly(self, account_id: str, job: str, trigger: str) -> None:
        """跑一次已经 _mark 过的同步，出错只记日志（结果已经记进 sync_jobs 了）。"""
        try:
            await self._run_marked(account_id, job, trigger)
        except (XianyuError, httpx.HTTPError) as exc:
            log.warning("账号 %s：自动同步 %s 失败：%s", account_id, job, exc)
        except Exception:
            log.exception("账号 %s：自动同步 %s 出错", account_id, job)

    async def _after_delay(self, account_id: str, job: str) -> None:
        try:
            await asyncio.sleep(self._trigger_delay)
        finally:
            # 先撤掉再跑：跑的时候又来了卡片，要再排一次，不能当成已经排着
            self._delayed.pop((account_id, job), None)
        if account_id not in self._accounts.ids():
            return  # 等的时候账号被移除了
        status = self.status(account_id, job)
        if status.enabled and not status.running and not self.paused(account_id):
            self._mark(account_id, job)
            await self._quietly(account_id, job, MESSAGE)

    async def _run_forever(self) -> None:
        while True:
            try:
                await self.run_due()
            except Exception:
                # 后台任务里的异常没人接，记下来接着跑
                log.exception("自动同步出错")
            await asyncio.sleep(self._tick)

    def _spawn(self, coro: Awaitable[Any]) -> asyncio.Task[Any]:
        task = asyncio.ensure_future(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _record(
        self,
        account_id: str,
        job: str,
        trigger: str,
        *,
        result: dict[str, int] | None = None,
        error: str | None = None,
    ) -> None:
        now = time.time()
        interval = self.status(account_id, job).interval
        wait = interval * (1 + random.uniform(0, JITTER))
        if error is not None:
            wait = min(wait, RETRY_AFTER)
        self._db.execute(
            """
            INSERT INTO sync_jobs (account_id, job, last_run_at, last_ok_at, last_trigger,
                                   last_error, last_result, next_run_at)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (account_id, job) DO UPDATE SET
                last_run_at = excluded.last_run_at,
                last_ok_at = COALESCE(excluded.last_ok_at, sync_jobs.last_ok_at),
                last_trigger = excluded.last_trigger,
                last_error = excluded.last_error,
                last_result = CASE WHEN excluded.last_error IS NULL THEN excluded.last_result
                                   ELSE sync_jobs.last_result END,
                next_run_at = excluded.next_run_at
            """,
            (
                account_id,
                job,
                now,
                now if error is None else None,
                trigger,
                error[:300] if error else None,
                json.dumps(result or {}),
                now + wait,
            ),
        )

    def _notify(self, account_id: str, job: str) -> None:
        if self._publish:
            running = (account_id, job) in self._running
            self._publish(account_id, {"type": "sync", "job": job, "running": running})

    def _status(self, account_id: str, job: str, row: Row | None) -> JobStatus:
        row = row or {}
        return JobStatus(
            job=job,
            enabled=row.get("enabled", True),
            interval=row.get("interval") or DEFAULT_INTERVALS[job],
            last_run_at=row.get("last_run_at"),
            last_ok_at=row.get("last_ok_at"),
            last_trigger=row.get("last_trigger"),
            last_error=row.get("last_error"),
            last_result=json.loads(row.get("last_result") or "{}"),
            next_run_at=row.get("next_run_at"),
            running=(account_id, job) in self._running,
        )
