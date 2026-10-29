"""账号：凭证文件放在 data/accounts/<unb>.json（`xianyu login` 生成），账号 ID 就是 unb。"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx

from xianyu_core import Credential, MtopClient

DEFAULT_DIR = Path("data/accounts")


class AccountNotFoundError(LookupError):
    pass


class Accounts:
    def __init__(
        self,
        root: str | Path = DEFAULT_DIR,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.root = Path(root)
        self._transport = transport  # 测试注入 httpx.MockTransport

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
        """拿这个账号的 MtopClient，用完把续期过的 cookie 写回凭证文件。"""
        path = self.path(account_id)
        async with MtopClient(Credential.load(path), transport=self._transport) as client:
            try:
                yield client
            finally:
                client.credential.save(path)
