"""管理后台的 HTTP 接口（FastAPI）。

登录鉴权（app/auth.py、app/users.py）：
- /api/auth/login 用户名密码登录，返回访问令牌（JWT，15 分钟），刷新令牌放进 HttpOnly cookie；
  访问令牌过期了 POST /api/auth/refresh 换新的；
- 别的接口都要带 `Authorization: Bearer <访问令牌>`，没带、过期、作废返回 401 unauthenticated。
  默认要登录：接口挂在 `authed` 上，只有登录本身、插件推登录和 /api/health 挂在 app 上；
- 数据隔离：每个用户只看得到自己名下的闲鱼账号，/api/accounts/{id}/... 碰到别人的账号
  和不存在的一样，404；
- 浏览器插件推登录（/api/login/import）、导入拼多多商品（POST /api/pdd/goods）用插件令牌，
  只能做这两件事、推到自己名下。

约定：
- 金额：响应里一律是分（`*_cent`，整数）；请求里的价格是元的字符串（"49"、"12.5"），服务端校验换算；
- 出错时返回 `{"code": ..., "message": ...}`，`code` 见 ERRORS；
- 同一个账号的平台请求串行执行，避免并发改动和凭证文件互相覆盖；
- 每个账号有限流和风控熔断（xianyu_core/guard.py），熔断状态存在文件里，重启后还在；
- 消息：`messages=True` 时每个账号连一条即时消息长连接（app/messages.py），新消息通过
  GET .../messages/stream（Server-Sent Events）通知页面；回复也走这条连接；
- 发布：先存草稿（app/drafts.py），图片传到 .../drafts/{id}/images（请求体就是图片文件），
  存在本机 data/images；点发布时才传到闲鱼。发货地按名字搜用自己的高德 key（app/places.py）；
- 编辑商品：POST .../items/{id}/edit 拿到这个商品的编辑草稿，之后和发布一样用
  .../drafts/{id}/... 改，.../drafts/{id}/publish 提交到闲鱼；
- 重新上架：POST .../items/{id}/relist 照下架、卖掉的商品抄一个发新商品的草稿，在发布页改好了发；
  请求体 {"publish": true} 是抄完直接发；
- 账号矩阵（app/matrix.py）：GET /api/accounts 每个账号带上昵称头像、备注、
  在售 / 待发货 / 未读几个数和即时消息状态；PUT .../remark 改备注，
  DELETE /api/accounts/{id} 移除账号（删凭证和归属，本地数据留着）；
- 自动同步（app/autosync.py）：`auto_sync=True` 时商品、订单按交易消息、打开页面、定时三条路
  自动同步，.../autosync 看状态、改设置；手动同步（.../items/sync、.../orders/sync）也记在一起。
  同步开始、结束都通过消息的推送流发一条 sync 事件。
- 拼多多货源（app/pdd.py）：浏览器插件在拼多多商品页上读出商品推到 POST /api/pdd/goods，
  按用户存（不跟闲鱼账号走），图片下载到本机；POST /api/accounts/{id}/pdd/{goods_id}/draft
  照它在这个账号建一个发布草稿。
- 搜索暂存（app/stash.py）：搜索页上点「暂存」把搜索结果里的一条存下来（POST /api/search/stash），
  按用户存、名下账号共用。商品详情后台不拉（pc.detail 看别人的商品一调就要滑块），浏览器插件在用户
  自己的 Chrome 里拿到交给页面，页面发到 POST /api/search/stash/{item_id}/detail。都不请求闲鱼。
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import json
from collections import defaultdict
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

import httpx
import websockets
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from app import auth, db, keepalive
from app import images as image_store
from app.accounts import DEFAULT_DIR, AccountNotFoundError, Accounts, LoginState, make_guard
from app.auth import (
    MAX_FAILURES_PER_IP,
    AccessTokens,
    LoginFailedError,
    LoginLimiter,
    NotSignedInError,
    TooManyAttemptsError,
    client_ip,
)
from app.autosync import INTERVAL_CHOICES, ITEMS, JOBS, MANUAL, AutoSync
from app.drafts import Draft, DraftImage, DraftNotFoundError, DraftService, address_option
from app.items import ItemService, PriceGuardError
from app.login import (
    LoginNotFoundError,
    WebLogin,
    imported_text,
    parse_cookie_header,
    same_login,
)
from app.matrix import AccountMatrix, AccountStats
from app.messages import CLOSING, ImOfflineError, MessageHub, MessageStore
from app.orders import OrderFeed, OrderRecord, OrderService
from app.pdd import PddGoods, PddGoodsNotFoundError, PddImage, PddService, draft_text
from app.places import PlaceSearch, PlacesUnavailableError
from app.search import SearchService
from app.stash import StashedItem, StashNotFoundError, StashService
from app.users import PLUGIN_TOKEN_PREFIX, REFRESH_IDLE, AccountTakenError, User, Users
from xianyu_core.credential import Credential
from xianyu_core.errors import (
    AuthRequiredError,
    CircuitOpenError,
    ImSendUncertainError,
    RateLimitedError,
    RiskControlError,
    XianyuError,
)
from xianyu_core.guard import Block, Guard
from xianyu_core.item import yuan_to_cent
from xianyu_core.order import SOLD
from xianyu_core.publish import PublishBlockedError
from xianyu_core.search import SearchQuery

WEB_DIST = Path(__file__).resolve().parent.parent / "web" / "dist"
# 消息推送流多久没事件就发一行注释，顺便发现页面已经关了、用户已经没有权限了
SSE_PING = 15.0
# 刷新令牌的 cookie：只发给 /api/auth 下面的接口，页面脚本读不到
REFRESH_COOKIE = "xym_refresh"
REFRESH_PATH = "/api/auth"

# 从消息里补订单时每笔的结果：新记下、刷新了、本地已结束没拉、平台查不到、平台报错
SCAN_RESULTS = ("added", "updated", "skipped", "missing", "failed")

# 页面只加载自己的脚本和样式（不认注入进来的脚本），不让别的网站用 iframe 嵌进去。
# 商品图、聊天图在阿里的 CDN 上，图片放开 https；商品图的地址是 http 的，
# upgrade-insecure-requests 让浏览器换成 https。草稿的图片要带访问令牌取，取回来是 blob: 地址。
# 样式要 'unsafe-inline'：提示框（sonner）、主题切换（next-themes）会插 <style>。
CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data: blob: https:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "upgrade-insecure-requests",
    ]
)

SECURITY_HEADERS = [
    (b"content-security-policy", CONTENT_SECURITY_POLICY.encode()),
    (b"x-content-type-options", b"nosniff"),
    (b"x-frame-options", b"DENY"),
    (b"referrer-policy", b"no-referrer"),
]

# 异常 → (HTTP 状态码, code)。按顺序匹配，子类要排在父类前面
ERRORS: list[tuple[type[Exception], int, str]] = [
    (NotSignedInError, 401, "unauthenticated"),
    (LoginFailedError, 401, "login_failed"),
    (TooManyAttemptsError, 429, "too_many_attempts"),
    (AccountTakenError, 409, "account_taken"),
    (PriceGuardError, 409, "price_guard"),
    (AccountNotFoundError, 404, "account_not_found"),
    (DraftNotFoundError, 404, "draft_not_found"),
    (PddGoodsNotFoundError, 404, "pdd_goods_not_found"),
    (StashNotFoundError, 404, "stash_not_found"),
    (LoginNotFoundError, 404, "login_not_found"),
    (ImOfflineError, 503, "im_offline"),
    (AuthRequiredError, 401, "auth_required"),
    (RiskControlError, 503, "risk_control"),
    (CircuitOpenError, 503, "circuit_open"),
    (RateLimitedError, 429, "rate_limited"),
    (ImSendUncertainError, 504, "send_uncertain"),
    (PublishBlockedError, 409, "publish_blocked"),
    (PlacesUnavailableError, 503, "places_unavailable"),
    (XianyuError, 502, "platform_error"),
    (ValueError, 400, "invalid"),
]


class SignInIn(BaseModel):
    username: str
    password: str


class PasswordIn(BaseModel):
    old_password: str
    new_password: str


class SyncIn(BaseModel):
    stats: bool = False


class PriceIn(BaseModel):
    price: str
    orig_price: str | None = None
    force: bool = False


class PublishIn(BaseModel):
    force: bool = False  # 编辑草稿改价超过保护线也提交


class RelistIn(BaseModel):
    publish: bool = False  # True 抄完直接发到闲鱼，False 只抄成草稿，在发布页改好了再发


class OrderAddIn(BaseModel):
    order: str  # 订单号，或者订单详情页的链接


class OrderSyncIn(BaseModel):
    side: str = "sold"  # sold 卖出的，bought 买到的


class AutoSyncIn(BaseModel):
    enabled: bool | None = None
    interval: float | None = None  # 秒，只能是 INTERVAL_CHOICES 里的


class VisitIn(BaseModel):
    jobs: list[str]


class RemarkIn(BaseModel):
    remark: str


class CookieImportIn(BaseModel):
    cookie: str  # 浏览器开发者工具里复制的 Cookie 请求头


class PddImportIn(BaseModel):
    # 拼多多商品页 window.rawData.store.initDataObj 里插件挑出来的字段（见 extension/pdd.js）
    goods: dict[str, Any]
    mall: dict[str, Any] | None = None


class OrderPriceIn(BaseModel):
    fees: dict[str, str]  # 价格项 key → 新金额（元），没传的项保持原值
    force: bool = False


class FetchIn(BaseModel):
    older: bool = False  # True 往前翻一页，False 拉最新的


class SendIn(BaseModel):
    text: str


class SearchIn(BaseModel):
    keyword: str
    page: int = 1
    sort: str = ""  # 见 xianyu_core/search.py 的 SORTS，空是综合
    publish_days: int | None = None
    min_price: str | None = None  # 元，空表示不限
    max_price: str | None = None
    filters: list[str] = []  # 见 QUICK_FILTERS


class StashIn(BaseModel):
    # 搜索接口返回的一条（字段一样，没有 url），加上用哪个词搜到的
    item_id: str
    title: str
    price_cent: int
    orig_price_cent: int | None = None
    pic_url: str = ""
    area: str = ""
    seller_nick: str = ""
    want_count: int | None = None
    published_at: float | None = None
    free_ship: bool = False
    tags: list[str] = []
    seller_tags: list[str] = []
    keyword: str = ""


class StashDetailIn(BaseModel):
    # 浏览器插件从商品页拿到的 pc.detail 的 itemDO（只挑了要用的字段，见 extension/goofish.js）
    item: dict[str, Any]


class DraftIn(BaseModel):
    description: str = ""
    price: str | None = None  # 元，空是还没填
    orig_price: str | None = None
    post_mode: str = "free"  # 见 xianyu_core/publish.py 的 POST_MODES
    post_price: str | None = None  # 一口价的邮费，元
    take_self: bool = False
    images: list[str] = []  # 图片的新顺序（sha）
    address: dict[str, Any] | None = None  # .../publish/addresses 返回的 address


class ChooseIn(BaseModel):
    property_id: str
    keys: list[str]


def create_app(
    *,
    database_url: str | None = None,
    accounts_dir: str | Path = DEFAULT_DIR,
    images_dir: str | Path = image_store.DEFAULT_DIR,
    amap_key: str | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    interval: float = 1.5,
    web_dist: Path | None = WEB_DIST,
    guard: Guard | None = None,
    login_poll_interval: float = 2.0,
    login_check_interval: float | None = None,
    messages: bool = False,
    im_connect: Callable[..., Awaitable[Any]] = websockets.connect,
    im_options: dict[str, Any] | None = None,
    auth_key: str | Path | None = None,
    auto_sync: bool = False,
    auto_sync_options: dict[str, Any] | None = None,
) -> FastAPI:
    """`database_url`：数据库连接串，不给时用 db.default_url()（环境变量 DATABASE_URL）。
    `guard`：限流和熔断，不给时用 data/guard.json 里的熔断状态。
    `login_check_interval`：定时确认登录态的间隔（秒，见 app/keepalive.py），None 时不定时查。
    `auth_key`：访问令牌的签名密钥文件，不给时用 data/auth.key，没有就生成。
    `images_dir`：草稿图片放哪，默认 data/images。
    `amap_key`：高德"Web 服务"的 key，发货地按名字搜用；没有时只能从常用地址里选
    （见 app/places.py）。

    `messages`：连不连即时消息。不连时消息接口只能看本地库里已有的。
    `im_connect`、`im_options` 给测试换假的服务端、调快节奏（见 MessageHub）。

    `auto_sync`：开不开自动同步（app/autosync.py）。不开时手动同步照样记结果。
    `auto_sync_options` 给测试调快节奏（见 AutoSync）。
    """
    accounts = Accounts(accounts_dir, transport=transport, guard=guard or make_guard())
    searcher = SearchService(accounts)
    places = PlaceSearch(amap_key, transport=transport)
    locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
    tokens = AccessTokens(auth.load_key(auth_key or auth.KEY_PATH))
    limiter = LoginLimiter()
    ip_limiter = LoginLimiter(max_failures=MAX_FAILURES_PER_IP)

    async def save_login(credential: Credential, user_id: int) -> None:
        # 等这个账号手上的平台请求做完再写，免得请求结束时把旧 cookie 写回来，盖掉刚扫到的凭证
        async with locks[credential.unb]:
            # 归别人的账号不写凭证，免得扫码就把别人名下账号的登录换掉
            app.state.users.claim(credential.unb, user_id)
            credential.save(accounts.path(credential.unb))
        app.state.messages.resume(credential.unb)

    # 每个用户一个扫码会话，互不干扰；进度只有发起的用户查得到
    logins: dict[int, WebLogin] = {}

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        conn = db.connect(database_url)
        app.state.users = Users(conn)
        app.state.matrix = AccountMatrix(conn, accounts)
        app.state.items = ItemService(conn, accounts, interval=interval)
        app.state.orders = OrderService(conn, accounts, interval=interval)
        images = image_store.ImageStore(images_dir)
        app.state.drafts = DraftService(conn, accounts, app.state.items, images)
        app.state.pdd = PddService(conn, images, transport=transport)
        app.state.stash = StashService(conn)
        # 同步开始、结束推给页面（hub 在下面建，回调时才用到）
        autosync = AutoSync(
            conn,
            accounts,
            locks,
            app.state.items,
            app.state.orders,
            publish=lambda account_id, event: hub.publish(account_id, event),
            **(auto_sync_options or {}),
        )
        app.state.autosync = autosync

        def order_status_changed(account_id: str, order: OrderRecord) -> None:
            # 卖出的订单新记下、状态变了，商品多半也变了（卖掉了、退款后回到在售）
            if order.side == SOLD:
                autosync.later(account_id, ITEMS)

        # 交易消息里的订单自动记进本地库，记下了推给页面
        feed = OrderFeed(
            app.state.orders,
            locks,
            interval=interval,
            on_change=lambda account_id: hub.publish(account_id, {"type": "orders"}),
            on_status=order_status_changed,
        )
        hub = MessageHub(
            MessageStore(conn),
            accounts,
            locks,
            connect=im_connect,
            on_order=feed.notify,
            **(im_options or {}),
        )
        app.state.messages = hub
        if messages:
            await hub.start()
        if auto_sync:
            autosync.start()
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
            await autosync.aclose()
            await hub.aclose()
            await feed.aclose()
            for login in logins.values():
                await login.aclose()
            conn.close()

    # 接口文档页（/docs、/openapi.json）不开：对公网开放以后，不给别人一份现成的接口清单
    app = FastAPI(
        title="闲鱼管理系统", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None
    )
    app.add_middleware(SecurityHeaders)

    for exc_type, status, code in ERRORS:
        app.add_exception_handler(exc_type, _error_handler(status, code))

    def users(request: Request) -> Users:
        return request.app.state.users

    def items(request: Request) -> ItemService:
        return request.app.state.items

    def orders(request: Request) -> OrderService:
        return request.app.state.orders

    def drafts(request: Request) -> DraftService:
        return request.app.state.drafts

    def pdd(request: Request) -> PddService:
        return request.app.state.pdd

    def stash(request: Request) -> StashService:
        return request.app.state.stash

    def hub(request: Request) -> MessageHub:
        return request.app.state.messages

    def matrix(request: Request) -> AccountMatrix:
        return request.app.state.matrix

    def autosync(request: Request) -> AutoSync:
        return request.app.state.autosync

    def autosync_info(request: Request, account_id: str) -> dict[str, Any]:
        service = autosync(request)
        return {
            "active": service.active,
            "paused": service.paused(account_id),
            "intervals": list(INTERVAL_CHOICES),
            "jobs": [asdict(s) for s in service.statuses(account_id)],
        }

    def current_user(request: Request) -> User:
        """请求头里的访问令牌对应的用户。没带、过期、作废（改了密码、停用了）都是 401。"""
        user_id, version = tokens.verify(_bearer(request))
        user = users(request).get(user_id)
        if user is None or user.disabled or user.token_version != version:
            raise NotSignedInError("登录已经失效，重新登录")
        request.state.user = user
        return user

    def importing_user(request: Request) -> User:
        """推登录的用户：浏览器插件带插件令牌，管理后台页面里粘贴的带访问令牌。"""
        token = _bearer(request)
        if not token.startswith(PLUGIN_TOKEN_PREFIX):
            return current_user(request)
        user = users(request).by_plugin_token(token)
        if user is None:
            raise NotSignedInError("插件令牌不对或者已经作废，到管理后台重新生成")
        request.state.user = user
        return user

    # 要登录的接口都挂在这上面，最后再加进 app
    authed = APIRouter(dependencies=[Depends(current_user)])

    def account(request: Request, account_id: str) -> str:
        """当前用户名下的账号。别人的账号和不存在的一样，都是 404，不让人知道别人有没有这个号。"""
        if not users(request).owns(request.state.user.id, account_id):
            raise AccountNotFoundError(f"找不到账号 {account_id}")
        return accounts.resolve(account_id)

    def web_login(request: Request) -> WebLogin:
        user_id = request.state.user.id
        if user_id not in logins:
            logins[user_id] = WebLogin(
                functools.partial(save_login, user_id=user_id),
                transport=transport,
                poll_interval=login_poll_interval,
            )
        return logins[user_id]

    def session(request: Request, response: Response, user: User, refresh: str) -> dict[str, Any]:
        """登录成功：刷新令牌写进 cookie（顺延有效期），返回访问令牌。"""
        response.set_cookie(
            REFRESH_COOKIE,
            refresh,
            max_age=int(REFRESH_IDLE),
            path=REFRESH_PATH,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return {
            "access_token": tokens.issue(user),
            "expires_in": tokens.ttl,
            "user": {"id": user.id, "username": user.username},
        }

    async def account_info(
        request: Request,
        account_id: str,
        *,
        recheck: bool = False,
        stats: AccountStats | None = None,
        added_at: float | None = None,
    ) -> dict[str, Any]:
        """账号、登录态，和账号矩阵里显示的资料、备注、概况、即时消息状态。

        记下的登录态还能用就不请求平台。登录有效、资料该拉了（见 AccountMatrix.profile_due），
        或者是手动重新检查（recheck）时，顺带拉一次资料。
        """
        book = matrix(request)
        status = None if recheck else accounts.login_status(account_id)
        if status is None:
            async with locks[account_id]:
                status = None if recheck else accounts.login_status(account_id)
                status = status or await accounts.check(account_id)
        if status.state is LoginState.VALID and (recheck or book.profile_due(account_id)):
            async with locks[account_id]:
                if recheck or book.profile_due(account_id):
                    await book.refresh_profile(account_id)
        if stats is None:
            stats = book.stats([account_id])[account_id]
        if added_at is None:
            added_at = users(request).accounts_added(request.state.user.id).get(account_id)
        profile = book.profile(account_id)
        guard = accounts.guard
        return {
            "id": account_id,
            "saved_at": accounts.load(account_id).saved_at,
            "added_at": added_at,
            "login": asdict(status),
            "block": _block(guard.block(account_id)) if guard else None,
            "profile": {
                "nick": profile.nick,
                "avatar": profile.avatar,
                "ip_location": profile.ip_location,
                "seller_credit": profile.seller_credit,
                "followers": profile.followers,
                "fetched_at": profile.fetched_at,
            },
            "remark": profile.remark,
            "stats": asdict(stats),
            "im": hub(request).status(account_id),
        }

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        """不用登录：发版脚本用它确认后台起来了。"""
        return {"ok": True}

    @app.post("/api/auth/login")
    async def sign_in(request: Request, response: Response, body: SignInIn) -> dict[str, Any]:
        """用户名密码登录。15 分钟里同一个用户名输错 5 次、或者同一个 IP 输错 20 次，
        要等到这 15 分钟过去。"""
        username = body.username.strip().lower()
        ip = client_ip(request.client.host if request.client else None)
        limiter.check(username)
        if ip:
            ip_limiter.check(ip)
        user = await users(request).authenticate(username, body.password)
        if user is None:
            limiter.failed(username)
            if ip:
                ip_limiter.failed(ip)
            raise LoginFailedError("用户名或密码不对")
        limiter.succeeded(username)
        return session(request, response, user, users(request).new_refresh_token(user.id))

    @app.post("/api/auth/refresh")
    async def refresh(request: Request, response: Response) -> Any:
        """拿 cookie 里的刷新令牌换一个新的访问令牌。刷新令牌没了、过期了时 401，要重新登录。"""
        token = request.cookies.get(REFRESH_COOKIE)
        user = users(request).use_refresh_token(token) if token else None
        if user is None:
            resp = JSONResponse(
                {"code": "unauthenticated", "message": "登录过期了，重新登录"}, status_code=401
            )
            resp.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH)
            return resp
        return session(request, response, user, token)

    @app.post("/api/auth/logout")
    async def sign_out(request: Request, response: Response) -> dict[str, Any]:
        """退出登录：删掉这个浏览器的刷新令牌。页面自己丢掉访问令牌。"""
        token = request.cookies.get(REFRESH_COOKIE)
        if token:
            users(request).drop_refresh_token(token)
        response.delete_cookie(REFRESH_COOKIE, path=REFRESH_PATH)
        return {"ok": True}

    @authed.post("/api/auth/password")
    async def change_password(
        request: Request, response: Response, body: PasswordIn
    ) -> dict[str, Any]:
        """改密码。别处的登录全部作废，这个浏览器换一套新令牌，不用重新登录。"""
        user: User = request.state.user
        if await users(request).authenticate(user.username, body.old_password) is None:
            raise ValueError("原密码不对")
        user = await users(request).set_password(user.id, body.new_password)
        return session(request, response, user, users(request).new_refresh_token(user.id))

    @authed.get("/api/auth/plugin-token")
    async def plugin_token_info(request: Request) -> dict[str, Any]:
        """有没有生成过插件令牌。令牌本身只在生成时给一次。"""
        return {"created_at": request.state.user.plugin_token_at}

    @authed.post("/api/auth/plugin-token")
    async def new_plugin_token(request: Request) -> dict[str, Any]:
        """生成浏览器插件用的令牌，之前的作废。"""
        token, created_at = users(request).new_plugin_token(request.state.user.id)
        return {"token": token, "created_at": created_at}

    @authed.delete("/api/auth/plugin-token")
    async def drop_plugin_token(request: Request) -> dict[str, Any]:
        users(request).drop_plugin_token(request.state.user.id)
        return {"ok": True}

    @authed.get("/api/accounts")
    async def list_accounts(request: Request) -> dict[str, Any]:
        """当前用户名下的账号，先归到名下的在前。凭证文件不在了的不列。"""
        added = users(request).accounts_added(request.state.user.id)
        present = set(accounts.ids())
        ids = [a for a in added if a in present]
        stats = matrix(request).stats(ids)
        return {
            "accounts": [
                await account_info(request, a, stats=stats[a], added_at=added[a]) for a in ids
            ]
        }

    @authed.post("/api/accounts/{account_id}/check")
    async def check_account(request: Request, account_id: str) -> dict[str, Any]:
        """马上确认一次登录态（会话过期时会先试免密续期），登录有效时顺带重拉资料。"""
        return await account_info(request, account(request, account_id), recheck=True)

    @authed.put("/api/accounts/{account_id}/remark")
    async def set_remark(request: Request, account_id: str, body: RemarkIn) -> dict[str, Any]:
        """给账号写个备注（最多 20 个字），空字符串是清掉。只存在这里，平台上看不到。"""
        account_id = account(request, account_id)
        matrix(request).set_remark(account_id, body.remark)
        return await account_info(request, account_id)

    @authed.delete("/api/accounts/{account_id}")
    async def remove_account(request: Request, account_id: str) -> dict[str, Any]:
        """移除账号：删掉保存的登录凭证，不再归当前用户，断开即时消息，资料和备注一起删。

        本地记下的商品、订单、消息、草稿留着（按账号存的），重新登录这个账号以后还在；
        熔断状态也留着。闲鱼上的东西一样不动。
        """
        account_id = account(request, account_id)
        # 等这个账号手上的平台请求做完再删，免得请求结束时把凭证写回来
        async with locks[account_id]:
            accounts.remove(account_id)
            users(request).release(account_id, request.state.user.id)
            matrix(request).forget(account_id)
        await hub(request).stop(account_id)
        return {"ok": True}

    @authed.delete("/api/accounts/{account_id}/block")
    async def unblock_account(request: Request, account_id: str) -> dict[str, Any]:
        """手动解除熔断。应该先在闲鱼 App 或网页上完成验证再解除。

        记下的"风控"登录态一并忘掉，下次拉账号列表时重新确认一次。
        """
        account_id = account(request, account_id)
        accounts.forget_login(account_id)
        ok = bool(accounts.guard and accounts.guard.unblock(account_id))
        hub(request).resume(account_id)
        return {"ok": ok}

    @authed.post("/api/login")
    async def start_login(request: Request) -> dict[str, Any]:
        """扫码登录：取一张新二维码（这个用户上一张没扫完的作废），之后轮询 GET /api/login/{id}。

        扫到的账号归当前用户；已经归别人的，进度是 failed，凭证不保存。
        """
        progress, svg = await web_login(request).start()
        return asdict(progress) | {"qr_svg": svg.decode()}

    @authed.get("/api/login/{session_id}")
    async def login_progress(request: Request, session_id: str) -> dict[str, Any]:
        """扫码进度。登录成功（phase=done）时凭证已经保存好了。"""
        return asdict(web_login(request).progress(session_id))

    @authed.delete("/api/login/{session_id}")
    async def cancel_login(request: Request, session_id: str) -> dict[str, Any]:
        """关掉扫码弹窗时取消，后台不再替这张二维码轮询。"""
        return {"ok": await web_login(request).cancel(session_id)}

    @app.post("/api/login/import", dependencies=[Depends(importing_user)])
    async def import_login(request: Request, body: CookieImportIn) -> dict[str, Any]:
        """导入浏览器的登录（整串 Cookie 请求头）：管理后台里粘贴的，和浏览器插件自动同步的。

        账号已经归别人的直接拒绝（409），不请求平台。
        和已保存的是同一份登录就直接返回（changed=false），不请求平台；
        不一样时先向平台确认能用再保存。过期的不续期、直接拒绝，不会拿失效的 cookie 盖掉现有凭证。
        cookie 里没有 unb 时先找出是哪个账号（Accounts.identify），已保存的登录里找不到才问平台。
        """
        credential = parse_cookie_header(body.cookie)
        verified = False
        if not credential.unb:
            credential, verified = await accounts.identify(credential)
        user_id = request.state.user.id
        path = accounts.path(credential.unb)
        async with locks[credential.unb]:
            if users(request).owner(credential.unb) not in (None, user_id):
                raise AccountTakenError(credential.unb)
            changed = not (path.exists() and same_login(credential, Credential.load(path)))
            if changed and not verified:
                credential = await accounts.verify(credential)
            users(request).claim(credential.unb, user_id)
            if changed:
                credential.save(path)
            else:
                credential = Credential.load(path)
        if changed:
            hub(request).resume(credential.unb)
        return {
            "account_id": credential.unb,
            "changed": changed,
            "keep_login_until": credential.keep_login_until,
            "message": imported_text(credential),
        }

    @app.post("/api/pdd/goods", dependencies=[Depends(importing_user)])
    async def import_pdd_goods(request: Request, body: PddImportIn) -> dict[str, Any]:
        """导入一个拼多多商品（浏览器插件推的），下载图片。已经导入过的是更新。

        返回商品、是不是新加的（added），和图片下载了几张（saved / total，同一个地址算一张）。
        """
        goods, added = await pdd(request).ingest(request.state.user.id, body.model_dump())
        return {"goods": _pdd_goods(goods, detail=True), "added": added, **_pdd_images(goods)}

    @authed.get("/api/pdd/goods")
    async def list_pdd_goods(request: Request) -> dict[str, Any]:
        return {"goods": [_pdd_goods(g) for g in pdd(request).list(request.state.user.id)]}

    @authed.get("/api/pdd/goods/{goods_id}")
    async def pdd_goods_detail(request: Request, goods_id: str) -> dict[str, Any]:
        return _pdd_goods(pdd(request).get(request.state.user.id, goods_id), detail=True)

    @authed.post("/api/pdd/goods/{goods_id}/images")
    async def fetch_pdd_images(request: Request, goods_id: str) -> dict[str, Any]:
        """把上次没下载下来的图再下一次。"""
        goods = await pdd(request).fetch_images(request.state.user.id, goods_id)
        return {"goods": _pdd_goods(goods, detail=True), **_pdd_images(goods)}

    @authed.delete("/api/pdd/goods/{goods_id}")
    async def delete_pdd_goods(request: Request, goods_id: str) -> dict[str, Any]:
        pdd(request).delete(request.state.user.id, goods_id)
        return {"ok": True}

    @authed.post("/api/accounts/{account_id}/pdd/{goods_id}/draft")
    async def draft_from_pdd(request: Request, account_id: str, goods_id: str) -> dict[str, Any]:
        """照拼多多货源在这个账号建一个发布草稿：描述是标题、商品参数和可选的规格，
        图片是下载下来的主图（最多 9 张），价格、邮费、分类在发布页填。不请求闲鱼。"""
        account_id = account(request, account_id)
        service = pdd(request)
        goods = service.get(request.state.user.id, goods_id)
        images = service.draft_images(goods)
        if not images:
            raise ValueError("主图还没下载下来，先在货源里点「重新下载图片」")
        draft = drafts(request).create(
            account_id,
            draft_text(goods),
            images=[DraftImage(img.sha, img.width or 0, img.height or 0) for img in images],
            pdd_goods_id=goods.goods_id,
        )
        return _draft(draft)

    @authed.get("/api/accounts/{account_id}/items")
    async def list_items(
        request: Request, account_id: str, status: int | None = None, include_gone: bool = False
    ) -> dict[str, Any]:
        records = items(request).list(
            account(request, account_id), status=status, include_gone=include_gone
        )
        return {
            "items": [asdict(r) for r in records],
            "last_synced_at": max((r.synced_at for r in records), default=None),
        }

    @authed.post("/api/accounts/{account_id}/items/sync")
    async def sync_items(request: Request, account_id: str, body: SyncIn) -> dict[str, Any]:
        account_id = account(request, account_id)
        result = await autosync(request).run(account_id, ITEMS, MANUAL, stats=body.stats)
        return asdict(result)

    @authed.get("/api/accounts/{account_id}/items/{item_id}/detail")
    async def item_detail(request: Request, account_id: str, item_id: str) -> dict[str, Any]:
        account_id = account(request, account_id)
        async with locks[account_id]:
            detail = await items(request).fetch_detail(account_id, item_id)
        data = asdict(detail)
        data.pop("raw")
        return data

    @authed.post("/api/accounts/{account_id}/items/{item_id}/price")
    async def change_price(
        request: Request, account_id: str, item_id: str, body: PriceIn
    ) -> dict[str, Any]:
        account_id = account(request, account_id)
        price = yuan_to_cent(body.price)
        orig = yuan_to_cent(body.orig_price) if body.orig_price else None
        async with locks[account_id]:
            old = await items(request).change_price(
                account_id, item_id, price, orig_price_cent=orig, force=body.force
            )
        return {"old_price_cent": old, "price_cent": price}

    @authed.post("/api/accounts/{account_id}/items/{item_id}/edit")
    async def edit_item(request: Request, account_id: str, item_id: str) -> dict[str, Any]:
        """这个商品的编辑草稿，没有就按闲鱼上现在的样子建一个（请求一次 editDetail）。"""
        account_id = account(request, account_id)
        async with locks[account_id]:
            draft = await drafts(request).edit_item(account_id, item_id)
        return _draft(draft)

    @authed.post("/api/accounts/{account_id}/items/{item_id}/relist")
    async def relist_item(
        request: Request, account_id: str, item_id: str, body: RelistIn | None = None
    ) -> dict[str, Any]:
        """照这个商品抄一个发新商品的草稿（请求一次 editDetail），有没发的就接着用。

        `publish` 是抄完直接发；发失败了草稿留在发布页的列表里，原因记在上面。
        """
        account_id = account(request, account_id)
        async with locks[account_id]:
            draft = await drafts(request).relist_item(account_id, item_id)
            if body and body.publish:
                draft = await drafts(request).publish(account_id, draft.id)
        return _draft(draft)

    @authed.post("/api/accounts/{account_id}/items/{item_id}/downshelf")
    async def downshelf(request: Request, account_id: str, item_id: str) -> dict[str, Any]:
        account_id = account(request, account_id)
        async with locks[account_id]:
            await items(request).downshelf(account_id, item_id)
        return {"ok": True}

    @authed.delete("/api/accounts/{account_id}/items/{item_id}")
    async def delete_item(request: Request, account_id: str, item_id: str) -> dict[str, Any]:
        account_id = account(request, account_id)
        async with locks[account_id]:
            await items(request).delete(account_id, item_id)
        return {"ok": True}

    @authed.get("/api/accounts/{account_id}/item-ops")
    async def item_ops(
        request: Request, account_id: str, item_id: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        ops = items(request).history(account(request, account_id), item_id, limit=min(limit, 500))
        return {"ops": [asdict(op) for op in ops]}

    @authed.post("/api/accounts/{account_id}/search")
    async def search(request: Request, account_id: str, body: SearchIn) -> dict[str, Any]:
        """用这个账号搜闲鱼上的商品，一次一页（30 条），结果不进本地库。"""
        account_id = account(request, account_id)
        query = SearchQuery(
            keyword=body.keyword,
            sort=body.sort,
            publish_days=body.publish_days,
            price_min_cent=_yuan_or_none(body.min_price),
            price_max_cent=_yuan_or_none(body.max_price),
            filters=tuple(body.filters),
        )
        async with locks[account_id]:
            result = await searcher.search(account_id, query, body.page)
        items = [asdict(card) for card in result.items]
        for item in items:
            item.pop("raw")
        return {
            "items": items,
            "page": body.page,
            "has_next": result.has_next,
            "total": result.total,
        }

    @authed.get("/api/search/stash")
    async def list_stash(request: Request) -> dict[str, Any]:
        """暂存的商品，最近暂存的在前。"""
        return {"items": [_stashed(it) for it in stash(request).list(request.state.user.id)]}

    @authed.post("/api/search/stash")
    async def save_stash(request: Request, body: StashIn) -> dict[str, Any]:
        """暂存搜索结果里的一条，不请求闲鱼。已经暂存过的更新成这次的样子。"""
        item, added = stash(request).save(
            request.state.user.id, StashedItem.clean(body.model_dump())
        )
        return {"item": _stashed(item), "added": added}

    @authed.post("/api/search/stash/{item_id}/detail")
    async def save_stash_detail(
        request: Request, item_id: str, body: StashDetailIn
    ) -> dict[str, Any]:
        """存浏览器插件拿到的商品详情，不请求闲鱼。返回更新后的这一件。"""
        return _stashed(stash(request).save_detail(request.state.user.id, item_id, body.item))

    @authed.delete("/api/search/stash/{item_id}")
    async def remove_stash(request: Request, item_id: str) -> dict[str, Any]:
        """移出暂存。本来就不在的也算成功（连点两下不报错）。"""
        return {"removed": stash(request).remove(request.state.user.id, item_id)}

    @authed.get("/api/accounts/{account_id}/drafts")
    async def list_drafts(request: Request, account_id: str) -> dict[str, Any]:
        return {"drafts": [_draft(d) for d in drafts(request).list(account(request, account_id))]}

    @authed.post("/api/accounts/{account_id}/drafts")
    async def create_draft(request: Request, account_id: str) -> dict[str, Any]:
        return _draft(drafts(request).create(account(request, account_id)))

    @authed.get("/api/accounts/{account_id}/drafts/{draft_id}")
    async def get_draft(request: Request, account_id: str, draft_id: int) -> dict[str, Any]:
        return _draft(drafts(request).get(account(request, account_id), draft_id))

    @authed.put("/api/accounts/{account_id}/drafts/{draft_id}")
    async def update_draft(
        request: Request, account_id: str, draft_id: int, body: DraftIn
    ) -> dict[str, Any]:
        draft = drafts(request).update(
            account(request, account_id),
            draft_id,
            description=body.description,
            price_cent=_yuan_or_none(body.price),
            orig_price_cent=_yuan_or_none(body.orig_price),
            post_mode=body.post_mode,
            post_price_cent=_yuan_or_none(body.post_price),
            take_self=body.take_self,
            image_order=body.images,
            address=body.address,
        )
        return _draft(draft)

    @authed.delete("/api/accounts/{account_id}/drafts/{draft_id}")
    async def delete_draft(request: Request, account_id: str, draft_id: int) -> dict[str, Any]:
        drafts(request).delete(account(request, account_id), draft_id)
        return {"ok": True}

    @authed.post("/api/accounts/{account_id}/drafts/{draft_id}/images")
    async def add_draft_image(request: Request, account_id: str, draft_id: int) -> dict[str, Any]:
        """请求体就是图片文件本身（不是表单）。"""
        account_id = account(request, account_id)
        size = int(request.headers.get("content-length") or 0)
        if size > image_store.MAX_BYTES:
            raise ValueError(f"图片太大，最大 {image_store.MAX_BYTES // 1024 // 1024} MB")
        return _draft(drafts(request).add_image(account_id, draft_id, await request.body()))

    @authed.get("/api/accounts/{account_id}/drafts/{draft_id}/images/{sha}")
    async def draft_image(request: Request, account_id: str, draft_id: int, sha: str) -> Response:
        content = drafts(request).image(account(request, account_id), draft_id, sha)
        # 文件名就是内容的哈希，内容不会变
        headers = {"cache-control": "private, max-age=31536000, immutable"}
        return Response(content, media_type="image/jpeg", headers=headers)

    @authed.delete("/api/accounts/{account_id}/drafts/{draft_id}/images/{sha}")
    async def remove_draft_image(
        request: Request, account_id: str, draft_id: int, sha: str
    ) -> dict[str, Any]:
        return _draft(drafts(request).remove_image(account(request, account_id), draft_id, sha))

    @authed.post("/api/accounts/{account_id}/drafts/{draft_id}/recommend")
    async def recommend_category(
        request: Request, account_id: str, draft_id: int
    ) -> dict[str, Any]:
        """按描述推荐分类和属性（请求平台）。"""
        account_id = account(request, account_id)
        async with locks[account_id]:
            draft = await drafts(request).recommend(account_id, draft_id)
        return _draft(draft)

    @authed.put("/api/accounts/{account_id}/drafts/{draft_id}/labels")
    async def choose_label(
        request: Request, account_id: str, draft_id: int, body: ChooseIn
    ) -> dict[str, Any]:
        """改一项分类或属性，平台按新的选择刷新别的属性。"""
        account_id = account(request, account_id)
        async with locks[account_id]:
            draft = await drafts(request).choose(account_id, draft_id, body.property_id, body.keys)
        return _draft(draft)

    @authed.post("/api/accounts/{account_id}/drafts/{draft_id}/publish")
    async def publish_draft(
        request: Request, account_id: str, draft_id: int, body: PublishIn | None = None
    ) -> dict[str, Any]:
        """发布新商品；编辑草稿是把改动提交到闲鱼。"""
        account_id = account(request, account_id)
        force = body.force if body else False
        async with locks[account_id]:
            draft = await drafts(request).publish(account_id, draft_id, force=force)
        return _draft(draft)

    @authed.get("/api/accounts/{account_id}/publish/addresses")
    async def publish_addresses(request: Request, account_id: str) -> dict[str, Any]:
        """可选的发货地（上次用的、常用地址），选好的 address 原样放进草稿。

        `search`：能不能按名字搜（配了高德 key 没有）。
        """
        account_id = account(request, account_id)
        async with locks[account_id]:
            addresses = await drafts(request).addresses(account_id)
        return {"addresses": addresses, "search": places.enabled}

    @authed.get("/api/publish/places")
    async def search_places(q: str, city: str = "") -> dict[str, Any]:
        """按名字搜发货地（高德），最多 10 个，格式和常用地址一样。不请求闲鱼。"""
        if len(q) > 50:
            raise ValueError("搜索词太长")
        return {"places": [address_option(poi) for poi in await places.search(q, city)]}

    @authed.get("/api/accounts/{account_id}/orders")
    async def list_orders(
        request: Request, account_id: str, side: str | None = None, status: str | None = None
    ) -> dict[str, Any]:
        """本地库里的订单。`side` 只看卖出的（sold）或买到的（bought），不给是全部。"""
        records = orders(request).list(account(request, account_id), side=side, status=status)
        return {
            "orders": [asdict(r) for r in records],
            "last_synced_at": max((r.synced_at for r in records), default=None),
        }

    @authed.post("/api/accounts/{account_id}/orders")
    async def add_order(request: Request, account_id: str, body: OrderAddIn) -> dict[str, Any]:
        account_id = account(request, account_id)
        async with locks[account_id]:
            record, added = await orders(request).add(account_id, body.order)
        return {"order": asdict(record), "added": added}

    @authed.delete("/api/accounts/{account_id}/orders/{order_id}")
    async def remove_order(request: Request, account_id: str, order_id: str) -> dict[str, Any]:
        if not orders(request).remove(account(request, account_id), order_id):
            raise ValueError(f"本地没有订单 {order_id}")
        return {"ok": True}

    @authed.post("/api/accounts/{account_id}/orders/sync")
    async def sync_orders(
        request: Request, account_id: str, body: OrderSyncIn | None = None
    ) -> dict[str, Any]:
        """刷新一边的订单：买到的整页同步"我买到的"，卖出的见 OrderService.sync。
        不带请求体是卖出的。"""
        account_id = account(request, account_id)
        side = body.side if body else SOLD
        if side not in JOBS or side == ITEMS:
            raise ValueError(f"不认识的订单类型：{side}")
        result = await autosync(request).run(account_id, side, MANUAL)
        return asdict(result)

    @authed.post("/api/accounts/{account_id}/orders/scan")
    async def scan_orders(request: Request, account_id: str) -> dict[str, Any]:
        """从本地存的消息里找订单（卖出的、买到的），逐笔拉详情记进本地库（新的交易消息
        xym serve 会自动记，这个补以前的）。本地已经结束的不再拉。

        `found` 消息里有几笔订单；每笔的结果见 OrderService.track。
        """
        account_id = account(request, account_id)
        found = [order_id for order_id, _role in hub(request).store.order_refs(account_id)]
        async with locks[account_id]:
            results = await orders(request).track_many(account_id, found)
        return {"found": len(found), **{key: results.get(key, []) for key in SCAN_RESULTS}}

    @authed.get("/api/accounts/{account_id}/orders/{order_id}/detail")
    async def order_detail(request: Request, account_id: str, order_id: str) -> dict[str, Any]:
        account_id = account(request, account_id)
        async with locks[account_id]:
            detail = await orders(request).fetch_detail(account_id, order_id)
        data = asdict(detail)
        data.pop("raw")
        return data

    @authed.get("/api/accounts/{account_id}/orders/{order_id}/price-fields")
    async def order_price_fields(
        request: Request, account_id: str, order_id: str
    ) -> dict[str, Any]:
        account_id = account(request, account_id)
        async with locks[account_id]:
            fields = await orders(request).price_fields(account_id, order_id)
        return {"fields": [asdict(f) for f in fields]}

    @authed.post("/api/accounts/{account_id}/orders/{order_id}/price")
    async def change_order_price(
        request: Request, account_id: str, order_id: str, body: OrderPriceIn
    ) -> dict[str, Any]:
        account_id = account(request, account_id)
        fees = {key: yuan_to_cent(value) for key, value in body.fees.items()}
        async with locks[account_id]:
            old, new = await orders(request).change_price(
                account_id, order_id, fees, force=body.force
            )
        return {"old_price_cent": old, "price_cent": new}

    @authed.get("/api/accounts/{account_id}/order-ops")
    async def order_ops(
        request: Request, account_id: str, order_id: str | None = None, limit: int = 50
    ) -> dict[str, Any]:
        ops = orders(request).history(account(request, account_id), order_id, limit=min(limit, 500))
        return {"ops": [asdict(op) for op in ops]}

    @authed.get("/api/accounts/{account_id}/autosync")
    async def autosync_status(request: Request, account_id: str) -> dict[str, Any]:
        """自动同步的设置和每种同步上次的结果。`active`：后台开没开自动同步；`paused`：账号现在
        为什么不自动同步（熔断、登录失效、碰到过风控），能同步时是 null。"""
        return autosync_info(request, account(request, account_id))

    @authed.put("/api/accounts/{account_id}/autosync/{job}")
    async def configure_autosync(
        request: Request, account_id: str, job: str, body: AutoSyncIn
    ) -> dict[str, Any]:
        """开关一种同步（items / sold / bought）、改定时的间隔。"""
        account_id = account(request, account_id)
        autosync(request).configure(account_id, job, enabled=body.enabled, interval=body.interval)
        return autosync_info(request, account_id)

    @authed.post("/api/accounts/{account_id}/autosync/visit")
    async def visit_autosync(request: Request, account_id: str, body: VisitIn) -> dict[str, Any]:
        """页面打开了：这几种同步数据旧了就在后台同步一次（见 AutoSync.visit），马上返回状态。"""
        account_id = account(request, account_id)
        unknown = set(body.jobs) - set(JOBS)
        if unknown:
            raise ValueError(f"不认识的同步：{', '.join(sorted(unknown))}")
        autosync(request).visit(account_id, body.jobs)
        return autosync_info(request, account_id)

    @authed.get("/api/accounts/{account_id}/conversations")
    async def list_conversations(request: Request, account_id: str) -> dict[str, Any]:
        """本地库里的会话，和即时消息的连接状态。"""
        account_id = account(request, account_id)
        records = hub(request).store.conversations(account_id)
        return {
            "conversations": [asdict(r) for r in records],
            "im": hub(request).status(account_id),
        }

    @authed.get("/api/accounts/{account_id}/conversations/{cid}/messages")
    async def list_messages(
        request: Request, account_id: str, cid: str, limit: int = 50
    ) -> dict[str, Any]:
        """本地库里一个会话最新的 `limit` 条消息（按时间正序）。local_more：本地还有更早的。"""
        account_id = account(request, account_id)
        store = hub(request).store
        conversation = store.conversation(account_id, cid)
        if conversation is None:
            raise ValueError(f"本地没有会话 {cid}")
        records, more = store.messages(account_id, cid, limit=min(max(limit, 1), 500))
        return {
            "conversation": asdict(conversation),
            "messages": [asdict(r) for r in records],
            "local_more": more,
        }

    @authed.post("/api/accounts/{account_id}/conversations/{cid}/fetch")
    async def fetch_messages(
        request: Request, account_id: str, cid: str, body: FetchIn
    ) -> dict[str, Any]:
        """通过即时消息连接向平台拉：最新的消息（补上本地缺的），或者往前翻一页。"""
        return await hub(request).fetch(account(request, account_id), cid, older=body.older)

    @authed.post("/api/accounts/{account_id}/conversations/{cid}/read")
    async def mark_read(request: Request, account_id: str, cid: str) -> dict[str, Any]:
        """看过了：清掉平台上这个会话的红点，本地未读数清零。不给对方发已读回执。"""
        return {"cleared": await hub(request).mark_read(account(request, account_id), cid)}

    @authed.post("/api/accounts/{account_id}/conversations/{cid}/messages")
    async def send_message(
        request: Request, account_id: str, cid: str, body: SendIn
    ) -> dict[str, Any]:
        """通过即时消息连接发一条文本消息，返回存进本地库的这条。

        504 send_uncertain：请求发出去了但没等到结果，消息可能已经发出去了，别急着重发。
        """
        record = await hub(request).send(account(request, account_id), cid, body.text)
        return {"message": asdict(record)}

    @authed.post("/api/accounts/{account_id}/im/reconnect")
    async def reconnect_im(request: Request, account_id: str) -> dict[str, Any]:
        account_id = account(request, account_id)
        hub(request).restart(account_id)
        return hub(request).status(account_id)

    @authed.get("/api/accounts/{account_id}/messages/stream")
    async def message_stream(request: Request, account_id: str) -> StreamingResponse:
        """Server-Sent Events：连上先发一次 status，之后有新消息、补拉完、连接状态变了都发一条。

        流开着的时候用户被停用、改了密码、账号转给了别人，下一次 ping 时断开；页面重连时就进不来了。
        """
        account_id = account(request, account_id)
        user: User = request.state.user

        def allowed() -> bool:
            now = users(request).get(user.id)
            return bool(
                now
                and not now.disabled
                and now.token_version == user.token_version
                and users(request).owns(user.id, account_id)
            )

        return StreamingResponse(
            sse_events(hub(request), account_id, allowed=allowed),
            media_type="text/event-stream",
            headers={"cache-control": "no-cache", "x-accel-buffering": "no"},
        )

    app.include_router(authed)
    if web_dist and (web_dist / "index.html").is_file():
        _mount_web(app, web_dist)
    return app


async def sse_events(
    hub: MessageHub,
    account_id: str,
    *,
    ping: float = SSE_PING,
    allowed: Callable[[], bool] | None = None,
) -> AsyncIterator[str]:
    """`allowed`：每次 ping 前确认一下还有没有权限看这个账号，没有了就结束。"""
    with hub.subscribe(account_id) as queue:
        yield _sse({"type": "status", **hub.status(account_id)})
        while True:
            try:
                event = await asyncio.wait_for(queue.get(), ping)
            except TimeoutError:
                if allowed and not allowed():
                    return
                yield ": ping\n\n"
                continue
            if event is CLOSING:
                return
            yield _sse(event)


def _sse(event: dict[str, Any]) -> str:
    return f"event: {event['type']}\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"


class SecurityHeaders:
    """每个响应都加上安全相关的响应头（见 SECURITY_HEADERS）。纯 ASGI，不碍着消息推送的流式响应。"""

    def __init__(self, app: Any):
        self.app = app

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Any) -> None:
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), *SECURITY_HEADERS]
            await send(message)

        await self.app(scope, receive, send_with_headers)


def _bearer(request: Request) -> str:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise NotSignedInError("还没有登录")
    return token.strip()


def _block(block: Block | None) -> dict[str, Any] | None:
    return asdict(block) if block else None


def _draft(draft: Draft) -> dict[str, Any]:
    """草稿给页面的样子：推荐卡片换成可选的属性列表。

    图片的 `url` 是闲鱼上的地址（传过或者编辑的商品原有的），还没传是 None。
    """
    category = draft.category or {}
    return {
        "id": draft.id,
        "description": draft.description,
        "price_cent": draft.price_cent,
        "orig_price_cent": draft.orig_price_cent,
        "post_mode": draft.post_mode,
        "post_price_cent": draft.post_price_cent,
        "post_keep": draft.can_keep_post(),
        "take_self": draft.take_self,
        "images": [
            {"sha": img.sha, "width": img.width, "height": img.height, "url": img.url}
            for img in draft.images
        ],
        "category": category.get("catName") or None,
        "properties": draft.properties(),
        "labels": draft.current_labels(),
        "unsupported": draft.unsupported(),
        "address": draft.address,
        "item_id": draft.item_id,
        "edit_item_id": draft.edit_item_id,
        "relist_item_id": draft.relist_item_id,
        "pdd_goods_id": draft.pdd_goods_id,
        "error": draft.error,
        "created_at": draft.created_at,
        "updated_at": draft.updated_at,
        "published_at": draft.published_at,
    }


def _pdd_goods(goods: PddGoods, *, detail: bool = False) -> dict[str, Any]:
    """拼多多货源给页面的样子。

    图片给拼多多 CDN 上的地址（页面直接显示），`saved` 是下载到本机没有。
    """
    body: dict[str, Any] = {
        "goods_id": goods.goods_id,
        "title": goods.title,
        "mall_name": goods.mall_name,
        "sales_tip": goods.sales_tip,
        "url": goods.url,
        "min_price_cent": goods.min_price_cent,
        "max_price_cent": goods.max_price_cent,
        "cover": goods.images[0].url if goods.images else "",
        "image_count": len(goods.images),
        "detail_count": len(goods.detail_images),
        "sku_count": len(goods.skus),
        "video_count": len(goods.videos),
        "missing_images": goods.missing(),
        "imported_at": goods.imported_at,
        "updated_at": goods.updated_at,
    }
    if not detail:
        return body

    def image(img: PddImage) -> dict[str, Any]:
        return {"url": img.url, "width": img.width, "height": img.height, "saved": bool(img.sha)}

    return {
        **body,
        "min_normal_price_cent": goods.min_normal_price_cent,
        "max_normal_price_cent": goods.max_normal_price_cent,
        "images": [image(img) for img in goods.images],
        "detail_images": [image(img) for img in goods.detail_images],
        "videos": [asdict(video) for video in goods.videos],
        "skus": [
            {
                "sku_id": sku.sku_id,
                "name": sku.name,
                "specs": sku.specs,
                "price_cent": sku.price_cent,
                "normal_price_cent": sku.normal_price_cent,
                "quantity": sku.quantity,
                "on_sale": sku.on_sale,
                "thumb": sku.thumb.url if sku.thumb else "",
            }
            for sku in goods.skus
        ],
        "properties": goods.properties,
    }


def _stashed(item: StashedItem) -> dict[str, Any]:
    return {**asdict(item), "url": item.url}


def _pdd_images(goods: PddGoods) -> dict[str, int]:
    total = len({img.url for img in goods.all_images()})
    return {"saved": total - goods.missing(), "total": total}


def _yuan_or_none(value: str | None) -> int | None:
    """价格：没填是 None（筛选里是不限），填了按元换算成分。"""
    return yuan_to_cent(value) if value and value.strip() else None


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
