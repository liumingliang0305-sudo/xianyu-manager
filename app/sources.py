"""第三方货源：从公开网页提取正文、内容图片，按后台用户保存。

简篇/美篇优先读取服务端的 __INITIAL_STATE__ JSON，不执行网页脚本；普通网页从
article/main/正文容器提取。图片复用草稿的 ImageStore，查看时用本地副本，不依赖原站。
只请求公网 HTTP(S)，每次跳转重新校验地址；页面、图片均限制下载大小。
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import json
import logging
import re
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from html.parser import HTMLParser
from socket import SOCK_STREAM, getaddrinfo
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlsplit, urlunsplit

import httpx

from app.db import Database, Row
from app.images import MAX_BYTES, ImageStore, drop_unused

log = logging.getLogger(__name__)

MAX_HTML_BYTES = 3 * 1024 * 1024
MAX_CONTENT = 50000
MAX_IMAGES = 100
DOWNLOAD_CONCURRENCY = 6
USER_AGENT = "Mozilla/5.0 (compatible; XianyuManager/1.0)"
ARTICLE_HOSTS = ("jianpian.cn", "jianpian.info", "meipian.cn")
PROVIDER_HOSTS = (*ARTICLE_HOSTS, "meipian.me", "ivwen.com")
FAKE_IP_NETWORK = ipaddress.ip_network("198.18.0.0/15")
TRACKING_PARAMS = {"sc", "sd", "a_uid", "s_uid", "from", "isappinstalled", "share_from"}


class SourceNotFoundError(LookupError):
    pass


@dataclass
class SourceImage:
    url: str
    sha: str | None = None
    width: int | None = None
    height: int | None = None


@dataclass
class Source:
    source_id: str
    url: str
    title: str
    content: str
    platform: str = "网页"
    author: str = ""
    images: list[SourceImage] = field(default_factory=list)
    imported_at: float = 0.0
    updated_at: float = 0.0

    def shas(self) -> set[str]:
        return {img.sha for img in self.images if img.sha}

    def missing(self) -> int:
        return sum(not img.sha for img in self.images)

    def images_json(self) -> str:
        return json.dumps([asdict(img) for img in self.images], ensure_ascii=False)

    @classmethod
    def from_row(cls, row: Row) -> Source:
        return cls(**{**row, "images": [SourceImage(**img) for img in json.loads(row["images"])]})


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
    if clean_share and any(host == root or host.endswith(f".{root}") for root in ARTICLE_HOSTS):
        # 简篇的裸域名会返回空页面，文章入口固定使用 www；CDN 子域名保持原样。
        if host in ("jianpian.cn", "www.jianpian.cn"):
            netloc = "www.jianpian.cn"
        query = urlencode([(k, v) for k, v in parse_qsl(query) if k not in TRACKING_PARAMS])
    return urlunsplit((parts.scheme, netloc, parts.path or "/", query, ""))


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
        and any(
            parts.hostname == host or parts.hostname.endswith(f".{host}") for host in PROVIDER_HOSTS
        )
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


async def fetch(
    client: httpx.AsyncClient, url: str, *, max_bytes: int, image: bool = False, referer: str = ""
) -> tuple[bytes, str, str]:
    """限制跳转、响应类型、解压后的大小；跳转不能绕过公网检查。"""
    for _ in range(6):
        url = canonical_url(url, clean_share=not image)
        ip = await public_url(url)
        async with client.stream(
            "GET",
            url,
            headers={"Referer": referer} if referer else {},
            extensions={"source_ip": ip},
        ) as r:
            if r.is_redirect:
                location = r.headers.get("location")
                if not location:
                    raise ValueError("网页跳转地址为空")
                url = urljoin(url, location)
                continue
            r.raise_for_status()
            kind = r.headers.get("content-type", "").split(";", 1)[0].lower().strip()
            allowed = (
                kind.startswith("image/")
                if image
                else kind in ("text/html", "application/xhtml+xml")
            )
            if not allowed:
                raise ValueError("链接没有返回图片" if image else "链接没有返回网页正文")
            data = bytearray()
            async for chunk in r.aiter_bytes():
                data.extend(chunk)
                if len(data) > max_bytes:
                    raise ValueError("图片过大" if image else "网页过大，暂时无法提取")
            return bytes(data), url, r.encoding or "utf-8"
    raise ValueError("网页跳转次数过多")


# 普通 HTML 的轻量树：既处理常见正文容器，也用于把简篇段落里的富文本转成纯文本。
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


def extract(html: str, url: str) -> Source:
    """提取而不执行脚本，保留正文顺序；图片去重，排除页面的头像、图标。"""
    url = canonical_url(url)
    doc = Document(html)
    nodes = list(doc.root.walk())
    source = Source(hashlib.sha256(url.encode()).hexdigest()[:32], url, "", "")
    seen: set[str] = set()

    def add_image(raw, width=None, height=None):
        image = image_url(raw, url)
        if image and image not in seen and len(source.images) < MAX_IMAGES:
            seen.add(image)
            source.images.append(SourceImage(image, width=_size(width), height=_size(height)))

    # 简篇图片是懒加载，SSR 的 img 没有 src，但完整地址在这个 JSON 里。
    article = author = None
    for node in nodes:
        if node.tag != "script":
            continue
        script = "".join(c for c in node.children if isinstance(c, str))
        match = re.search(r"window\.__INITIAL_STATE__\s*=\s*", script)
        if not match:
            continue
        try:
            state, _ = json.JSONDecoder().raw_decode(script[match.end() :])
            article = state.get("detail", {}).get("article")
            author = state.get("users", {}).get("author", {})
        except (ValueError, AttributeError):
            continue
        if isinstance(article, dict) and isinstance(article.get("content"), list):
            break
        article = None
    if article:
        source.platform = "简篇" if "jianpian" in urlsplit(url).hostname else "美篇"
        source.title = plain_text(str(article.get("title") or article.get("rich_text_title") or ""))
        source.author = str(author.get("nickname") or "") if isinstance(author, dict) else ""
        paragraphs = []
        for block in article["content"]:
            if not isinstance(block, dict):
                continue
            if block.get("text"):
                paragraphs.append(plain_text(str(block["text"])))
            add_image(block.get("img_url"), block.get("img_width"), block.get("img_height"))
        source.content = "\n\n".join(p for p in paragraphs if p)
    else:
        meta = {
            n.attrs.get("property") or n.attrs.get("name"): n.attrs.get("content", "")
            for n in nodes
            if n.tag == "meta"
        }
        title_node = next((n for n in nodes if n.tag == "title"), None)
        source.title = meta.get("og:title") or (
            clean_text(text_content(title_node)) if title_node else ""
        )
        source.author = meta.get("author", "")
        body = next((n for n in nodes if n.tag == "article"), None)
        body = body or next(
            (
                n
                for n in nodes
                if re.search(
                    r"(?:article|post|entry)[-_]content|rich_media_content|mp-article-content",
                    n.attrs.get("id", "") + " " + n.attrs.get("class", ""),
                )
            ),
            None,
        )
        body = body or next((n for n in nodes if n.tag == "main"), None)
        body = body or next((n for n in nodes if n.tag == "body"), doc.root)
        source.content = clean_text(text_content(body))
        for node in body.walk():
            if node.tag != "img":
                continue
            add_image(
                node.attrs.get("data-src")
                or node.attrs.get("data-original")
                or node.attrs.get("src"),
                node.attrs.get("width"),
                node.attrs.get("height"),
            )
        if not source.images:
            add_image(meta.get("og:image"))
    source.title = clean_text(source.title)[:500]
    if not source.title:
        source.title = source.content.split("\n", 1)[0][:100]
    if not source.title or (not source.content and not source.images):
        raise ValueError("没有提取到正文或图片；这个链接可能需要登录，或不支持直接提取")
    if len(source.content) > MAX_CONTENT:
        raise ValueError(f"正文超过 {MAX_CONTENT} 字，暂时无法保存")
    return source


def _size(value: Any) -> int | None:
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


class SourceService:
    def __init__(self, db: Database, images: ImageStore, *, transport=None):
        self._db = db
        self._images = images
        self._transport = transport
        self._locks: defaultdict[tuple[int, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    def list(self, user_id: int) -> list[Source]:
        rows = self._db.execute(
            "SELECT source_id, url, title, content, platform, author, images,"
            " imported_at, updated_at"
            " FROM third_party_sources WHERE user_id = %s ORDER BY updated_at DESC, source_id",
            (user_id,),
        )
        return [Source.from_row(row) for row in rows]

    def get(self, user_id: int, source_id: str) -> Source:
        source = self.find(user_id, source_id)
        if source is None:
            raise SourceNotFoundError("找不到这个第三方货源")
        return source

    def find(self, user_id: int, source_id: str) -> Source | None:
        row = self._db.execute(
            "SELECT source_id, url, title, content, platform, author, images,"
            " imported_at, updated_at"
            " FROM third_party_sources WHERE user_id = %s AND source_id = %s",
            (user_id, source_id),
        ).fetchone()
        return Source.from_row(row) if row else None

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport or PublicTransport(),
            timeout=30,
            follow_redirects=False,
            trust_env=False,
            headers={"User-Agent": USER_AGENT},
        )

    async def ingest(self, user_id: int, url: str) -> tuple[Source, bool]:
        try:
            async with self.client() as client:
                html, final_url, encoding = await fetch(client, url, max_bytes=MAX_HTML_BYTES)
            source = await asyncio.to_thread(
                extract, html.decode(encoding, errors="replace"), final_url
            )
        except httpx.HTTPError as exc:
            raise ValueError("读取货源网页失败，请检查链接或稍后重试") from exc
        async with self._locks[(user_id, source.source_id)]:
            old = self.find(user_id, source.source_id)
            if old:
                saved = {img.url: img for img in old.images if img.sha}
                source.images = [saved.get(img.url, img) for img in source.images]
            now = time.time()
            source.imported_at = old.imported_at if old else now
            source.updated_at = now
            self._db.execute(
                "INSERT INTO third_party_sources (user_id, source_id, url, title, content,"
                " platform,"
                " author, images, imported_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)"
                " ON CONFLICT (user_id, source_id) DO UPDATE SET title = excluded.title,"
                " content = excluded.content, platform = excluded.platform,"
                " author = excluded.author,"
                " images = excluded.images, updated_at = excluded.updated_at",
                (
                    user_id,
                    source.source_id,
                    source.url,
                    source.title,
                    source.content,
                    source.platform,
                    source.author,
                    source.images_json(),
                    source.imported_at,
                    now,
                ),
            )
            if old:
                drop_unused(self._db, self._images, old.shas() - source.shas())
            source = await self._download(user_id, source)
        return source, old is None

    async def fetch_images(self, user_id: int, source_id: str) -> Source:
        async with self._locks[(user_id, source_id)]:
            return await self._download(user_id, self.get(user_id, source_id))

    async def delete(self, user_id: int, source_id: str) -> None:
        async with self._locks[(user_id, source_id)]:
            source = self.get(user_id, source_id)
            self._db.execute(
                "DELETE FROM third_party_sources WHERE user_id = %s AND source_id = %s",
                (user_id, source_id),
            )
            drop_unused(self._db, self._images, source.shas())

    def image_path(self, user_id: int, source_id: str, sha: str):
        source = self.get(user_id, source_id)
        if sha not in source.shas():
            raise SourceNotFoundError("这个货源里没有这张图片")
        path = self._images.path(sha)
        if not path.exists():
            raise SourceNotFoundError("图片文件不存在，请重新导入货源")
        return path

    async def _download(self, user_id: int, source: Source) -> Source:
        gate = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
        async with self.client() as client:

            async def download(img: SourceImage):
                if img.sha:
                    return
                async with gate:
                    try:
                        data, _, _ = await fetch(
                            client, img.url, max_bytes=MAX_BYTES, image=True, referer=source.url
                        )
                        stored = await asyncio.to_thread(self._images.save, data)
                    except (httpx.HTTPError, ValueError, OSError) as exc:
                        log.warning("下载第三方货源图片失败 %s：%s", img.url, exc)
                        return
                    img.sha, img.width, img.height = stored.sha, stored.width, stored.height

            await asyncio.gather(*(download(img) for img in source.images))
        self._db.execute(
            "UPDATE third_party_sources SET images = %s WHERE user_id = %s AND source_id = %s",
            (source.images_json(), user_id, source.source_id),
        )
        return source
