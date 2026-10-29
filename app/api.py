"""管理后台的 HTTP 接口（FastAPI）。没有登录鉴权，只能监听本机。

约定：
- 金额：响应里一律是分（`*_cent`，整数）；请求里的价格是元的字符串（"49"、"12.5"），服务端校验换算；
- 出错时返回 `{"code": ..., "message": ...}`，`code` 见 ERRORS；
- 同一个账号的平台请求串行执行，避免并发改动和凭证文件互相覆盖。
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import db
from app.accounts import DEFAULT_DIR, AccountNotFoundError, Accounts
from app.items import ItemService, PriceGuardError
from xianyu_core.errors import AuthRequiredError, RiskControlError, XianyuError
from xianyu_core.item import yuan_to_cent

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"

# 异常 → (HTTP 状态码, code)。按顺序匹配，子类要排在父类前面
ERRORS: list[tuple[type[Exception], int, str]] = [
    (PriceGuardError, 409, "price_guard"),
    (AccountNotFoundError, 404, "account_not_found"),
    (AuthRequiredError, 401, "auth_required"),
    (RiskControlError, 503, "risk_control"),
    (XianyuError, 502, "platform_error"),
    (ValueError, 400, "invalid"),
]


class SyncIn(BaseModel):
    stats: bool = False


class PriceIn(BaseModel):
    price: str
    orig_price: str | None = None
    force: bool = False


class DescIn(BaseModel):
    desc: str


def create_app(
    *,
    db_path: str | Path = db.DEFAULT_PATH,
    accounts_dir: str | Path = DEFAULT_DIR,
    transport: httpx.AsyncBaseTransport | None = None,
    interval: float = 1.5,
    web_dist: Path | None = WEB_DIST,
) -> FastAPI:
    accounts = Accounts(accounts_dir, transport=transport)
    locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        conn = db.connect(db_path)
        app.state.items = ItemService(conn, accounts, interval=interval)
        try:
            yield
        finally:
            conn.close()

    app = FastAPI(title="闲鱼管理系统", lifespan=lifespan)

    for exc_type, status, code in ERRORS:
        app.add_exception_handler(exc_type, _error_handler(status, code))

    def items(request: Request) -> ItemService:
        return request.app.state.items

    def account(account_id: str) -> str:
        return accounts.resolve(account_id)

    @app.get("/api/accounts")
    async def list_accounts() -> dict[str, Any]:
        return {
            "accounts": [
                {"id": account_id, "saved_at": accounts.load(account_id).saved_at}
                for account_id in accounts.ids()
            ]
        }

    @app.get("/api/accounts/{account_id}/items")
    async def list_items(
        request: Request, account_id: str, status: int | None = None, include_gone: bool = False
    ) -> dict[str, Any]:
        records = items(request).list(account(account_id), status=status, include_gone=include_gone)
        return {
            "items": [asdict(r) for r in records],
            "last_synced_at": max((r.synced_at for r in records), default=None),
        }

    @app.post("/api/accounts/{account_id}/items/sync")
    async def sync_items(request: Request, account_id: str, body: SyncIn) -> dict[str, Any]:
        account_id = account(account_id)
        async with locks[account_id]:
            result = await items(request).sync(account_id, with_stats=body.stats)
        return asdict(result)

    @app.get("/api/accounts/{account_id}/items/{item_id}/detail")
    async def item_detail(request: Request, account_id: str, item_id: str) -> dict[str, Any]:
        account_id = account(account_id)
        async with locks[account_id]:
            detail = await items(request).fetch_detail(account_id, item_id)
        data = asdict(detail)
        data.pop("raw")
        return data

    @app.post("/api/accounts/{account_id}/items/{item_id}/price")
    async def change_price(
        request: Request, account_id: str, item_id: str, body: PriceIn
    ) -> dict[str, Any]:
        account_id = account(account_id)
        price = yuan_to_cent(body.price)
        orig = yuan_to_cent(body.orig_price) if body.orig_price else None
        async with locks[account_id]:
            old = await items(request).change_price(
                account_id, item_id, price, orig_price_cent=orig, force=body.force
            )
        return {"old_price_cent": old, "price_cent": price}

    @app.put("/api/accounts/{account_id}/items/{item_id}/desc")
    async def change_desc(
        request: Request, account_id: str, item_id: str, body: DescIn
    ) -> dict[str, Any]:
        account_id = account(account_id)
        async with locks[account_id]:
            await items(request).change_desc(account_id, item_id, body.desc.strip())
        return {"ok": True}

    @app.post("/api/accounts/{account_id}/items/{item_id}/downshelf")
    async def downshelf(request: Request, account_id: str, item_id: str) -> dict[str, Any]:
        account_id = account(account_id)
        async with locks[account_id]:
            await items(request).downshelf(account_id, item_id)
        return {"ok": True}

    @app.delete("/api/accounts/{account_id}/items/{item_id}")
    async def delete_item(request: Request, account_id: str, item_id: str) -> dict[str, Any]:
        account_id = account(account_id)
        async with locks[account_id]:
            await items(request).delete(account_id, item_id)
        return {"ok": True}

    @app.get("/api/accounts/{account_id}/item-ops")
    async def item_ops(
        request: Request, account_id: str, item_id: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        ops = items(request).history(account(account_id), item_id, limit=min(limit, 500))
        return {"ops": [asdict(op) for op in ops]}

    if web_dist and (web_dist / "index.html").is_file():
        _mount_web(app, web_dist)
    return app


def _error_handler(status: int, code: str):
    async def handle(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"code": code, "message": str(exc)}, status_code=status)

    return handle


def _mount_web(app: FastAPI, dist: Path) -> None:
    """生产环境用：前端打包产物（web/dist）由这里直接托管，前端路由都回落到 index.html。"""
    dist = dist.resolve()
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        file = (dist / path).resolve()
        if path and file.is_file() and dist in file.parents:
            return FileResponse(file)
        return FileResponse(dist / "index.html")
