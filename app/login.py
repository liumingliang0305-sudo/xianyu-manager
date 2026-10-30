"""管理后台里的登录：扫码登录，和导入浏览器的登录。

扫码登录：后台任务跑 QRLogin，前端轮询进度。同一时间只有一个扫码会话（现在只登录一个账号），
再取一张码会把上一个没扫完的取消掉。扫码成功后凭证写到 data/accounts/<unb>.json，
和 `xianyu login` 一样。

导入浏览器的登录：纯 HTTP 扫码登录平台不给开保持登录（2026-10-30 实测，同一个账号在浏览器里能开），
所以留一条路，把浏览器里开了保持登录的 cookie 整串贴进来。见 docs/protocol-notes.md。
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum

import httpx

from xianyu_core.auth import QRLogin, QRStatus
from xianyu_core.constants import KEEP_LOGIN_COOKIES
from xianyu_core.credential import Credential
from xianyu_core.errors import QRLoginError, VerificationRequiredError
from xianyu_core.renew import KeepLoginSettings

log = logging.getLogger(__name__)


class LoginNotFoundError(LookupError):
    pass


class LoginPhase(StrEnum):
    WAITING = "waiting"  # 等扫码
    SCANNED = "scanned"  # 已扫码，等手机上确认
    CONFIRMED = "confirmed"  # 手机上确认了，正在拿凭证
    DONE = "done"  # 登录成功，凭证已保存
    EXPIRED = "expired"  # 二维码过期或者等太久了，换一张
    CANCELED = "canceled"  # 在手机上取消了
    VERIFY = "verify"  # 平台要求额外身份验证，要人工处理
    FAILED = "failed"  # 其他错误


FINISHED = frozenset(
    {LoginPhase.DONE, LoginPhase.EXPIRED, LoginPhase.CANCELED, LoginPhase.VERIFY, LoginPhase.FAILED}
)

# 导入浏览器的登录至少要有的 cookie：unb 是账号 ID，cookie2 是会话
IMPORT_REQUIRED_COOKIES = ("unb", "cookie2")
# 这几个 cookie 都一样就是同一份登录，浏览器插件重复推过来时不用再问平台
SAME_LOGIN_COOKIES = ("unb", "cookie2", *KEEP_LOGIN_COOKIES)

_PHASE_OF_QR = {
    QRStatus.NEW: LoginPhase.WAITING,
    QRStatus.SCANNED: LoginPhase.SCANNED,
    QRStatus.CONFIRMED: LoginPhase.CONFIRMED,
}


@dataclass(frozen=True)
class LoginProgress:
    id: str
    phase: LoginPhase
    message: str = ""  # 出错的原因；登录成功时是保持登录的情况
    account_id: str = ""  # 登录成功后是账号 unb


class WebLogin:
    """扫码会话。`save` 负责把凭证写进账号目录。"""

    def __init__(
        self,
        save: Callable[[Credential], Awaitable[None]],
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        poll_interval: float = 2.0,
        timeout: float = 300.0,
    ):
        self._save = save
        self._transport = transport  # 测试注入 httpx.MockTransport
        self._poll_interval = poll_interval
        self._timeout = timeout
        self._progress: LoginProgress | None = None
        self._task: asyncio.Task[None] | None = None
        # 取码要几个来回，期间再来一个取码请求要排队，免得两个会话同时在跑
        self._lock = asyncio.Lock()

    async def start(self) -> tuple[LoginProgress, bytes]:
        """取一张新二维码，后台开始等扫码。返回进度和二维码 SVG。"""
        async with self._lock:
            await self._stop()
            login = QRLogin(
                poll_interval=self._poll_interval, timeout=self._timeout, transport=self._transport
            )
            try:
                qr = await login.start()
            except httpx.HTTPError as exc:
                await login.aclose()
                raise QRLoginError(f"获取二维码失败：{exc!r}") from exc
            except BaseException:
                await login.aclose()
                raise
            self._progress = LoginProgress(id=secrets.token_urlsafe(8), phase=LoginPhase.WAITING)
            self._task = asyncio.create_task(self._run(self._progress.id, login))
            return self._progress, qr.to_svg()

    def progress(self, session_id: str) -> LoginProgress:
        if self._progress is None or self._progress.id != session_id:
            raise LoginNotFoundError("这张二维码已经失效了，换一张再扫")
        return self._progress

    async def cancel(self, session_id: str) -> bool:
        """取消当前会话，之后再查进度就是找不到。还没扫完、真的取消了时返回 True。"""
        async with self._lock:
            if self._progress is None or self._progress.id != session_id:
                return False
            self._progress = None
            return await self._stop()

    async def aclose(self) -> None:
        async with self._lock:
            await self._stop()

    async def _stop(self) -> bool:
        task, self._task = self._task, None
        if task is None or task.done():
            return False
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        return True

    async def _run(self, session_id: str, login: QRLogin) -> None:
        def update(**changes: object) -> None:
            if self._progress is not None and self._progress.id == session_id:
                self._progress = dataclasses.replace(self._progress, **changes)

        def on_status(status: QRStatus) -> None:
            if status in _PHASE_OF_QR:
                update(phase=_PHASE_OF_QR[status])

        try:
            async with login:
                credential = await login.wait(on_status=on_status)
                keep_login = login.keep_login
            await self._save(credential)
            update(
                phase=LoginPhase.DONE,
                account_id=credential.unb,
                message=_keep_login_text(keep_login, credential),
            )
        except VerificationRequiredError as exc:
            log.warning("扫码登录要求额外身份验证：%s", exc.verification_url)
            update(
                phase=LoginPhase.VERIFY,
                message=(
                    "平台要求额外身份验证。先在电脑浏览器打开 goofish.com 正常登录一次、"
                    "完成验证，再重新扫码"
                ),
            )
        except QRLoginError as exc:
            phase = {
                QRStatus.EXPIRED: LoginPhase.EXPIRED,
                QRStatus.CANCELED: LoginPhase.CANCELED,
            }.get(QRStatus.parse(exc.status), LoginPhase.FAILED)
            update(phase=phase, message=str(exc))
        except Exception as exc:
            # 后台任务里的异常没人接，这里不兜住的话进度会一直停在"正在拿凭证"
            log.exception("扫码登录失败")
            update(phase=LoginPhase.FAILED, message=str(exc) or type(exc).__name__)


def parse_cookie_header(text: str) -> Credential:
    """浏览器开发者工具里复制的 Cookie 请求头，可以带 `cookie:` 前缀。

    缺登录 cookie 时抛 ValueError，消息里只有 cookie 名，不带值。
    `_m_h5_tk` 可以没有，第一次请求时平台会发。
    """
    text = text.strip()
    if text[:7].lower() == "cookie:":
        text = text[7:]
    credential = Credential.from_cookie_header(text)
    missing = [name for name in IMPORT_REQUIRED_COOKIES if not credential.cookies.get(name)]
    if missing:
        raise ValueError(f"cookie 里没有 {'、'.join(missing)}，要复制整个 Cookie 请求头")
    if not credential.unb.isdigit():
        raise ValueError("cookie 里的 unb 不是账号 ID，复制的可能不是闲鱼的 cookie")
    return credential


def same_login(a: Credential, b: Credential) -> bool:
    return all(a.cookies.get(name) == b.cookies.get(name) for name in SAME_LOGIN_COOKIES)


def imported_text(credential: Credential) -> str:
    until = credential.keep_login_until
    if not until:
        return "这份登录没有开保持登录，会话过期后要重新登录"
    return f"保持登录到 {_clock(until)}"


def _keep_login_text(settings: KeepLoginSettings | None, credential: Credential) -> str:
    if settings is None:
        return "开启保持登录时出错，会话过期后要重新扫码"
    until = credential.keep_login_until
    if settings.enabled and until:
        return f"保持登录到 {_clock(until)}"
    if not settings.can_open:
        return "平台不给这次登录开保持登录，会话过期后要重新扫码"
    return "平台没有确认开启保持登录，会话过期后要重新扫码"


def _clock(seconds: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(seconds))
