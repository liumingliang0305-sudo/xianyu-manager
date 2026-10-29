"""账号：凭证文件放在 data/accounts/<unb>.json（`xianyu login` 生成），账号 ID 就是 unb。

登录态（有效 / 失效 / 风控）在每次和平台打交道时顺手记下，只放在内存里。
会话过期时 MtopClient 会先试免密续期，续期拿到的 cookie 随凭证一起写回文件。
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import httpx

from xianyu_core import AuthRequiredError, Credential, MtopClient, RiskControlError, XianyuError

DEFAULT_DIR = Path("data/accounts")
# 只读接口，只返回 userId（等于 unb），用来确认登录态
LOGIN_USER_API = "mtop.taobao.idlemessage.pc.loginuser.get"
# 有效的登录态多久以后要重新确认，秒
CHECK_INTERVAL = 600


class AccountNotFoundError(LookupError):
    pass


class LoginState(StrEnum):
    VALID = "valid"
    EXPIRED = "expired"  # 免密续期也没成功，要重新扫码
    RISK = "risk"  # 触发风控，要人工处理
    ERROR = "error"  # 网络或平台出错，没判断出来


@dataclass(frozen=True)
class LoginStatus:
    state: LoginState
    checked_at: float
    renewed: bool = False  # 这次做了免密续期
    message: str = ""
    keep_login_until: float | None = None  # 保持登录到期时间，没开是 None


class Accounts:
    def __init__(
        self,
        root: str | Path = DEFAULT_DIR,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self.root = Path(root)
        self._transport = transport  # 测试注入 httpx.MockTransport
        self._status: dict[str, LoginStatus] = {}

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
        """拿这个账号的 MtopClient，用完把续期过的 cookie 写回凭证文件，顺手记下登录态。"""
        path = self.path(account_id)
        async with MtopClient(Credential.load(path), transport=self._transport) as client:
            state: LoginState | None = LoginState.VALID
            message = ""
            try:
                yield client
            except AuthRequiredError as exc:
                state, message = LoginState.EXPIRED, str(exc)
                raise
            except RiskControlError as exc:
                state, message = LoginState.RISK, str(exc)
                raise
            except BaseException:
                state = None  # 别的错误说明不了登录态
                raise
            finally:
                credential = client.credential
                credential.save(path)
                if state is not None:
                    self._status[account_id] = LoginStatus(
                        state=state,
                        checked_at=time.time(),
                        renewed=bool(client.renewal and client.renewal.ok),
                        message=message,
                        keep_login_until=credential.keep_login_until,
                    )

    async def check(self, account_id: str) -> LoginStatus:
        """调一次只读接口确认登录态。会话过期时会先试免密续期。"""
        try:
            async with self.mtop(account_id) as mtop:
                user = await mtop.call(LOGIN_USER_API)
                if str(user.get("userId", "")) != account_id:
                    raise AuthRequiredError("登录态对应的账号和凭证文件不一致")
        except (AuthRequiredError, RiskControlError):
            pass  # mtop() 已经记下了
        except (XianyuError, httpx.HTTPError) as exc:
            self._status[account_id] = LoginStatus(
                state=LoginState.ERROR,
                checked_at=time.time(),
                message=str(exc) or type(exc).__name__,
                keep_login_until=self.load(account_id).keep_login_until,
            )
        return self._status[account_id]

    def login_status(
        self, account_id: str, *, max_age: float = CHECK_INTERVAL
    ) -> LoginStatus | None:
        """记下的登录态，需要重新确认时返回 None。

        凭证文件更新过（比如重新扫码了）就要重新确认；有效的过了 max_age 也要。
        失效和风控不会自己好，不自动重查，免得反复续期或在风控期间继续发请求。
        """
        status = self._status.get(account_id)
        if status is None:
            return None
        if (self.load(account_id).saved_at or 0) > status.checked_at:
            return None
        if status.state in (LoginState.VALID, LoginState.ERROR):
            if time.time() - status.checked_at > max_age:
                return None
        return status
