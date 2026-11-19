"""第三方货源的解析方案：认出链接属于哪个网站，再用对应的方案提取标题、正文和图片。

识别分三步：先看网址（域名、路径、单页应用的 # 路由）；认不出就跟着短链接跳转，
跳到认识的网址就停；还认不出就读网页找特征（自定义域名的秀米、135编辑器、自我游商城）；
都不是就按普通网页取正文。后台可以手动换方案，手动选的方案取不到数据就直接报错，不悄悄换。

都不执行网页脚本：数据在网页里的直接读，单页应用按它自己的接口取。
"""

from __future__ import annotations

import asyncio
import base64
import json
import re
import secrets
import string
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

import httpx

from app.webpage import (
    Document,
    Element,
    canonical_url,
    clean_text,
    fetch,
    host_in,
    image_url,
    plain_text,
    text_content,
    with_fragment,
)

MAX_CONTENT = 50000
MAX_IMAGES = 100


class PageMismatchError(ValueError):
    """选的方案在这个网页上找不到它要的数据。"""

    def __init__(self, what: str):
        super().__init__(f"{what}；可以换一个解析方案再试")


@dataclass(frozen=True)
class Parser:
    id: str
    name: str  # 方案名
    sites: str  # 适用的网站
    summary: str  # 怎么取的


@dataclass
class SourceImage:
    url: str
    sha: str | None = None
    width: int | None = None
    height: int | None = None


@dataclass
class Parsed:
    url: str  # 规范链接：存库，同一链接再次导入是更新
    site: str  # 哪个网站
    title: str = ""
    content: str = ""
    author: str = ""
    images: list[SourceImage] = field(default_factory=list)
    parser: str = ""
    # 网站图片地址的改写（去掉转 webp 的参数、升级 https），去重前做
    rewrite: Callable[[str], str] | None = field(default=None, repr=False, compare=False)

    def add_image(self, raw: Any, base: str, width: Any = None, height: Any = None) -> None:
        url = image_url(raw, base)
        if url and self.rewrite:
            url = self.rewrite(url)
        if url and len(self.images) < MAX_IMAGES and all(img.url != url for img in self.images):
            self.images.append(SourceImage(url, width=_size(width), height=_size(height)))


def _size(value: Any) -> int | None:
    try:
        number = int(value)
        return number if number > 0 else None
    except (TypeError, ValueError):
        return None


def finish(parsed: Parsed) -> Parsed:
    parsed.title = clean_text(parsed.title)[:500]
    if not parsed.title:
        parsed.title = parsed.content.split("\n", 1)[0][:100]
    if not parsed.title or (not parsed.content and not parsed.images):
        raise ValueError("没有提取到正文或图片；这个链接可能需要登录，或不支持直接提取")
    if len(parsed.content) > MAX_CONTENT:
        raise ValueError(f"正文超过 {MAX_CONTENT} 字，暂时无法保存")
    return parsed


def rich_text(parsed: Parsed, html: str, base: str) -> str:
    """富文本片段：文字按块换行，图片按出现顺序收进 parsed。"""
    root = Document(html).root
    collect_images(parsed, root, base)
    return clean_text(text_content(root))


def collect_images(parsed: Parsed, node: Element, base: str) -> None:
    for img in node.walk():
        if img.tag == "img":
            parsed.add_image(
                img.attrs.get("data-src") or img.attrs.get("data-original") or img.attrs.get("src"),
                base,
                img.attrs.get("width"),
                img.attrs.get("height"),
            )


async def load(client: httpx.AsyncClient, url: str, html: str | None) -> tuple[str, str]:
    """识别时已经取过网页就复用，否则现取；返回（网页，最后一跳的地址）。"""
    if html is not None:
        return html, canonical_url(url)
    page = await fetch(client, url)
    return page.text(), page.url


Hit = tuple[str, str]  # （网站，链接是什么）


class Scheme:
    info: Parser

    def match_url(self, url: str) -> Hit | None:
        return None

    def match_page(self, url: str, html: str) -> Hit | None:
        return None

    async def parse(self, client: httpx.AsyncClient, url: str, html: str | None) -> Parsed:
        raise NotImplementedError


