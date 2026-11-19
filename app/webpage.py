"""读公开网页的基础：链接规范化、防 SSRF 的受限请求、不执行脚本的轻量 HTML 解析。

第三方货源的各个解析方案（app/source_parsers.py）都经这里请求网页、数据和图片：
只请求公网 HTTP(S)，每一跳都重新解析并校验地址，连接钉在校验过的 IP 上；
网页、数据、图片都限制下载大小。
"""

from __future__ import annotations

import asyncio
import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from html.parser import HTMLParser
from socket import SOCK_STREAM, getaddrinfo
from typing import Any, Literal
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

MAX_HTML_BYTES = 3 * 1024 * 1024
MAX_JSON_BYTES = 8 * 1024 * 1024
USER_AGENT = "Mozilla/5.0 (compatible; XianyuManager/1.0)"
ARTICLE_HOSTS = ("jianpian.cn", "jianpian.info", "meipian.cn")
# 本机开 Clash 时这些已知平台的 HTTPS 域名会解析成 Fake-IP，只对它们放行这个保留段
PROVIDER_HOSTS = (
    *ARTICLE_HOSTS,
    "meipian.me",
    "ivwen.com",
    "xiumi.us",
    "xiumius.cn",
    "ziwoyou.net",
    "zowoyoo.com",
    "leleku.cn",
    "135editor.com",
)
FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
TRACKING_PARAMS = {"sc", "sd", "a_uid", "s_uid", "from", "isappinstalled", "share_from"}

Kind = Literal["html", "json", "image"]


def host_in(host: str | None, roots: tuple[str, ...]) -> bool:
    host = (host or "").lower()
    return any(host == root or host.endswith(f".{root}") for root in roots)


def canonical_url(value: str, *, clean_share: bool = True) -> str:
    """清理分享参数和片段，同一篇简篇的不同分享链接视为同一条货源。"""
    try:
        parts = urlsplit(value.strip())
        host = (parts.hostname or "").lower().rstrip(".")
        if (
            parts.scheme not in ("https", "http")
            or not host
            or parts.username is not None
            or parts.password is not None
            or parts.port not in (None, 80, 443)
            or len(value) > 4096
            or any(ord(c) < 32 for c in value)
        ):
            raise ValueError
        netloc = f"[{host}]" if ":" in host else host
        if parts.port is not None:
            netloc += f":{parts.port}"
    except ValueError as exc:
        raise ValueError("请输入完整的公开网页链接（http:// 或 https://）") from exc
    query = parts.query
    if clean_share and host_in(host, ARTICLE_HOSTS):
        # 简篇的裸域名会返回空页面，文章入口固定使用 www；CDN 子域名保持原样。
        if host in ("jianpian.cn", "www.jianpian.cn"):
            netloc = "www.jianpian.cn"
        query = urlencode([(k, v) for k, v in parse_qsl(query) if k not in TRACKING_PARAMS])
    return urlunsplit((parts.scheme, netloc, parts.path or "/", query, ""))


def with_fragment(url: str, fragment: str) -> str:
    return f"{url}#{fragment}" if fragment else url


async def public_url(url: str) -> str:
    """域名解析出的每个地址都必须是公网，拒绝本机、内网、云元数据等地址。"""
    parts = urlsplit(canonical_url(url))
    try:
        addresses = await asyncio.to_thread(
            getaddrinfo, parts.hostname, parts.port or 443, type=SOCK_STREAM
        )
    except OSError as exc:
        raise ValueError("链接的域名无法解析，请检查地址") from exc
    # Clash 等代理把域名解析为 198.18/15 的 Fake-IP。只对已知平台的 HTTPS 域名兼容这个
    # 保留段；仍校验 TLS 证书，任意网页、IP 字面量、内网地址都不走这个例外。
    provider_proxy = (
        parts.scheme == "https"
        and parts.port in (None, 443)
        and host_in(parts.hostname, PROVIDER_HOSTS)
    )
    ips = [ipaddress.ip_address(address[4][0]) for address in addresses]
    if not ips or any(
        not ip.is_global and not (provider_proxy and ip.version == 4 and ip in FAKE_IP_NETWORK)
        for ip in ips
    ):
        raise ValueError("只支持公开网页，不能访问本机或内网地址")
    return str(ips[0])


class PublicTransport(httpx.AsyncBaseTransport):
    """连接校验过的 IP，保留原域名的 Host 和 TLS 校验，避免 DNS 重绑定。

    每个原站独立连接池：两个域名恰好解析到同一个 IP 时，也不能复用对方的 TLS 连接。
    """

    def __init__(self):
        self._origins: dict[tuple[str, str, int | None], httpx.AsyncHTTPTransport] = {}

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        origin = (request.url.scheme, request.url.host, request.url.port)
        if origin not in self._origins:
            self._origins[origin] = httpx.AsyncHTTPTransport(trust_env=False)
        pinned = httpx.Request(
            request.method,
            request.url.copy_with(host=request.extensions["source_ip"]),
            headers=request.headers,
            stream=request.stream,
            extensions={**request.extensions, "sni_hostname": request.url.raw_host.decode("ascii")},
        )
        return await self._origins[origin].handle_async_request(pinned)

    async def aclose(self) -> None:
        await asyncio.gather(*(transport.aclose() for transport in self._origins.values()))


