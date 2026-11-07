"""搜索暂存：搜索页上挑出来的商品（别人的）留一份，给以后的操作用（比如照同行的文案写自己的）。

两部分，都是页面发过来的，这里校验、整理后存下，后台不请求闲鱼：
- 搜到时搜索结果里的样子：标题、售价、原价、主图、发货地、卖家、想要人数、发布时间、标签，
  再加上是用哪个词搜到的；
- 商品详情：完整描述、全部图片、浏览收藏、在售还是卖掉了（pc.detail 的 itemDO）。
  后台直接调 pc.detail 看别人的商品，第一次就要滑块（2026-11-07 两次都是，见
  docs/protocol-notes.md#看别人的商品详情），所以由浏览器插件在用户自己的 Chrome 里打开商品页、
  用页面自己的请求库拿（extension/goofish.js），交给管理后台页面，页面再发到这里（save_detail）。
  没拿到过的只有搜到时的样子。

按管理后台的用户存，不跟闲鱼账号走：用哪个账号搜到的都放在一起，名下账号共用。
同一件商品再暂存一次是更新成这次搜到的样子，第一次暂存的时间不变。
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from app.db import Database, Row
from xianyu_core.constants import WEB_HOST
from xianyu_core.item import ItemDetail

# 搜索结果里正常远到不了，防止有人塞一大堆进来
MAX_TEXT = 500
MAX_URL = 1000
MAX_TAGS = 20
MAX_TAG = 50
MAX_DESCRIPTION = 10000
MAX_IMAGES = 30


class StashNotFoundError(LookupError):
    pass


@dataclass
class StashedItem:
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
    tags: list[str] = field(default_factory=list)
    seller_tags: list[str] = field(default_factory=list)
    keyword: str = ""  # 用哪个词搜到的
    # 商品详情，没拿到过时 detail_at 是 None、别的是空的
    description: str = ""
    images: list[str] = field(default_factory=list)
    browse_count: int | None = None
    collect_count: int | None = None
    status: int | None = None  # 0 在售，1 已卖出
    status_text: str = ""
    detail_at: float | None = None
    saved_at: float = 0.0
    updated_at: float = 0.0

    @property
    def url(self) -> str:
        return f"{WEB_HOST}/item?id={self.item_id}"

    @classmethod
    def clean(cls, fields: dict[str, Any]) -> StashedItem:
        """页面发来的搜索结果（字段和搜索接口返回的一样）→ StashedItem。不对的抛 ValueError。"""
        item_id = str(fields.get("item_id") or "").strip()
        if not item_id.isdigit() or len(item_id) > 32:
            raise ValueError("商品 ID 不对")
        title = _text(fields.get("title"))
        if not title:
            raise ValueError("商品没有标题")
        price = fields.get("price_cent")
        if not isinstance(price, int) or price < 0:
            raise ValueError("售价不对")
        orig = fields.get("orig_price_cent")
        pic = _text(fields.get("pic_url"), MAX_URL)
        if pic and not pic.startswith(("http://", "https://")):
            raise ValueError("主图地址不对")
        want = fields.get("want_count")
        published = fields.get("published_at")
        if not isinstance(published, int | float) or published <= 0:
            published = None
        return cls(
            item_id=item_id,
            title=title,
            price_cent=price,
            orig_price_cent=orig if isinstance(orig, int) and orig > 0 else None,
            pic_url=pic,
            area=_text(fields.get("area")),
            seller_nick=_text(fields.get("seller_nick")),
            want_count=want if isinstance(want, int) and want >= 0 else None,
            published_at=float(published) if published else None,
            free_ship=fields.get("free_ship") is True,
            tags=_tags(fields.get("tags")),
            seller_tags=_tags(fields.get("seller_tags")),
            keyword=_text(fields.get("keyword")),
        )

    @classmethod
    def from_row(cls, row: Row) -> StashedItem:
        return cls(
            item_id=row["item_id"],
            title=row["title"],
            price_cent=row["price_cent"],
            orig_price_cent=row["orig_price_cent"],
            pic_url=row["pic_url"],
            area=row["area"],
            seller_nick=row["seller_nick"],
            want_count=row["want_count"],
            published_at=row["published_at"],
            free_ship=row["free_ship"],
            tags=json.loads(row["tags"]),
            seller_tags=json.loads(row["seller_tags"]),
            keyword=row["keyword"],
            description=row["description"],
            images=json.loads(row["images"]),
            browse_count=row["browse_count"],
            collect_count=row["collect_count"],
            status=row["status"],
            status_text=row["status_text"],
            detail_at=row["detail_at"],
            saved_at=row["saved_at"],
            updated_at=row["updated_at"],
        )


class StashService:
    def __init__(self, db: Database):
        self._db = db

    def list(self, user_id: int) -> list[StashedItem]:
        """最近暂存的在前。"""
        rows = self._db.execute(
            "SELECT * FROM search_stash WHERE user_id = %s ORDER BY saved_at DESC, item_id",
            (user_id,),
        )
        return [StashedItem.from_row(row) for row in rows]

    def save(self, user_id: int, item: StashedItem) -> tuple[StashedItem, bool]:
        """存一件，返回 (存下的, 是不是新加的)。已经暂存过的更新成这次的样子，暂存时间不变。"""
        now = time.time()
        row = self._db.execute(
            "INSERT INTO search_stash (user_id, item_id, title, price_cent, orig_price_cent,"
            " pic_url, area, seller_nick, want_count, published_at, free_ship, tags, seller_tags,"
            " keyword, saved_at, updated_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
            " ON CONFLICT (user_id, item_id) DO UPDATE SET title = excluded.title,"
            " price_cent = excluded.price_cent, orig_price_cent = excluded.orig_price_cent,"
            " pic_url = excluded.pic_url, area = excluded.area,"
            " seller_nick = excluded.seller_nick, want_count = excluded.want_count,"
            " published_at = excluded.published_at, free_ship = excluded.free_ship,"
            " tags = excluded.tags, seller_tags = excluded.seller_tags,"
            " keyword = excluded.keyword, updated_at = excluded.updated_at"
            # xmax 是 0 的是这次新插进去的行，更新的不是
            " RETURNING *, (xmax = 0) AS added",
            (
                user_id,
                item.item_id,
                item.title,
                item.price_cent,
                item.orig_price_cent,
                item.pic_url,
                item.area,
                item.seller_nick,
                item.want_count,
                item.published_at,
                item.free_ship,
                json.dumps(item.tags, ensure_ascii=False),
                json.dumps(item.seller_tags, ensure_ascii=False),
                item.keyword,
                now,
                now,
            ),
        ).fetchone()
        assert row is not None
        return StashedItem.from_row(row), bool(row["added"])

    def save_detail(self, user_id: int, item_id: str, item: dict[str, Any]) -> StashedItem:
        """存浏览器插件拿到的商品详情（pc.detail 返回的 itemDO，插件挑过字段）。

        售价、原价、想要换成详情里的（比搜到时新）；详情里没有原价的留着搜到的。
        格式不对抛 ValueError，没暂存过这件抛 StashNotFoundError。
        """
        if str(item.get("itemId", "")) != item_id:
            raise ValueError("详情里的商品 ID 和要存的对不上")
        try:
            detail = ItemDetail.parse({"itemDO": item})
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            raise ValueError(f"商品详情的格式不对：{exc}") from None
        row = self._db.execute(
            "UPDATE search_stash SET description = %s, images = %s, browse_count = %s,"
            " collect_count = %s, status = %s, status_text = %s, detail_at = %s,"
            " want_count = %s, price_cent = COALESCE(%s, price_cent),"
            " orig_price_cent = COALESCE(%s, orig_price_cent)"
            " WHERE user_id = %s AND item_id = %s RETURNING *",
            (
                detail.desc.strip()[:MAX_DESCRIPTION],
                json.dumps(detail_images(detail)),
                detail.browse_count,
                detail.collect_count,
                detail.status if detail.status >= 0 else None,
                _text(detail.status_text, MAX_TAG),
                time.time(),
                detail.want_count,
                detail.price_cent or None,
                detail.orig_price_cent or None,
                user_id,
                item_id,
            ),
        ).fetchone()
        if row is None:
            raise StashNotFoundError(f"暂存里没有商品 {item_id}")
        return StashedItem.from_row(row)

    def remove(self, user_id: int, item_id: str) -> bool:
        """移出暂存。本来就没有的不报错，返回 False。"""
        cursor = self._db.execute(
            "DELETE FROM search_stash WHERE user_id = %s AND item_id = %s", (user_id, item_id)
        )
        return cursor.rowcount > 0


def detail_images(detail: ItemDetail) -> list[str]:
    """详情里的图片地址：补上协议（有的是 //img.alicdn.com/…），去重，不是 http(s) 的丢掉。"""
    urls: list[str] = []
    for url in detail.image_urls:
        url = f"https:{url}" if url.startswith("//") else url
        if url.startswith(("http://", "https://")) and len(url) <= MAX_URL and url not in urls:
            urls.append(url)
    return urls[:MAX_IMAGES]


def _text(value: Any, limit: int = MAX_TEXT) -> str:
    return value.strip()[:limit] if isinstance(value, str) else ""


def _tags(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    tags = [t for v in values if (t := _text(v, MAX_TAG))]
    return tags[:MAX_TAGS]
