"""按账号的限流和风控熔断。

- 限流：每个账号按"读"和"改动"两类接口分别计数，窗口是最近一分钟。超出时先等，要等的时间超过
  `max_wait` 就抛 RateLimitedError，免得网页上的请求卡住太久。计数只在本进程内存里；
- 熔断：调接口碰到风控（RiskControlError）时，这个账号停 `cooldown` 秒，期间所有请求直接抛
  CircuitOpenError，一个都不发出去。熔断状态存在文件里，命令行和管理后台两个进程共用，重启后还在。

默认数值和取值理由见 docs/guard.md。
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import time
from collections import defaultdict, deque
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

from xianyu_core.errors import CircuitOpenError, RateLimitedError, XianyuError
from xianyu_core.item import DELETE_API, DOWNSHELF_API, EDIT_API
from xianyu_core.order import ADJUST_PRICE_API

READ = "read"
WRITE = "write"

# 会改平台数据的接口。新加的改动类接口要登记在这里，不然按读接口限流
WRITE_APIS = frozenset({EDIT_API, DOWNSHELF_API, DELETE_API, ADJUST_PRICE_API})

WINDOW = 60.0
DEFAULT_LIMITS = {READ: 30, WRITE: 3}  # 每个账号每分钟最多几次
DEFAULT_MAX_WAIT = 20.0
DEFAULT_COOLDOWN = 30 * 60.0


def api_kind(api: str) -> str:
    return WRITE if api in WRITE_APIS else READ


@dataclass
class Block:
    """一次熔断：从 `since` 到 `until`（秒级时间戳），`reason` 是触发它的错误。"""

    since: float
    until: float
    reason: str


class BlockStore(Protocol):
    def load(self) -> dict[str, Block]: ...

    def save(self, blocks: dict[str, Block]) -> None: ...


class MemoryBlockStore:
    """只在内存里，进程退出就没了。测试和临时用。"""

    def __init__(self) -> None:
        self._blocks: dict[str, Block] = {}

    def load(self) -> dict[str, Block]:
        return dict(self._blocks)

    def save(self, blocks: dict[str, Block]) -> None:
        self._blocks = dict(blocks)


class FileBlockStore:
    """JSON 文件 {账号: {since, until, reason}}。

    先写临时文件再改名，别的进程不会读到写了一半的文件。
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict[str, Block]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return {account: Block(**value) for account, value in raw.items()}
        except FileNotFoundError:
            return {}
        except (ValueError, TypeError, AttributeError) as exc:
            # 读不出来就不放行：宁可停下来让人看一眼，也不要在风控期间继续发请求
            raise XianyuError(f"熔断状态文件损坏：{self.path}，检查后删掉它再试") from exc

    def save(self, blocks: dict[str, Block]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(self.path.name + ".tmp")
        data = {account: asdict(block) for account, block in blocks.items()}
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, self.path)


class Guard:
    """所有账号共用一个实例（一个进程一个）。`clock`、`sleep` 可以换成假的，测试用。"""

    def __init__(
        self,
        store: BlockStore | None = None,
        *,
        limits: dict[str, int] | None = None,
        max_wait: float = DEFAULT_MAX_WAIT,
        cooldown: float = DEFAULT_COOLDOWN,
        clock: Callable[[], float] = time.time,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._store = store or MemoryBlockStore()
        self._limits = {**DEFAULT_LIMITS, **(limits or {})}
        self._max_wait = max_wait
        self._cooldown = cooldown
        self._clock = clock
        self._sleep = sleep
        self._hits: defaultdict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._locks: defaultdict[tuple[str, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    def bind(self, account: str) -> AccountGuard:
        return AccountGuard(self, account)

    # ---- 熔断 ----

    def block(self, account: str) -> Block | None:
        """这个账号当前的熔断，没有或已经过期时返回 None。"""
        block = self._store.load().get(account)
        return block if block and block.until > self._clock() else None

    def trip(self, account: str, reason: str) -> Block:
        """熔断这个账号。已经在熔断期的，从现在重新计时。"""
        now = self._clock()
        blocks = self._active(now)
        blocks[account] = Block(since=now, until=now + self._cooldown, reason=reason[:300])
        self._store.save(blocks)
        return blocks[account]

    def unblock(self, account: str) -> bool:
        """手动解除，返回原来是不是在熔断期。"""
        blocks = self._active(self._clock())
        removed = blocks.pop(account, None)
        self._store.save(blocks)
        return removed is not None

    # ---- 限流 ----

    async def acquire(self, account: str, api: str) -> None:
        """发请求之前调用：熔断中直接抛错；超过频率就等，要等太久就抛错。"""
        self._check(account)
        kind = api_kind(api)
        limit = self._limits[kind]
        key = (account, kind)
        async with self._locks[key]:
            hits = self._hits[key]
            while True:
                now = self._clock()
                while hits and now - hits[0] >= WINDOW:
                    hits.popleft()
                if len(hits) < limit:
                    hits.append(now)
                    return
                wait = WINDOW - (now - hits[0])
                if wait > self._max_wait:
                    what = "改动" if kind == WRITE else "请求"
                    raise RateLimitedError(
                        f"{what}太频繁：每分钟最多 {limit} 次，{math.ceil(wait)} 秒后再试",
                        retry_after=wait,
                    )
                await self._sleep(wait)
                self._check(account)  # 等的时候可能已经被别的请求熔断了

    # ---- 内部 ----

    def _check(self, account: str) -> None:
        block = self.block(account)
        if block:
            minutes = math.ceil((block.until - self._clock()) / 60)
            raise CircuitOpenError(
                f"账号触发风控后已暂停请求，{minutes} 分钟后自动恢复",
                until=block.until,
                reason=block.reason,
            )

    def _active(self, now: float) -> dict[str, Block]:
        return {a: b for a, b in self._store.load().items() if b.until > now}


class AccountGuard:
    """绑定到一个账号的 Guard，交给 MtopClient 用。"""

    def __init__(self, guard: Guard, account: str):
        self._guard = guard
        self._account = account

    async def acquire(self, api: str) -> None:
        await self._guard.acquire(self._account, api)

    def trip(self, reason: str) -> None:
        self._guard.trip(self._account, reason)
