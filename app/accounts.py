"""账号：凭证文件放在 data/accounts/<unb>.json（`xianyu login` 生成），账号 ID 就是 unb。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from xianyu_core import Credential, MtopClient
from xianyu_core.guard import FileBlockStore, Guard

DEFAULT_DIR = Path("data/accounts")


def make_guard(db_path: str | Path) -> Guard:
    """限流和熔断。熔断状态放在本地库旁边（默认 data/guard.json），内存库（测试）就放内存里。"""
    if str(db_path) == ":memory:":
        return Guard()
    return Guard(FileBlockStore(Path(db_path).parent / "guard.json"))


class AccountNotFoundError(LookupError):
    pass


class Accounts:
    def __init__(
        self,
        root: str | Path = DEFAULT_DIR,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        guard: Guard | None = None,
    ):
        self.root = Path(root)
        self._transport = transport  # 测试注入 httpx.MockTransport
        self.guard = guard  # 不传就不限流、不熔断

    def ids(self) -> list[str]:
        if not self.root.is_dir():
            return []
        return sorted(p.stem for p in self.root.glob("*.json") if p.stem.isdigit())

    def resolve(self, account_id: str | None) -> str:
        """没指定账号时，只有一个账号就用它，否则报错。"""
        ids = self.ids()
        if account_id:
            if account_id not in ids:
                raise AccountNotFoundError(f"找不到账号 {account_id}，先用 xianyu login 登录")
            return account_id
        if len(ids) == 1:
            return ids[0]
        if not ids:
            raise AccountNotFoundError(f"{self.root} 下没有账号，先用 xianyu login 登录")
        raise AccountNotFoundError(f"有 {len(ids)} 个账号，请指定一个：{', '.join(ids)}")

    def path(self, account_id: str) -> Path:
        return self.root / f"{account_id}.json"

    def load(self, account_id: str) -> Credential:
        return Credential.load(self.path(account_id))

    @asynccontextmanager
    async def mtop(self, account_id: str) -> AsyncIterator[MtopClient]:
        """拿这个账号的 MtopClient（带限流和熔断），用完把续期过的 cookie 写回凭证文件。"""
        path = self.path(account_id)
        guard = self.guard.bind(account_id) if self.guard else None
        credential = Credential.load(path)
        async with MtopClient(credential, transport=self._transport, guard=guard) as client:
            try:
                yield client
            finally:
                client.credential.save(path)