# —— 简篇、美篇：服务端渲染的 __INITIAL_STATE__ ——

JIANPIAN_HOSTS = ("jianpian.cn", "jianpian.info")
MEIPIAN_HOSTS = ("meipian.cn", "meipian.me", "ivwen.com")


def article_state(doc: Document) -> tuple[dict | None, dict]:
    for node in doc.root.walk():
        if node.tag != "script":
            continue
        script = node.script_text()
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
            return article, author if isinstance(author, dict) else {}
    return None, {}


def parse_article(html: str, url: str) -> Parsed:
    """简篇图片是懒加载，SSR 的 img 没有 src，但完整地址在这个 JSON 里。"""
    url = canonical_url(url)
    article, author = article_state(Document(html))
    if not article:
        raise PageMismatchError("网页里没有简篇、美篇的文章数据")
    parsed = Parsed(url, "简篇" if host_in(urlsplit(url).hostname, JIANPIAN_HOSTS) else "美篇")
    parsed.title = plain_text(str(article.get("title") or article.get("rich_text_title") or ""))
    parsed.author = str(author.get("nickname") or "")
    paragraphs = []
    for block in article["content"]:
        if not isinstance(block, dict):
            continue
        if block.get("text"):
            paragraphs.append(plain_text(str(block["text"])))
        parsed.add_image(block.get("img_url"), url, block.get("img_width"), block.get("img_height"))
    parsed.content = "\n\n".join(p for p in paragraphs if p)
    return finish(parsed)


class ArticleScheme(Scheme):
    info = Parser(
        "jianpian",
        "简篇/美篇文章数据",
        "简篇、美篇",
        "读网页里服务端写好的文章数据（__INITIAL_STATE__），段落文字和懒加载的图片都能拿到",
    )

    def match_url(self, url):
        host = urlsplit(url).hostname
        if host_in(host, JIANPIAN_HOSTS):
            return "简篇", "简篇文章"
        if host_in(host, MEIPIAN_HOSTS):
            return "美篇", "美篇文章"
        return None

    async def parse(self, client, url, html):
        html, url = await load(client, url, html)
        return await asyncio.to_thread(parse_article, html, url)


# —— 秀米：网页是空壳，排好版的内容在作品数据 JSON 里 ——

XIUMI_HOSTS = ("xiumi.us", "xiumius.cn")
SHOW_INFO = re.compile(r'showInfo\s*=\s*JSON\.parse\(decodeURIComponent\("([^"]*)"\)\)')


def xiumi_image(url: str) -> str:
    """秀米图床的 x-oss-process=style/xmwebp 会转成 webp，去掉拿原图。"""
    parts = urlsplit(url)
    if not host_in(parts.hostname, XIUMI_HOSTS):
        return url
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)]
    return urlunsplit(
        parts._replace(query=urlencode([q for q in query if q[0] != "x-oss-process"]))
    )


def xiumi_show_info(html: str) -> dict | None:
    match = SHOW_INFO.search(html)
    if not match:
        return None
    try:
        info = json.loads(unquote(match.group(1)))
    except ValueError:
        return None
    return info if isinstance(info, dict) else None


def xiumi_fragments(node: Any) -> Iterator[str]:
    """作品里每一层预先渲染好的 HTML（_$raHTML），跳过背景层。"""
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "_$raHTML" and isinstance(value, str):
                yield value
            elif key != "grounds":
                yield from xiumi_fragments(value)
    elif isinstance(node, list):
        for value in node:
            yield from xiumi_fragments(value)


