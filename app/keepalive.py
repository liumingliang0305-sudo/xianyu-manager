"""定时确认登录态：会话一过期就免密续期，不用等到有人操作时才发现。

网页端每打开一个页面就续一次。管理后台没人用的时候也要续，所以每隔一段时间调一次只读接口
（和账号菜单里"重新检查登录态"一样）。会话过期时 MtopClient 照常试免密续期，遵守 sdkSilent 限频。

- 有效的：上次确认超过 interval 才再查；
- 失效的：只在续期有希望时再试（保持登录没到期、限频也过了），而且隔 EXPIRED_RETRY_AFTER 才试一次，
  免得平台一直拒绝时反复续；
- 风控的：不碰，要人工处理。
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Mapping

from app.accounts import Accounts, LoginState, LoginStatus
from xianyu_core.credential import Credential, cookie_time

log = logging.getLogger(__name__)

# 默认 30 分钟查一次。会话多久过期平台没公开，查得勤一点，过期后很快就能续上
LOGIN_CHECK_INTERVAL = 30 * 60
# 失效以后续期又有希望了，最快隔这么久再试一次
EXPIRED_RETRY_AFTER = 2 * 3600


def renewable(credential: Credential, now: float | None = None) -> bool:
    """会话过期的话，现在能不能免密续期：保持登录没到期，sdkSilent 限频也过了。"""
    now = time.time() if now is None else now
    until = credential.keep_login_until
    blocked = cookie_time(credential.cookies.get("sdkSilent"))
    return bool(until and until > now) and not (blocked and blocked > now)


def due(accounts: Accounts, account_id: str, *, max_age: float) -> bool:
    """这个账号这一轮要不要确认登录态。"""
    status = accounts.login_status(account_id, max_age=max_age)
    if status is None:
        return True
    if status.state is not LoginState.EXPIRED:
        return False
    waited = time.time() - status.checked_at
    return waited >= EXPIRED_RETRY_AFTER and renewable(accounts.load(account_id))


async def check_due(
    accounts: Accounts, locks: Mapping[str, asyncio.Lock], *, max_age: float
) -> dict[str, LoginStatus]:
    """查一轮，返回这一轮查了的账号和结果。和网页请求共用每个账号的锁，不会同时发请求。"""
    checked = {}
    for account_id in accounts.ids():
        async with locks[account_id]:
            if not due(accounts, account_id, max_age=max_age):
                continue
            status = await accounts.check(account_id)
        checked[account_id] = status
        # 默认日志级别是 WARNING，续期和失效都要让人在终端里看得到
        if status.renewed:
            log.warning("账号 %s 的会话过期了，已经免密续期", account_id)
        elif status.state is LoginState.EXPIRED:
            log.warning("账号 %s 的登录失效了：%s", account_id, status.message)
    return checked


async def run_forever(
    accounts: Accounts, locks: Mapping[str, asyncio.Lock], *, interval: float
) -> None:
    """启动时查一轮，之后每 interval 秒查一轮。"""
    while True:
        try:
            await check_due(accounts, locks, max_age=interval)
        except Exception:
            # 后台任务里的异常没人接，记下来接着跑
            log.exception("定时确认登录态出错")
        await asyncio.sleep(interval)
