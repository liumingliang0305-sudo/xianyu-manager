"""商品搜索：网页端搜索页（goofish.com/search）同一个接口。

接口名和请求体照抄网页端（xy-site 0.0.179 的 p_search-index.js）：每页 30 条，
网页端最多翻到第 50 页。排序和筛选也是搜索页上那几样：
综合 / 最近活跃 / 信用 / 新降价 / 最新发布 / 价格高低，发布天数，价格区间，
个人闲置、包邮、全新、验货宝这几个勾选项。
网页端排序和发布天数只能选一个，这里两个可以一起用，接口按字段认。
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

from xianyu_core.constants import WEB_HOST
from xianyu_core.item import cent_to_yuan, yuan_to_cent
from xianyu_core.mtop import MtopClient

SEARCH_API = "mtop.taobao.idlemtopsearch.pc.search"
SPM_SEARCH = "a21ybx.search.0.0"

ROWS_PER_PAGE = 30
MAX_PAGES = 50

# 排序 → (sortField, sortValue)。不在这里的就是"综合"
SORTS: dict[str, tuple[str, str]] = {
    "active": ("modify", "desc"),
    "credit": ("credit", "credit_desc"),
    "reduce": ("reduce", "desc"),
    "new": ("create", "desc"),
    "price_asc": ("price", "asc"),
    "price_desc": ("price", "desc"),
}
SORT_TEXT = {
    "": "综合",
    "active": "最近活跃",
    "credit": "信用排序",
    "reduce": "新降价",
    "new": "最新发布",
    "price_asc": "价格从低到高",
    "price_desc": "价格从高到低",
}
# 勾选项 → (平台的值, 网页上的文字)
QUICK_FILTERS: dict[str, tuple[str, str]] = {
    "personal": ("filterPersonal", "个人闲置"),
    "free_ship": ("filterFreePostage", "包邮"),
    "brand_new": ("filterNew", "全新"),
    "appraise": ("filterAppraise", "验货宝"),
}
PUBLISH_DAYS = (1, 3, 7, 14)

_WANT = re.compile(r"(\d+)\s*人想要")


@dataclass(frozen=True)
class SearchQuery:
    """一次搜索的条件。价格单位是分，0 或 None 表示不限。"""

    keyword: str
    sort: str = ""
    publish_days: int | None = None
    price_min_cent: int | None = None
    price_max_cent: int | None = None
    filters: tuple[str, ...] = ()  # QUICK_FILTERS 的键

    def check(self) -> None:
        """条件不对时抛 ValueError。"""
        if not self.keyword.strip():
            raise ValueError("搜索词不能为空")
        if self.sort and self.sort not in SORTS:
            raise ValueError(f"不认识的排序：{self.sort}")
        if self.publish_days is not None and self.publish_days not in PUBLISH_DAYS:
            raise ValueError(f"发布天数只能是 {'、'.join(map(str, PUBLISH_DAYS))}")
        unknown = [f for f in self.filters if f not in QUICK_FILTERS]
        if unknown:
            raise ValueError(f"不认识的筛选：{'、'.join(unknown)}")
        low, high = self.price_min_cent, self.price_max_cent
        if (low is not None and low < 0) or (high is not None and high < 0):
            raise ValueError("价格不能是负数")
        if low and high and low > high:
            raise ValueError("最低价不能高于最高价")


def build_payload(query: SearchQuery, page: int = 1) -> dict[str, Any]:
    """搜索接口的请求体。键的顺序、空值的写法和网页端一致。"""
    search_filter = ""
    if query.publish_days:
        search_filter += f"publishDays:{query.publish_days};"
    if query.price_min_cent or query.price_max_cent:
        # 网页端没填的一边，最低价是空串，最高价是 JS 的 undefined
        low = cent_to_yuan(query.price_min_cent) if query.price_min_cent else ""
        high = cent_to_yuan(query.price_max_cent) if query.price_max_cent else "undefined"
        search_filter += f"priceRange:{low},{high};"
    if query.filters:
        values = [QUICK_FILTERS[f][0] for f in query.filters]
        search_filter += f"quickFilter:{','.join(values)};"
    sort_field, sort_value = SORTS.get(query.sort, ("", ""))
    from_filter = bool(sort_field or search_filter)
    return {
        "pageNumber": page,
        "keyword": query.keyword.strip(),
        "fromFilter": from_filter,
        "rowsPerPage": ROWS_PER_PAGE,
        "sortValue": sort_value,
        "sortField": sort_field,
        "customDistance": "",
        "gps": "",
        "propValueStr": {"searchFilter": search_filter} if from_filter else {},
        "customGps": "",
        "searchReqFromPage": "pcSearch",
        "extraFilterValue": "{}",
        "userPositionJson": "{}",
    }


@dataclass
class SearchCard:
    """搜索结果里的一条商品（别人的）。"""

    item_id: str
    title: str
    price_cent: int
    orig_price_cent: int | None  # 卖家填的原价，没填是 None
    pic_url: str
    area: str  # 发货地，一般是省或市
    seller_nick: str
    want_count: int | None  # 从"7人想要"标签解析，没有标签时是 None
    published_at: float | None  # 秒级时间戳
    free_ship: bool
    tags: list[str]  # 成色、品牌、属性、降价这些标签
    seller_tags: list[str]  # "卖家信用极好"、"回复超快"这些
    url: str
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def parse(cls, row: dict[str, Any]) -> SearchCard | None:
        """一行结果。不是商品（结构不认识）时返回 None。"""
        main = ((row.get("data") or {}).get("item") or {}).get("main") or {}
        ex = main.get("exContent") or {}
        item_id = str(ex.get("itemId") or "")
        if not item_id:
            return None
        args = (main.get("clickParam") or {}).get("args") or {}
        groups = {
            key: [_tag_text(t) for t in (group or {}).get("tagList") or []]
            for key, group in (ex.get("fishTags") or {}).items()
        }
        texts = [t for key in ("r2", "r3") for t in groups.get(key, []) if t]
        published = _int_or_none(args.get("publishTime"))
        return cls(
            item_id=item_id,
            title=ex.get("title") or "",
            price_cent=_price_cent(args.get("price"), ex.get("price")),
            orig_price_cent=_yuan_text_cent(ex.get("oriPrice")),
            pic_url=ex.get("picUrl") or "",
            area=ex.get("area") or "",
            seller_nick=ex.get("userNickName") or "",
            want_count=next((int(m.group(1)) for t in texts if (m := _WANT.search(t))), None),
            published_at=published / 1000 if published else None,
            free_ship="freeship" in str(args.get("tag") or "").split("/"),
            tags=[t for t in texts if not _WANT.search(t)],
            seller_tags=[t for t in groups.get("r4", []) if t],
            url=f"{WEB_HOST}/item?id={item_id}",
            raw=row,
        )


@dataclass
class SearchPage:
    items: list[SearchCard]
    has_next: bool
    total: int | None  # 平台说一共找到多少条（不一定都翻得到）


class SearchApi:
    """搜索接口。不管限流，调用方自己控制节奏。

    async with MtopClient(credential) as mtop:
        page = await SearchApi(mtop).search(SearchQuery("机械键盘"))
    """

    def __init__(self, mtop: MtopClient):
        self._mtop = mtop

    async def search(self, query: SearchQuery, page: int = 1) -> SearchPage:
        query.check()
        if not 1 <= page <= MAX_PAGES:
            raise ValueError(f"页码要在 1 到 {MAX_PAGES} 之间")
        data = await self._mtop.call(SEARCH_API, build_payload(query, page), spm_cnt=SPM_SEARCH)
        info = data.get("resultInfo") or {}
        control = info.get("searchResControlFields") or {}
        cards = [c for row in data.get("resultList") or [] if (c := SearchCard.parse(row))]
        has_next = bool(info.get("hasNextPage", control.get("nextPage")))
        return SearchPage(
            items=cards,
            has_next=has_next and page < MAX_PAGES,
            total=_int_or_none(control.get("numFound")),
        )


def _tag_text(tag: dict[str, Any]) -> str:
    """文字标签的内容。图标标签（包邮图标之类）的 content 是图标名，不要。"""
    data = tag.get("data") or {}
    return "" if data.get("type") == "img" else str(data.get("content") or "").strip()


def _price_cent(price: Any, parts: Any) -> int:
    """售价：点击参数里的 price（"49.90"）最准；没有时拼卡片上显示的几段（¥ 1 .2 万）。"""
    if price not in (None, ""):
        try:
            return yuan_to_cent(price)
        except ValueError:
            pass
    if isinstance(parts, list):
        text = "".join(str(p.get("text", "")) for p in parts if isinstance(p, dict))
        return _yuan_text_cent(text) or 0
    return 0


def _yuan_text_cent(text: Any) -> int | None:
    """`"¥199"`、`"1.2万"` → 分。认不出来时是 None。"""
    value = str(text or "").strip().lstrip("¥￥").strip()
    scale = 1
    if value.endswith("万"):
        value, scale = value[:-1], 10000
    try:
        cents = Decimal(value) * scale * 100
    except InvalidOperation:
        return None
    return int(cents) if cents > 0 else None


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