def parse_xiumi(info: dict, show: dict, url: str) -> Parsed:
    show_url = image_url(info.get("show_url"), url)  # 作品的正式地址，换分享链接也不变
    parsed = Parsed(show_url or canonical_url(url), "秀米", rewrite=xiumi_image)
    parsed.title = str(show.get("title") or info.get("title") or "")
    owner = info.get("owner")
    parsed.author = str(owner.get("nickname") or "") if isinstance(owner, dict) else ""
    fragments = list(xiumi_fragments(show.get("cubes")))
    preview = show.get("$appendix", {}).get("htmlForPreview") if isinstance(show, dict) else ""
    if not fragments and isinstance(preview, str) and preview:
        fragments = [preview]
    if not fragments:
        raise PageMismatchError("秀米作品里没有可读的排版内容")
    texts = [rich_text(parsed, fragment, url) for fragment in fragments]
    parsed.content = "\n".join(text for text in texts if text)
    if not parsed.images:
        parsed.add_image(show.get("cover") or info.get("cover"), url)
    return finish(parsed)


class XiumiScheme(Scheme):
    info = Parser(
        "xiumi",
        "秀米作品数据",
        "秀米",
        "秀米网页只是空壳：先从网页里找到作品数据的地址，再读作品数据里排好版的文字和图片",
    )

    def match_url(self, url):
        parts = urlsplit(url)
        if host_in(parts.hostname, XIUMI_HOSTS) and parts.path.strip("/"):
            return "秀米", "秀米图文作品"
        return None

    def match_page(self, url, html):
        if "show_data_url" in html and xiumi_show_info(html):
            return "秀米", "秀米图文作品（自定义域名）"
        return None

    async def parse(self, client, url, html):
        html, url = await load(client, url, html)
        info = xiumi_show_info(html)
        data_url = image_url(info.get("show_data_url"), url) if info else None
        if not info or not data_url:
            raise PageMismatchError("网页里没有秀米作品信息")
        data = await fetch(client, data_url, kind="json", referer=url)
        try:
            show = json.loads(data.text())
        except ValueError as exc:
            raise ValueError("秀米作品数据读不出来，请稍后重试") from exc
        if not isinstance(show, dict):
            raise PageMismatchError("秀米作品数据的格式认不出")
        return await asyncio.to_thread(parse_xiumi, info, show, url)


# —— 自我游分销商城：Vue 单页应用，以访客身份调它的商城接口 ——

ZIWOYOU_HOSTS = ("ziwoyou.net",)
ZIWOYOU_API = "https://task-api.zowoyoo.com/api"
ZIWOYOU_HEADERS = {"channel": "19", "lang": "zh"}  # 19：H5 商城
PRODUCT_PATH = re.compile(r"/productdetail/(\d+)")


def ziwoyou_product(url: str) -> tuple[str, dict[str, str]] | None:
    """商品号在路径（/newb2c/productdetail/1）或 # 路由（/m2/#/productdetail/1?userId=…）里。"""
    parts = urlsplit(url)
    route, _, route_query = parts.fragment.partition("?")
    params = dict(parse_qsl(parts.query)) | dict(parse_qsl(route_query))
    for path in (route, parts.path):
        if match := PRODUCT_PATH.search(path):
            return match.group(1), params
    return None


def ziwoyou_image(url: str) -> str:
    """商品图床 qnimg 的 http 偶尔连不上，https 一样能取。"""
    parts = urlsplit(url)
    if parts.scheme == "http" and host_in(parts.hostname, ("zowoyoo.com",)):
        return urlunsplit(parts._replace(scheme="https"))
    return url