def public_client(transport: httpx.AsyncBaseTransport | None = None) -> httpx.AsyncClient:
    """不自动跳转（每一跳都要重新过公网检查），不读系统代理。"""
    return httpx.AsyncClient(
        transport=transport or PublicTransport(),
        timeout=30,
        follow_redirects=False,
        trust_env=False,
        headers={"User-Agent": USER_AGENT},
    )


@dataclass
class Fetched:
    data: bytes | None  # None：stop 认出了跳转目标，那一跳没有请求
    url: str  # 最后一跳的规范地址（不含 #）
    fragment: str = ""  # 跳转链里最后出现的 #，单页应用的路由在这里
    encoding: str = "utf-8"
    headers: httpx.Headers = field(default_factory=httpx.Headers)

    @property
    def full_url(self) -> str:
        return with_fragment(self.url, self.fragment)

    def text(self) -> str:
        return (self.data or b"").decode(self.encoding, errors="replace")


ACCEPTS: dict[Kind, Callable[[str], bool]] = {
    "html": lambda kind: kind in ("text/html", "application/xhtml+xml"),
    "json": lambda kind: kind == "application/json" or kind.endswith("+json"),
    "image": lambda kind: kind.startswith("image/"),
}
WRONG_KIND = {
    "html": "链接没有返回网页正文",
    "json": "网站没有返回数据",
    "image": "链接没有返回图片",
}
TOO_LARGE = {"html": "网页过大，暂时无法提取", "json": "网站返回的数据过大", "image": "图片过大"}
MAX_BYTES_BY_KIND = {"html": MAX_HTML_BYTES, "json": MAX_JSON_BYTES}


async def fetch(
    client: httpx.AsyncClient,
    url: str,
    *,
    kind: Kind = "html",
    max_bytes: int | None = None,
    referer: str = "",
    method: str = "GET",
    json: Any = None,
    headers: dict[str, str] | None = None,
    stop: Callable[[str], bool] | None = None,
) -> Fetched:
    """限制跳转、响应类型、解压后的大小；跳转不能绕过公网检查。

    stop(带 # 的地址) 为真时不再请求这一跳，直接返回地址：短链接跳到已认识的页面时用。
    """
    limit = max_bytes or MAX_BYTES_BY_KIND[kind]
    fragment = urlsplit(url.strip()).fragment
    for _ in range(6):
        url = canonical_url(url, clean_share=kind == "html")
        if stop and stop(with_fragment(url, fragment)):
            return Fetched(None, url, fragment)
        ip = await public_url(url)
        async with client.stream(
            method,
            url,
            headers={**({"Referer": referer} if referer else {}), **(headers or {})},
            json=json,
            extensions={"source_ip": ip},
        ) as r:
            if r.is_redirect:
                location = r.headers.get("location")
                if not location or method != "GET":
                    raise ValueError("网页跳转地址无效")
                url = urljoin(url, location)
                fragment = urlsplit(url).fragment or fragment
                continue
            r.raise_for_status()
            content_type = r.headers.get("content-type", "").split(";", 1)[0].lower().strip()
            if not ACCEPTS[kind](content_type):
                raise ValueError(WRONG_KIND[kind])
            data = bytearray()
            async for chunk in r.aiter_bytes():
                data.extend(chunk)
                if len(data) > limit:
                    raise ValueError(TOO_LARGE[kind])
            return Fetched(bytes(data), url, fragment, r.encoding or "utf-8", r.headers)
    raise ValueError("网页跳转次数过多")


# 普通 HTML 的轻量树：既处理常见正文容器，也用于把富文本片段转成纯文本。
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
SKIP = {
    "script",
    "style",
    "nav",
    "footer",
    "header",
    "aside",
    "form",
    "button",
    "svg",
    "canvas",
    "template",
    "noscript",
    "iframe",
}
BLOCKS = {
    "p",
    "div",
    "section",
    "article",
    "main",
    "li",
    "h1",
    "h2",
    "h3",
    "h4",
    "tr",
    "blockquote",
}


@dataclass
class Element:
    tag: str
    attrs: dict[str, str]
    children: list[Element | str] = field(default_factory=list)

    def walk(self):
        yield self
        for child in self.children:
            if isinstance(child, Element):
                yield from child.walk()

    def script_text(self) -> str:
        return "".join(c for c in self.children if isinstance(c, str))


class Document(HTMLParser):
    def __init__(self, html: str):
        super().__init__(convert_charrefs=True)
        self.root = Element("root", {})
        self.stack = [self.root]
        self.feed(html)
        self.close()

    def handle_starttag(self, tag, attrs):
        node = Element(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].children.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                break

    def handle_data(self, data):
        self.stack[-1].children.append(data)


def text_content(node: Element) -> str:
    if node.tag in SKIP or "hidden" in node.attrs or node.attrs.get("aria-hidden") == "true":
        return ""
    if node.tag == "br":
        return "\n"
    text = "".join(text_content(c) if isinstance(c, Element) else c for c in node.children)
    return f"\n{text}\n" if node.tag in BLOCKS else text


def plain_text(html: str) -> str:
    return clean_text(text_content(Document(html).root))


def clean_text(text: str) -> str:
    return "\n".join(
        line for raw in text.splitlines() if (line := re.sub(r"\s+", " ", raw).strip())
    )


def image_url(raw: Any, base: str) -> str | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        # 不清理图片的查询参数：CDN 的签名、转换参数可能是获取图片所必需的。
        return canonical_url(urljoin(base, raw.strip()), clean_share=False)
    except ValueError:
        return None
