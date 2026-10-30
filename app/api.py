"""管理后台的 HTTP 接口（FastAPI）。没有登录鉴权，只能监听本机。

约定：
- 金额：响应里一律是分（`*_cent`，整数）；请求里的价格是元的字符串（"49"、"12.5"），服务端校验换算；
- 出错时返回 `{"code": ..., "message": ...}`，`code` 见 ERRORS；
- 同一个账号的平台请求串行执行，避免并发改动和凭证文件互相覆盖；
- 每个账号有限流和风控熔断（xianyu_core/guard.py），熔断状态和命令行共用。
"""

from __future__ import annotations

import asyncio
import contextlib
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

from app import db, keepalive
from app.accounts import DEFAULT_DIR, AccountNotFoundError, Accounts, make_guard
from app.items import ItemService, PriceGuardError
from app.login import (
    LoginNotFoundError,
    WebLogin,
    imported_text,
    parse_cookie_header,
    same_login,
)
from app.orders import OrderService
from xianyu_core.credential import Credential
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
    (LoginNotFoundError, 404, "login_not_found"),
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


class CookieImportIn(BaseModel):
    cookie: str  # 浏览器开发者工具里复制的 Cookie 请求头


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
    login_poll_interval: float = 2.0,
    login_check_interval: float | None = None,
) -> FastAPI:
    """`login_check_interval`：定时确认登录态的间隔（秒，见 app/keepalive.py），None 时不定时查。"""
    accounts = Accounts(accounts_dir, transport=transport, guard=guard or make_guard(db_path))
    locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def save_login(credential: Credential) -> None:
        # 等这个账号手上的平台请求做完再写，免得请求结束时把旧 cookie 写回来，盖掉刚扫到的凭证
        async with locks[credential.unb]:
            credential.save(accounts.path(credential.unb))

    logins = WebLogin(save_login, transport=transport, poll_interval=login_poll_interval)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        conn = db.connect(db_path)
        app.state.items = ItemService(conn, accounts, interval=interval)
        app.state.orders = OrderService(conn, accounts, interval=interval)
        keeper = None
        if login_check_interval:
            keeper = asyncio.create_task(
                keepalive.run_forever(accounts, locks, interval=login_check_interval)
            )
        try:
            yield
        finally:
            if keeper:
                keeper.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await keeper
            await logins.aclose()
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

    async def account_info(account_id: str, *, recheck: bool = False) -> dict[str, Any]:
        """账号和它的登录态。记下的登录态还能用就不请求平台。"""
        status = None if recheck else accounts.login_status(account_id)
        if status is None:
            async with locks[account_id]:
                status = None if recheck else accounts.login_status(account_id)
                status = status or await accounts.check(account_id)
        guard = accounts.guard
        return {
            "id": account_id,
            "saved_at": accounts.load(account_id).saved_at,
            "login": asdict(status),
            "block": _block(guard.block(account_id)) if guard else None,
        }

    @app.get("/api/accounts")
    async def list_accounts() -> dict[str, Any]:
        return {"accounts": [await account_info(account_id) for account_id in accounts.ids()]}

    @app.post("/api/accounts/{account_id}/check")
    async def check_account(account_id: str) -> dict[str, Any]:
        """马上确认一次登录态（会话过期时会先试免密续期）。"""
        return await account_info(account(account_id), recheck=True)

    @app.delete("/api/accounts/{account_id}/block")
    async def unblock_account(account_id: str) -> dict[str, Any]:
        """手动解除熔断。应该先在闲鱼 App 或网页上完成验证再解除。

        记下的"风控"登录态一并忘掉，下次拉账号列表时重新确认一次。
        """
        account_id = account(account_id)
        accounts.forget_login(account_id)
        return {"ok": bool(accounts.guard and accounts.guard.unblock(account_id))}

    @app.post("/api/login")
    async def start_login() -> dict[str, Any]:
        """扫码登录：取一张新二维码（上一张没扫完的作废），之后轮询 GET /api/login/{id}。"""
        progress, svg = await logins.start()
        return asdict(progress) | {"qr_svg": svg.decode()}

    @app.get("/api/login/{session_id}")
    async def login_progress(session_id: str) -> dict[str, Any]:
        """扫码进度。登录成功（phase=done）时凭证已经保存好了。"""
        return asdict(logins.progress(session_id))

    @app.delete("/api/login/{session_id}")
    async def cancel_login(session_id: str) -> dict[str, Any]:
        """关掉扫码弹窗时取消，后台不再替这张二维码轮询。"""
        return {"ok": await logins.cancel(session_id)}

    @app.post("/api/login/import")
    async def import_login(body: CookieImportIn) -> dict[str, Any]:
        """导入浏览器的登录（整串 Cookie 请求头）：管理后台里粘贴的，和浏览器插件自动同步的。

        和已保存的是同一份登录就直接返回（changed=false），不请求平台；
        不一样时先向平台确认能用再保存。过期的不续期、直接拒绝，不会拿失效的 cookie 盖掉现有凭证。
        """
        credential = parse_cookie_header(body.cookie)
        path = accounts.path(credential.unb)
        async with locks[credential.unb]:
            changed = not (path.exists() and same_login(credential, Credential.load(path)))
            if changed:
                credential = await accounts.verify(credential)
                credential.save(path)
            else:
                credential = Credential.load(path)
        return {
            "account_id": credential.unb,
            "changed": changed,
            "keep_login_until": credential.keep_login_until,
            "message": imported_text(credential),
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
