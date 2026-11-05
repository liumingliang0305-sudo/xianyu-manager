"""拼多多货源：浏览器插件在拼多多商品页上读出商品数据推过来，存进本地库，图片下载到本机。

数据来源：商品页（mobile.yangkeduo.com/goods*.html）服务端渲染时写在页面里的
`window.rawData.store.initDataObj.goods`（2026-11 实测，见 docs/protocol-notes.md#拼多多商品页）。
要在浏览器里登录拼多多才看得到商品页。插件只挑这里用到的字段发过来（不带页面里的登录用户、分享参数），
这里再整理成固定的格式：标题、主图、详情图、视频、规格（SKU）、商品参数、价格区间、店铺。
拼多多的详情全是图片，没有文字介绍。

货源按管理后台的用户存，不跟闲鱼账号走：同一个货源可以在名下任何一个账号建发布草稿。
同一个商品再导入一次是更新（下载过的图不再下）。

图片（主图、详情图、规格图）导入时下载到 data/images（app/images.py，和草稿共用，
同一张图只存一份），建草稿时直接用；视频只记地址，不下载。拼多多的图片、视频地址不用登录也能下载。
只下载拼多多自己 CDN 上的地址（*.pddpic.com、*.yangkeduo.com），别的地址直接丢掉。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import httpx

from app.db import Database, Row
from app.images import ImageStore, drop_unused
from xianyu_core.publish import MAX_DESC, MAX_IMAGES

log = logging.getLogger(__name__)

MEDIA_HOSTS = ("pddpic.com", "yangkeduo.com")
# 一个商品最多收多少：页面上正常远到不了，防止有人塞一大堆地址进来
MAX_GALLERY = 30
MAX_DETAIL = 80
MAX_SKUS = 300
DOWNLOAD_CONCURRENCY = 6
DOWNLOAD_TIMEOUT = 30.0


class PddGoodsNotFoundError(LookupError):
    pass


@dataclass
class PddImage:
    url: str
    sha: str | None = None  # 下载下来以后 data/images 里的编号，还没下载（或者下载失败）是 None
    width: int | None = None  # 下载下来以后的宽高；详情图页面上就有
    height: int | None = None


@dataclass
class PddVideo:
    url: str
    cover: str = ""  # 封面图地址，没有是空串


@dataclass
class PddSku:
    sku_id: str
    specs: list[dict[str, str]]  # [{"key": "款式", "value": "10KG…"}]
    price_cent: int | None  # 拼单价
    normal_price_cent: int | None  # 单独购买价
    quantity: int | None  # 库存
    on_sale: bool
    thumb: PddImage | None

    @property
    def name(self) -> str:
        return " / ".join(spec["value"] for spec in self.specs)


@dataclass
class PddGoods:
    goods_id: str
    title: str
    mall_name: str = ""
    sales_tip: str = ""  # 页面上的"热卖12.4万+件"
    min_price_cent: int | None = None  # 拼单价的区间
    max_price_cent: int | None = None
    min_normal_price_cent: int | None = None  # 单独购买价的区间
    max_normal_price_cent: int | None = None
    images: list[PddImage] = field(default_factory=list)  # 主图（轮播图）
    detail_images: list[PddImage] = field(default_factory=list)
    videos: list[PddVideo] = field(default_factory=list)
    skus: list[PddSku] = field(default_factory=list)
    properties: list[dict[str, str]] = field(default_factory=list)  # [{"key": "品牌", "value": …}]
    imported_at: float = 0.0
    updated_at: float = 0.0

    @property
    def url(self) -> str:
        return goods_url(self.goods_id)

    def all_images(self) -> Iterator[PddImage]:
        yield from self.images
        yield from self.detail_images
        for sku in self.skus:
            if sku.thumb:
                yield sku.thumb

    def shas(self) -> set[str]:
        return {img.sha for img in self.all_images() if img.sha}

    def missing(self) -> int:
        """还没下载下来的图片有几张（同一个地址算一张）。"""
        return len({img.url for img in self.all_images() if not img.sha})

    def to_data(self) -> str:
        """存进 data 列的部分（列表类的），别的有单独的列。"""
        return json.dumps(
            {
                "sales_tip": self.sales_tip,
                "min_normal_price_cent": self.min_normal_price_cent,
                "max_normal_price_cent": self.max_normal_price_cent,
                "images": [asdict(img) for img in self.images],
                "detail_images": [asdict(img) for img in self.detail_images],
                "videos": [asdict(video) for video in self.videos],
                "skus": [asdict(sku) for sku in self.skus],
                "properties": self.properties,
            },
            ensure_ascii=False,
        )

    @classmethod
    def from_row(cls, row: Row) -> PddGoods:
        data = json.loads(row["data"])
        return cls(
            goods_id=row["goods_id"],
            title=row["title"],
            mall_name=row["mall_name"],
            min_price_cent=row["min_price_cent"],
            max_price_cent=row["max_price_cent"],
            imported_at=row["imported_at"],
            updated_at=row["updated_at"],
            sales_tip=data.get("sales_tip", ""),
            min_normal_price_cent=data.get("min_normal_price_cent"),
            max_normal_price_cent=data.get("max_normal_price_cent"),
            images=[PddImage(**img) for img in data.get("images", [])],
            detail_images=[PddImage(**img) for img in data.get("detail_images", [])],
            videos=[PddVideo(**video) for video in data.get("videos", [])],
            skus=[
                PddSku(**{**sku, "thumb": PddImage(**sku["thumb"]) if sku["thumb"] else None})
                for sku in data.get("skus", [])
            ],
            properties=data.get("properties", []),
        )


def goods_url(goods_id: str) -> str:
    """商品页的地址（不带分享参数）。"""
    return f"https://mobile.yangkeduo.com/goods.html?goods_id={goods_id}"


def parse(payload: dict[str, Any]) -> PddGoods:
    """插件发来的 {"goods": rawData 里挑出来的字段, "mall": {...}} → PddGoods。

    缺商品 ID、标题时抛 ValueError；别的字段缺了、格式不对的跳过。
    """
    goods = payload.get("goods")
    if not isinstance(goods, dict):
        raise ValueError("没有商品数据")
    goods_id = str(goods.get("goodsID") or "").strip()
    if not goods_id.isdigit():
        raise ValueError("商品数据里没有商品 ID")
    title = str(goods.get("goodsName") or "").strip()
    if not title:
        raise ValueError("商品数据里没有标题")
    mall = payload.get("mall") if isinstance(payload.get("mall"), dict) else {}

    images = _images(goods.get("topGallery"), MAX_GALLERY)
    detail = _images(goods.get("detailGallery"), MAX_DETAIL, sized=True)
    return PddGoods(
        goods_id=goods_id,
        title=title,
        mall_name=str(mall.get("mallName") or "").strip(),
        sales_tip=str(goods.get("sideSalesTip") or "").strip(),
        min_price_cent=_cent(goods.get("minGroupPrice")),
        max_price_cent=_cent(goods.get("maxGroupPrice")),
        min_normal_price_cent=_cent(goods.get("minNormalPrice")),
        max_normal_price_cent=_cent(goods.get("maxNormalPrice")),
        images=images,
        detail_images=detail,
        videos=_videos(goods.get("videoGallery"), goods.get("descVideoGallery")),
        skus=_skus(goods.get("skus")),
        properties=_properties(goods.get("goodsProperty")),
    )


def draft_text(goods: PddGoods) -> str:
    """建发布草稿时的描述：标题、商品参数、可选的规格。超过闲鱼的字数上限时从后面去掉几行。"""
    lines = [goods.title]
    if goods.properties:
        lines.append("")
        lines += [f"{prop['key']}：{prop['value']}" for prop in goods.properties]
    if len(goods.skus) > 1:
        options: dict[str, list[str]] = {}
        for sku in goods.skus:
            for spec in sku.specs:
                values = options.setdefault(spec["key"], [])
                if spec["value"] not in values:
                    values.append(spec["value"])
        for key, values in options.items():
            lines += ["", f"可选{key}："]
            lines += [f"- {value}" for value in values]
    while len("\n".join(lines)) > MAX_DESC and len(lines) > 1:
        lines.pop()
    return "\n".join(lines).strip()[:MAX_DESC]


class PddService:
    """货源的增删查，和下载图片。`transport` 给测试换假的拼多多 CDN。"""

    def __init__(
        self,
        db: Database,
        images: ImageStore,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        self._db = db
        self._images = images
        self._transport = transport
        # 同一个货源同一时间只有一处在改（插件连点两下、页面上同时点重新下载）
        self._locks: defaultdict[tuple[int, str], asyncio.Lock] = defaultdict(asyncio.Lock)

    def list(self, user_id: int) -> list[PddGoods]:
        rows = self._db.execute(
            "SELECT * FROM pdd_goods WHERE user_id = %s ORDER BY updated_at DESC", (user_id,)
        )
        return [PddGoods.from_row(row) for row in rows]

    def get(self, user_id: int, goods_id: str) -> PddGoods:
        goods = self._find(user_id, goods_id)
        if goods is None:
            raise PddGoodsNotFoundError(f"找不到拼多多商品 {goods_id}")
        return goods

    async def ingest(self, user_id: int, payload: dict[str, Any]) -> tuple[PddGoods, bool]:
        """存一个插件推过来的商品，下载图片。返回 (商品, 是不是新加的)。

        已经导入过的是更新：下载过的图（按地址认）不再下，商品页上没了的图跟着删掉。
        """
        goods = parse(payload)
        async with self._locks[(user_id, goods.goods_id)]:
            old = self._find(user_id, goods.goods_id)
            if old:
                saved = {img.url: img for img in old.all_images() if img.sha}
                for img in goods.all_images():
                    if img.url in saved:
                        done = saved[img.url]
                        img.sha, img.width, img.height = done.sha, done.width, done.height
            now = time.time()
            self._db.execute(
                "INSERT INTO pdd_goods (user_id, goods_id, title, mall_name, min_price_cent,"
                " max_price_cent, data, imported_at, updated_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)"
                " ON CONFLICT (user_id, goods_id) DO UPDATE SET title = excluded.title,"
                " mall_name = excluded.mall_name, min_price_cent = excluded.min_price_cent,"
                " max_price_cent = excluded.max_price_cent, data = excluded.data,"
                " updated_at = excluded.updated_at",
                (
                    user_id,
                    goods.goods_id,
                    goods.title,
                    goods.mall_name,
                    goods.min_price_cent,
                    goods.max_price_cent,
                    goods.to_data(),
                    now,
                    now,
                ),
            )
            if old:
                drop_unused(self._db, self._images, old.shas() - goods.shas())
            result = await self._download(user_id, goods.goods_id)
        return result, old is None

    async def fetch_images(self, user_id: int, goods_id: str) -> PddGoods:
        """把还没下载下来的图片再下一次（上次网络出错的）。"""
        self.get(user_id, goods_id)
        async with self._locks[(user_id, goods_id)]:
            return await self._download(user_id, goods_id)

    def delete(self, user_id: int, goods_id: str) -> None:
        """删掉货源和只有它用的图片。照它建的草稿不受影响（草稿用的图还在用，不会删）。"""
        goods = self.get(user_id, goods_id)
        self._db.execute(
            "DELETE FROM pdd_goods WHERE user_id = %s AND goods_id = %s", (user_id, goods_id)
        )
        drop_unused(self._db, self._images, goods.shas())

    def draft_images(self, goods: PddGoods) -> list[PddImage]:
        """建草稿用的图：下载下来的主图，最多 9 张。"""
        return [img for img in goods.images if img.sha][:MAX_IMAGES]

    def _find(self, user_id: int, goods_id: str) -> PddGoods | None:
        row = self._db.execute(
            "SELECT * FROM pdd_goods WHERE user_id = %s AND goods_id = %s", (user_id, goods_id)
        ).fetchone()
        return PddGoods.from_row(row) if row else None

    async def _download(self, user_id: int, goods_id: str) -> PddGoods:
        """下载还没下的图，下完存一次。下载失败的留着地址，sha 还是 None。"""
        goods = self.get(user_id, goods_id)
        pending: dict[str, list[PddImage]] = defaultdict(list)
        for img in goods.all_images():
            if not img.sha:
                pending[img.url].append(img)
        if not pending:
            return goods
        gate = asyncio.Semaphore(DOWNLOAD_CONCURRENCY)
        async with httpx.AsyncClient(
            transport=self._transport, timeout=DOWNLOAD_TIMEOUT, follow_redirects=True
        ) as client:

            async def fetch(url: str) -> None:
                async with gate:
                    try:
                        resp = await client.get(url)
                        resp.raise_for_status()
                        # 转 JPEG 要解压、压缩，放到线程里做，不卡住消息连接这些
                        stored = await asyncio.to_thread(self._images.save, resp.content)
                    except (httpx.HTTPError, ValueError, OSError) as exc:
                        log.warning("下载拼多多图片失败 %s：%s", url, exc)
                        return
                for img in pending[url]:
                    img.sha, img.width, img.height = stored.sha, stored.width, stored.height

            await asyncio.gather(*(fetch(url) for url in pending))
        self._db.execute(
            "UPDATE pdd_goods SET data = %s WHERE user_id = %s AND goods_id = %s",
            (goods.to_data(), user_id, goods_id),
        )
        return goods


# ---- 整理插件发来的数据 ----


def media_url(value: Any) -> str | None:
    """拼多多 CDN 上的图片、视频地址：补上 https，去掉缩略参数（?imageMogr2/… 去掉就是原图）。
    不是拼多多自己域名的返回 None。"""
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    if text.startswith("//"):
        text = f"https:{text}"
    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    if parts.scheme not in ("http", "https") or not any(
        host == root or host.endswith(f".{root}") for root in MEDIA_HOSTS
    ):
        return None
    return urlunsplit(("https", parts.netloc, parts.path, "", ""))


def _images(entries: Any, limit: int, *, sized: bool = False) -> list[PddImage]:
    images: list[PddImage] = []
    seen: set[str] = set()
    for entry in entries if isinstance(entries, list) else []:
        raw = entry.get("url") if isinstance(entry, dict) else entry
        url = media_url(raw)
        if not url or url in seen:
            continue
        seen.add(url)
        img = PddImage(url)
        if sized and isinstance(entry, dict):
            img.width, img.height = _int(entry.get("width")), _int(entry.get("height"))
        images.append(img)
        if len(images) >= limit:
            break
    return images


def _videos(*galleries: Any) -> list[PddVideo]:
    videos: list[PddVideo] = []
    seen: set[str] = set()
    for gallery in galleries:
        for entry in gallery if isinstance(gallery, list) else []:
            if not isinstance(entry, dict):
                continue
            url = media_url(entry.get("videoUrl") or entry.get("url"))
            if not url or url in seen or not url.endswith(".mp4"):
                continue
            seen.add(url)
            cover = media_url(entry.get("url")) or ""
            videos.append(PddVideo(url, "" if cover.endswith(".mp4") else cover))
    return videos


def _skus(entries: Any) -> list[PddSku]:
    skus: list[PddSku] = []
    for entry in (entries if isinstance(entries, list) else [])[:MAX_SKUS]:
        if not isinstance(entry, dict):
            continue
        specs = [
            {
                "key": str(spec.get("spec_key") or "").strip(),
                "value": str(spec["spec_value"]).strip(),
            }
            for spec in entry.get("specs") or []
            if isinstance(spec, dict) and spec.get("spec_value")
        ]
        thumb = media_url(entry.get("thumbUrl"))
        skus.append(
            PddSku(
                sku_id=str(entry.get("skuId") or entry.get("skuID") or ""),
                specs=specs,
                price_cent=_cent(entry.get("groupPrice")),
                normal_price_cent=_cent(entry.get("normalPrice")),
                quantity=_int(entry.get("quantity")),
                on_sale=entry.get("isOnsale", 1) == 1,
                thumb=PddImage(thumb) if thumb else None,
            )
        )
    return skus


def _properties(entries: Any) -> list[dict[str, str]]:
    props = []
    for entry in entries if isinstance(entries, list) else []:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "").strip()
        values = [str(v).strip() for v in entry.get("values") or [] if str(v).strip()]
        if key and values:
            props.append({"key": key, "value": "、".join(values)})
    return props


_PRICE = re.compile(r"^\d+(\.\d{1,2})?$")


def _cent(value: Any) -> int | None:
    """页面上的价格是元的字符串（"298"、"29.9"）→ 分；没有、不像价格的是 None。"""
    text = str(value).strip() if value is not None else ""
    if not _PRICE.match(text):
        return None
    try:
        return int(Decimal(text) * 100)
    except InvalidOperation:
        return None


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
