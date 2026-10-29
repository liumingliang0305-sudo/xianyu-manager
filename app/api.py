"""管理后台的 HTTP 接口（FastAPI）。没有登录鉴权，只能监听本机。

约定：
- 金额：响应里一律是分（`*_cent`，整数）；请求里的价格是元的字符串（"49"、"12.5"），服务端校验换算；
- 出错时返回 `{"code": ..., "message": ...}`，`code` 见 ERRORS；
- 同一个账号的平台请求串行执行，避免并发改动和凭证文件互相覆盖；
- 每个账号有限流和风控熔断（xianyu_core/guard.py），熔断状态和命令行共用。
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
from app.accounts import DEFAULT_DIR, AccountNotFoundError, Accounts, make_guard
from app.items import ItemService, PriceGuardError
from app.orders import OrderService
from xianyu_core.errors import (
    AuthRequiredError,
    CircuitOpenError,
    RateLimitedError,
    RiskControlError,
    XianyuError,
)
from xianyu_core.guard import Block, Guard
from xianyu_core.item import yuan_to_cent

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"

# 异常 → (HTTP 状态码, code)。按顺序匹配，子类要排在父类前面
ERRORS: list[tuple[type[Exception], int, str]] = [
    (PriceGuardError, 409, "price_guard"),
    (AccountNotFoundError, 404, "account_not_found"),
    (AuthRequiredError, 401, "auth_required"),
    (RiskControlError, 503, "risk_control"),
    (CircuitOpenError, 503, "circuit_open"),
    (RateLimitedError, 429, "rate_limited"),
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


class OrderAddIn(BaseModel):
    order: str  # 订单号，或者订单详情页的链接


class OrderPriceIn(BaseModel):
    fees: dict[str, str]  # 价格项 key → 新金额（元），没传的项保持原值
    force: bool = False


def create_app(
    *,
    db_path: str | Path = db.DEFAULT_PATH,
    accounts_dir: str | Path = DEFAULT_DIR,
    transport: httpx.AsyncBaseTransport | None = None,
    interval: float = 1.5,
    web_dist: Path | None = WEB_DIST,
    guard: Guard | None = None,
) -> FastAPI:
    accounts = Accounts(accounts_dir, transport=transport, guard=guard or make_guard(db_path))
    locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        conn = db.connect(db_path)
        app.state.items = ItemService(conn, accounts, interval=interval)
        app.state.orders = OrderService(conn, accounts, interval=interval)
        try:
            yield
        finally:
            conn.close()

    app = FastAPI(title="闲鱼管理系统", lifespan=lifespan)

    for exc_type, status, code in ERRORS:
        app.add_exception_handler(exc_type, _error_handler(status, code))

    def items(request: Request) -> ItemService:
        return request.app.state.items

    def orders(request: Request) -> OrderService:
        return request.app.state.orders

    def account(account_id: str) -> str:
        return accounts.resolve(account_id)

    @app.get("/api/accounts")
    async def list_accounts() -> dict[str, Any]:
        guard = accounts.guard
        return {
            "accounts": [
                {
                    "id": account_id,
                    "saved_at": accounts.load(account_id).saved_at,
                    "block": _block(guard.block(account_id)) if guard else None,
                }
                for account_id in accounts.ids()
            ]
        }

    @app.delete("/api/accounts/{account_id}/block")
    async def unblock_account(account_id: str) -> dict[str, Any]:
        """手动解除熔断。应该先在闲鱼 App 或网页上完成验证再解除。"""
        account_id = account(account_id)
        return {"ok": bool(accounts.guard and accounts.guard.unblock(account_id))}

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

    @app.get("/api/accounts/{account_id}/orders")
    async def list_orders(
        request: Request, account_id: str, status: str | None = None
    ) -> dict[str, Any]:
        records = orders(request).list(account(account_id), status=status)
        return {
            "orders": [asdict(r) for r in records],
            "last_synced_at": max((r.synced_at for r in records), default=None),
        }

    @app.post("/api/accounts/{account_id}/orders")
    async def add_order(request: Request, account_id: str, body: OrderAddIn) -> dict[str, Any]:
        account_id = account(account_id)
        async with locks[account_id]:
            record, added = await orders(request).add(account_id, body.order)
        return {"order": asdict(record), "added": added}

    @app.delete("/api/accounts/{account_id}/orders/{order_id}")
    async def remove_order(request: Request, account_id: str, order_id: str) -> dict[str, Any]:
        if not orders(request).remove(account(account_id), order_id):
            raise ValueError(f"本地没有订单 {order_id}")
        return {"ok": True}

    @app.post("/api/accounts/{account_id}/orders/sync")
    async def sync_orders(request: Request, account_id: str) -> dict[str, Any]:
        account_id = account(account_id)
        async with locks[account_id]:
            result = await orders(request).sync(account_id)
        return asdict(result)

    @app.get("/api/accounts/{account_id}/orders/{order_id}/detail")
    async def order_detail(request: Request, account_id: str, order_id: str) -> dict[str, Any]:
        account_id = account(account_id)
        async with locks[account_id]:
            detail = await orders(request).fetch_detail(account_id, order_id)
        data = asdict(detail)
        data.pop("raw")
        return data

    @app.get("/api/accounts/{account_id}/orders/{order_id}/price-fields")
    async def order_price_fields(
        request: Request, account_id: str, order_id: str
    ) -> dict[str, Any]:
        account_id = account(account_id)
        async with locks[account_id]:
            fields = await orders(request).price_fields(account_id, order_id)
        return {"fields": [asdict(f) for f in fields]}

    @app.post("/api/accounts/{account_id}/orders/{order_id}/price")
    async def change_order_price(
        request: Request, account_id: str, order_id: str, body: OrderPriceIn
    ) -> dict[str, Any]:
        account_id = account(account_id)
        fees = {key: yuan_to_cent(value) for key, value in body.fees.items()}
        async with locks[account_id]:
            old, new = await orders(request).change_price(
                account_id, order_id, fees, force=body.force
            )
        return {"old_price_cent": old, "price_cent": new}

    @app.get("/api/accounts/{account_id}/order-ops")
    async def order_ops(
        request: Request, account_id: str, order_id: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        ops = orders(request).history(account(account_id), order_id, limit=min(limit, 500))
        return {"ops": [asdict(op) for op in ops]}

    if web_dist and (web_dist / "index.html").is_file():
        _mount_web(app, web_dist)
    return app


def _block(block: Block | None) -> dict[str, Any] | None:
    return asdict(block) if block else None


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
