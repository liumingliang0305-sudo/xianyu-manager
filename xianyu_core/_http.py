from __future__ import annotations

import httpx

from xianyu_core.constants import BROWSER_HEADERS
from xianyu_core.credential import cookies_to_jar


def new_client(
    cookies: dict[str, str] | None = None,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> httpx.AsyncClient:
    """统一的 HTTP 客户端：带浏览器请求头、跟随跳转，cookie 全部放在客户端的 jar 里。

    `transport` 留给测试注入 httpx.MockTransport。
    """
    client = httpx.AsyncClient(
        headers=BROWSER_HEADERS,
        follow_redirects=True,
        timeout=20.0,
        transport=transport,
    )
    if cookies:
        cookies_to_jar(cookies, client.cookies)
    return client