def money(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    return f"{number:.2f}".rstrip("0").rstrip(".") if number > 0 else ""


def parse_ziwoyou(
    url: str, product_id: str, params: dict, shop: str, detail: dict, content: str, notes: str
) -> Parsed:
    parts = urlsplit(url)
    query = urlencode([(k, params[k]) for k in ("userId", "orderCustId") if params.get(k)])
    page = urlunsplit(
        (parts.scheme, parts.netloc, f"/newb2c/productdetail/{product_id}", query, "")
    )
    parsed = Parsed(page, "自我游分销商城", author=shop, rewrite=ziwoyou_image)
    parsed.title = str(detail.get("infoTitle") or "")
    lines = []
    if subtitle := plain_text(str(detail.get("infoTitleDesc") or "")):
        lines.append(subtitle)
    if price := money(detail.get("salePrice")):
        unit = str(detail.get("unit") or "")
        market = money(detail.get("marketPrice"))
        lines.append(
            f"售价：{price} 元{f'/{unit}' if unit else ''}"
            + (f"（门市价 {market} 元）" if market and float(market) > float(price) else "")
        )
    if area := str(detail.get("areaName") or ""):
        lines.append(f"地区：{area}")
    for view in detail.get("viewNames") or []:
        if isinstance(view, dict) and view.get("name"):
            address = str(view.get("address") or "").strip()
            lines.append(f"景点/酒店：{view['name']}" + (f"（{address}）" if address else ""))
    for image in [*(detail.get("pictures") or []), *(detail.get("contentImgs") or [])]:
        parsed.add_image(image, page)
    sections = [
        ("费用包含", plain_text(str(detail.get("chargeInclude") or ""))),
        ("费用不含", plain_text(str(detail.get("chargeNoInclude") or ""))),
        ("重要提示", plain_text(str(detail.get("importantNote") or ""))),
        ("图文详情", rich_text(parsed, content, page)),
        ("使用说明", plain_text(str(detail.get("userNote") or ""))),
        ("退改说明", plain_text(str(detail.get("refundNote") or ""))),
        ("预订须知", rich_text(parsed, notes, page)),
    ]
    blocks = ["\n".join(lines)] + [f"【{name}】\n{text}" for name, text in sections if text]
    parsed.content = "\n\n".join(block for block in blocks if block)
    parsed.add_image(detail.get("signImg"), page)
    return finish(parsed)


async def ziwoyou_api(
    client: httpx.AsyncClient, path: str, headers: dict[str, str], **kwargs
) -> tuple[Any, httpx.Headers]:
    response = await fetch(client, f"{ZIWOYOU_API}{path}", kind="json", headers=headers, **kwargs)
    try:
        body = json.loads(response.text())
    except ValueError as exc:
        raise ValueError("自我游商城接口返回的数据读不出来，请稍后重试") from exc
    if not isinstance(body, dict) or body.get("state") != 1:
        message = body.get("msg") if isinstance(body, dict) else ""
        raise ValueError(f"自我游商城接口没有返回数据：{message or '未知原因'}")
    return body.get("data"), response.headers


class ZiwoyouScheme(Scheme):
    info = Parser(
        "ziwoyou",
        "自我游商城接口",
        "自我游分销商城（含 s.leleku.cn 短链接）",
        "商城是单页应用，网页里没有正文：以访客身份登录商城接口，"
        "取商品标题、价格、图文详情和预订须知",
    )

    def match_url(self, url):
        product = ziwoyou_product(url)
        if product and host_in(urlsplit(url).hostname, ZIWOYOU_HOSTS):
            return "自我游分销商城", f"商城商品页（商品号 {product[0]}）"
        return None

    def match_page(self, url, html):
        product = ziwoyou_product(url)
        if product and "zowoyoo.com/saasb2" in html:
            return "自我游分销商城", f"商城商品页（商品号 {product[0]}，自定义域名）"
        return None

    async def parse(self, client, url, html):
        product = ziwoyou_product(url)
        if not product:
            raise PageMismatchError("链接里没有自我游商城的商品号（/productdetail/数字）")
        product_id, params = product
        host = urlsplit(url).hostname or ""
        site = base64.b64encode(host.encode()).decode()
        saas, _ = await ziwoyou_api(client, f"/user/saasInfo?url={site}", ZIWOYOU_HEADERS)
        if not isinstance(saas, dict) or not saas.get("parentCustId"):
            raise PageMismatchError(f"{host} 不是自我游分销商城")
        # 和浏览器里打开一样：随机一个访客 openId 登录，令牌在响应头 authtoken 里
        alphabet = string.ascii_letters + string.digits
        _, headers = await ziwoyou_api(
            client,
            "/user/login",
            ZIWOYOU_HEADERS,
            method="POST",
            json={
                "code": "",
                "appId": "",
                "unionId": "",
                "parentCustId": str(saas["parentCustId"]),
                "openId": "".join(secrets.choice(alphabet) for _ in range(45)),
                "orderCustId": params.get("orderCustId", ""),
                "userId": params.get("userId", ""),
                "client": "",
                "saleChannel": 4,
                "h5LoginUserRequest": {
                    "avatarUrl": "",
                    "email": "",
                    "identification": "",
                    "nickName": "",
                    "realName": "",
                },
            },
        )
        token = headers.get("authtoken")
        if not token:
            raise ValueError("自我游商城没有发放访客令牌，请稍后重试")
        auth = {**ZIWOYOU_HEADERS, "authtoken": token}
        base = "/product/common"
        detail, _ = await ziwoyou_api(client, f"{base}/prodDetail/{product_id}", auth)
        if not isinstance(detail, dict):
            raise ValueError("自我游商城里找不到这个商品，可能已经下架")
        content, _ = await ziwoyou_api(client, f"{base}/product/content/{product_id}", auth)
        notes, _ = await ziwoyou_api(client, f"{base}/product/desc/{product_id}", auth)
        return await asyncio.to_thread(
            parse_ziwoyou,
            url,
            product_id,
            params,
            str(saas.get("webName") or ""),
            detail,
            content if isinstance(content, str) else "",
            notes if isinstance(notes, str) else "",
        )


# —— 135编辑器：分享页（常挂自定义域名）把文章写在脚本的 data 变量里 ——

SPVIEW_PATH = re.compile(r"^/wx_msgs/spview/\d+")
DATA_VAR = re.compile(r"\bvar\s+data\s*=\s*")


def editor135_data(html: str) -> dict | None:
    for match in DATA_VAR.finditer(html):
        try:
            data, _ = json.JSONDecoder().raw_decode(html[match.end() :])
        except ValueError:
            continue
        if isinstance(data, dict) and isinstance(data.get("content"), str):
            return data
    return None


def parse_editor135(html: str, url: str) -> Parsed:
    url = canonical_url(url)
    data = editor135_data(html)
    if not data:
        raise PageMismatchError("网页里没有135编辑器的文章数据")
    # 这类分享页的文章常从自我游商城复制，图也在同一个图床，一样升级 https
    parsed = Parsed(url, "135编辑器", title=str(data.get("name") or ""), rewrite=ziwoyou_image)
    parsed.author = str(data.get("author") or "")
    parsed.content = rich_text(parsed, data["content"], url) or str(data.get("summary") or "")
    if not parsed.images:
        parsed.add_image(data.get("coverimg"), url)
    return finish(parsed)


class Editor135Scheme(Scheme):
    info = Parser(
        "editor135",
        "135编辑器文章数据",
        "135编辑器（含自定义域名的 /wx_msgs/spview/ 分享页）",
        "正文由网页脚本填进页面：直接读脚本里 data 变量的文章标题和排版内容",
    )

    def match_url(self, url):
        parts = urlsplit(url)
        if SPVIEW_PATH.match(parts.path) or (
            host_in(parts.hostname, ("135editor.com",)) and parts.path.startswith("/wx_msgs/")
        ):
            return "135编辑器", "135编辑器图文分享页"
        return None

    def match_page(self, url, html):
        if "135editor" in html and editor135_data(html):
            return "135编辑器", "135编辑器图文分享页（自定义域名）"
        return None

    async def parse(self, client, url, html):
        html, url = await load(client, url, html)
        return await asyncio.to_thread(parse_editor135, html, url)


# —— 普通网页：article、main 或常见的正文容器 ——


def parse_generic(html: str, url: str) -> Parsed:
    """不执行脚本，保留正文顺序；图片去重，排除页面的头像、图标。"""
    url = canonical_url(url)
    doc = Document(html)
    nodes = list(doc.root.walk())
    meta = {
        n.attrs.get("property") or n.attrs.get("name"): n.attrs.get("content", "")
        for n in nodes
        if n.tag == "meta"
    }
    parsed = Parsed(url, (meta.get("og:site_name") or "").strip()[:50] or "网页")
    title_node = next((n for n in nodes if n.tag == "title"), None)
    parsed.title = meta.get("og:title") or (
        clean_text(text_content(title_node)) if title_node else ""
    )
    parsed.author = meta.get("author", "")
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
    parsed.content = clean_text(text_content(body))
    collect_images(parsed, body, url)
    if not parsed.images:
        parsed.add_image(meta.get("og:image"), url)
    return finish(parsed)


class GenericScheme(Scheme):
    info = Parser(
        "generic",
        "通用网页正文",
        "任意公开网页",
        "取 article、main 或常见正文容器里的文字和图片；靠脚本加载内容的网页取不全",
    )

    async def parse(self, client, url, html):
        html, url = await load(client, url, html)
        return await asyncio.to_thread(parse_generic, html, url)


SCHEMES: list[Scheme] = [
    ArticleScheme(),
    XiumiScheme(),
    ZiwoyouScheme(),
    Editor135Scheme(),
    GenericScheme(),
]
BY_ID = {scheme.info.id: scheme for scheme in SCHEMES}
PARSERS = [scheme.info for scheme in SCHEMES]


def parser_info(parser_id: str) -> Parser:
    return BY_ID[parser_id].info if parser_id in BY_ID else BY_ID["generic"].info


def match_url(url: str) -> tuple[Scheme, Hit] | None:
    for scheme in SCHEMES:
        if hit := scheme.match_url(url):
            return scheme, hit
    return None


MatchedBy = Literal["url", "redirect", "page", "fallback"]


@dataclass
class Detection:
    input_url: str  # 粘贴的链接（规范化，保留 # 路由）
    url: str  # 识别用的地址：短链接跳转后的，保留 # 路由
    parser: str
    site: str  # 哪个网站
    link: str  # 链接是什么
    matched_by: MatchedBy
    html: str | None = field(default=None, repr=False)  # 识别时取到的网页，解析时复用

    @property
    def reason(self) -> str:
        first, last = urlsplit(self.input_url).hostname, urlsplit(self.url).hostname
        hop = f"从 {first} 跳转到 {last}，" if first != last else ""
        return {
            "url": "按网址识别",
            "redirect": f"短链接 {first} 跳转到 {last}，按跳转后的网址识别",
            "page": f"{hop}按网页里的特征识别",
            "fallback": f"{hop}没有认出专门的网站，按普通网页提取",
        }[self.matched_by]

    def public(self) -> dict[str, Any]:
        return {
            "input_url": self.input_url,
            "url": self.url,
            "host": urlsplit(self.url).hostname or "",
            "site": self.site,
            "link": self.link,
            "parser": self.parser,
            "matched_by": self.matched_by,
            "reason": self.reason,
        }


async def detect(client: httpx.AsyncClient, raw_url: str) -> Detection:
    """先看网址；认不出就跟着跳转，跳到认识的网址就停；再认不出就读网页找特征。"""
    url = with_fragment(canonical_url(raw_url), urlsplit(raw_url.strip()).fragment)
    if matched := match_url(url):
        scheme, (site, link) = matched
        return Detection(url, url, scheme.info.id, site, link, "url")
    page = await fetch(client, url, stop=lambda hop: match_url(hop) is not None)
    if page.data is None:
        scheme, (site, link) = match_url(page.full_url)  # type: ignore[misc]
        return Detection(url, page.full_url, scheme.info.id, site, link, "redirect")
    html = page.text()
    for scheme in SCHEMES:
        if hit := scheme.match_page(page.full_url, html):
            site, link = hit
            return Detection(url, page.full_url, scheme.info.id, site, link, "page", html)
    return Detection(url, page.full_url, "generic", "网页", "普通网页", "fallback", html)


async def parse(client: httpx.AsyncClient, detection: Detection, parser: str | None) -> Parsed:
    """parser 为空时用识别出的方案；手动选的方案取不到数据就报错，不悄悄换。"""
    parser = parser or detection.parser
    if parser not in BY_ID:
        raise ValueError("没有这个解析方案")
    parsed = await BY_ID[parser].parse(client, detection.url, detection.html)
    parsed.parser = parser
    return parsed
