"""订单接口（卖家视角）：卖出的订单列表、订单详情、改价。

- 卖出的订单列表在卖家工作台（seller.goofish.com）里，**只对闲鱼专业卖家开放**，个人卖家调用返回
  `PERMISSION_EXCEPTION`（2026-10-29 实测）。工作台的接口要带它自己的 Origin / Referer 和
  `idle_site_biz_code` 请求头，`type` 是 json 不是 originaljson。请求格式参考 Ydisks-Xianyu-Helper
  （Apache-2.0）的 sold_orders.go，返回字段没有实测核对过，所以解析写得宽松，原始条目留在 `raw` 里；
- 订单详情照抄网页端订单详情页，2026-10-29 用一笔真实的已完成订单核对过；
- 改价照抄网页端消息页里的订单卡片，还没有实测。
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from xianyu_core.item import yuan_to_cent
from xianyu_core.mtop import MtopClient

SOLD_LIST_API = "mtop.taobao.idle.trade.merchant.sold.get"
DETAIL_API = "mtop.idle.web.trade.order.detail"
PRICE_RENDER_API = "mtop.taobao.idle.trade.order.modify.price.render"
ADJUST_PRICE_API = "mtop.taobao.idle.trade.user.adjust.price"

PAGE_SIZE = 20

SELLER_SITE = "https://seller.goofish.com"
SELLER_HEADERS = {
    "origin": SELLER_SITE,
    "referer": f"{SELLER_SITE}/?site=COMMONPRO",
    "idle_site_biz_code": "COMMONPRO",
}
SPM_SELLER = "a21107h.42831410.0.0"
SPM_ORDER = "a21ybx.order-detail.0.0"

# 归一后的订单状态。平台只给中文状态文字，按文字归类，认不出的算 OTHER
UNPAID = "unpaid"
TO_SHIP = "to_ship"
SHIPPED = "shipped"
DONE = "done"
CLOSED = "closed"
REFUND = "refund"
OTHER = "other"

STATUS_TEXT = {
    UNPAID: "待付款",
    TO_SHIP: "待发货",
    SHIPPED: "已发货",
    DONE: "交易成功",
    CLOSED: "已关闭",
    REFUND: "退款中",
    OTHER: "其他",
}

# 按顺序匹配，"退款成功"要排在"退款"前面
_STATUS_RULES: list[tuple[tuple[str, ...], str]] = [
    (("待付款", "等待买家付款"), UNPAID),
    (("待发货", "已付款", "等待卖家发货", "等待你发货"), TO_SHIP),
    (("已发货", "待收货", "等待买家确认", "等待买家收货"), SHIPPED),
    (("交易成功", "已完成"), DONE),
    (("退款成功", "交易关闭", "已关闭", "已取消"), CLOSED),
    (("退款",), REFUND),
]

_MONEY = re.compile(r"-?\d+(?:\.\d{1,2})?")
_CHINA = timezone(timedelta(hours=8))


def order_status(text: str, in_refund: bool = False) -> str:
    """平台的状态文字（"待发货"、"交易成功"……）→ 上面的归一状态。"""
    if in_refund:
        return REFUND
    for words, status in _STATUS_RULES:
        if any(word in text for word in words):
            return status
    return OTHER


@dataclass
class SoldOrder:
    """卖出的订单列表里的一条。"""

    order_id: str
    item_id: str
    status: str  # 归一后的状态
    status_text: str  # 平台原文
    title: str
    pic_url: str
    price_cent: int | None  # 订单金额，解析不出来时是 None
    quantity: int
    buyer_id: str
    buyer_nick: str
    created_at: float | None  # 下单时间，秒级时间戳
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def parse(cls, row: dict[str, Any]) -> SoldOrder:
        common = row.get("commonData") or {}
        buyer = row.get("buyerInfoVO") or {}
        price = row.get("priceVO") or {}
        item = row.get("itemVO") or row.get("itemInfoVO") or {}
        status_text = str(common.get("orderStatus") or "")
        return cls(
            order_id=str(common.get("orderId") or ""),
            item_id=str(common.get("itemId") or item.get("itemId") or ""),
            status=order_status(status_text, _bool(common.get("inRefund"))),
            status_text=status_text,
            title=_first([item, common], "title", "itemTitle"),
            pic_url=_first([item, common], "picUrl", "itemPic", "mainPic", "itemMainPictCdnUrl"),
            price_cent=money_cent(_first([price], "totalPrice", "actualFee", "confirmFee")),
            quantity=_int(_first([price, item], "buyNum", "quantity", "buyAmount")) or 1,
            buyer_id=str(buyer.get("buyerId") or ""),
            buyer_nick=_first([buyer], "buyerNick", "nick", "userNick"),
            created_at=parse_time(
                _first([common, row], "orderCreateTime", "createTime", "gmtCreate")
            ),
            raw=row,
        )


@dataclass
class OrderPage:
    orders: list[SoldOrder]
    has_next: bool


@dataclass
class OrderDetail:
    """订单详情页。平台按区块（components）返回，这里只取管理时用得到的。"""

    order_id: str
    item_id: str
    status: str
    status_title: str  # 比如"等待你发货"
    status_desc: str
    steps: list[
        dict[str, Any]
    ]  # [{"title": "已拍下", "done": True, "time": 秒级时间戳或 None}, ...]
    title: str
    pic_url: str
    sku: str
    quantity: int
    amount_title: str  # 比如"成交价"、"实付款"
    amount_cent: int | None
    # 金额明细：[{"title": "商品总价", "cent": 23100}, {"title": "软件服务费", "cent": -139}, ...]
    bills: list[dict[str, Any]]
    income_title: str  # 卖家视角才有，比如"预估到手"
    income_cent: int | None
    fields: list[dict[str, str]]  # 订单信息：[{"title": "订单编号", "value": "..."}, ...]
    receiver_name: str  # 收货地址区块只在没发货的订单里有；发货后地址在 fields 的"收货地址"里
    receiver_phone: str  # 平台开了号码保护时是打码的
    receiver_address: str
    actions: list[str]  # 平台给卖家显示的按钮，比如 ["修改价格", "关闭订单"]
    seller: bool | None  # 当前账号是不是这笔订单的卖家；平台没给时是 None
    buyer_id: str
    buyer_nick: str
    created_at: float | None
    raw: dict[str, Any] = field(repr=False, default_factory=dict)

    @classmethod
    def parse(cls, order_id: str, data: dict[str, Any]) -> OrderDetail:
        blocks = {
            c.get("render"): c.get("data") or {}
            for c in data.get("components") or []
            if isinstance(c, dict)
        }
        state = blocks.get("orderStatusVO") or {}
        info = blocks.get("orderInfoVO") or {}
        address = blocks.get("addressInfoVO") or {}
        status_info = state.get("orderStatusInfo") or {}
        item = info.get("itemInfo") or {}
        price = info.get("priceInfo") or {}
        amount = price.get("amount") or {}
        fields = [
            {"title": str(f.get("title") or ""), "value": str(f.get("value") or "")}
            for f in info.get("orderInfoList") or []
            if isinstance(f, dict)
        ]
        by_title = {f["title"]: f["value"] for f in fields}
        created = (
            (data.get("commonInfo") or {}).get("createTime")
            or (info.get("commonInfo") or {}).get("createTime")
            or by_title.get("下单时间")
        )
        status_title = str(status_info.get("title") or "")
        ut_args = data.get("utArgs") or {}
        seller = data.get("seller")
        seller = _bool(seller) if seller is not None else None
        income = price.get("expectBill") or {}
        bills = [b for b in price.get("billList") or [] if isinstance(b, dict)]
        if isinstance(price.get("softwareBill"), dict):
            bills.append(price["softwareBill"])
        return cls(
            order_id=order_id,
            item_id=str(data.get("itemId") or item.get("itemId") or ""),
            status=order_status(status_title),
            status_title=status_title,
            status_desc=_rich_text(status_info.get("descRichText"))
            or str(ut_args.get("orderStatusName") or ""),
            steps=[
                {
                    "title": str(node.get("title") or ""),
                    "done": _bool(node.get("completed")),
                    "time": parse_time(node.get("time")),
                }
                for node in state.get("orderStatusNodeList") or []
                if isinstance(node, dict)
            ],
            title=str(item.get("title") or ""),
            pic_url=str(item.get("itemMainPictCdnUrl") or ""),
            sku=str(item.get("skuInfo") or ""),
            quantity=_int(item.get("buyAmount")) or 1,
            amount_title=str(amount.get("title") or ""),
            amount_cent=money_cent(amount.get("value")),
            bills=[
                {"title": str(b.get("title") or ""), "cent": money_cent(b.get("value"))}
                for b in bills
            ],
            income_title=str(income.get("title") or ""),
            income_cent=money_cent(income.get("value")),
            fields=fields,
            receiver_name=str(address.get("name") or ""),
            receiver_phone=str(address.get("phoneNumber") or ""),
            receiver_address=str(address.get("address") or ""),
            actions=[
                str(b.get("name"))
                for b in (data.get("bottomBarVO") or {}).get("buttonList") or []
                if isinstance(b, dict) and b.get("name")
            ],
            seller=seller,
            buyer_id=str(data.get("peerUserId") or "") if seller else "",
            buyer_nick=by_title.get("买家昵称", ""),
            created_at=parse_time(created),
            raw=data,
        )


@dataclass
class PriceField:
    """改价弹窗里的一项（商品价、运费……）。`key` 是提交时的字段名。"""

    key: str
    name: str
    price_cent: int | None  # 当前值，平台没给时是 None

    @classmethod
    def parse(cls, row: dict[str, Any]) -> PriceField:
        return cls(
            key=str(row.get("key") or ""),
            name=str(row.get("name") or row.get("key") or ""),
            price_cent=money_cent(row.get("price")),
        )


class OrderApi:
    """一个账号的订单接口。不管限流，调用方自己控制节奏。"""

    def __init__(self, mtop: MtopClient):
        self._mtop = mtop

    async def sold_page(self, page: int = 1, page_size: int = PAGE_SIZE) -> OrderPage:
        """卖出的订单，新的在前。"""
        data = await self._mtop.call(
            SOLD_LIST_API,
            {
                "pageNumber": page,
                "rowsPerPage": page_size,
                "orderIds": "",
                "queryCode": "ALL",
                "orderSearchParam": "{}",
            },
            spm_cnt=SPM_SELLER,
            extra_params={"type": "json", "valueType": "string"},
            headers=SELLER_HEADERS,
        )
        module = data.get("module") or {}
        rows = [row for row in module.get("items") or [] if isinstance(row, dict)]
        orders = [order for order in map(SoldOrder.parse, rows) if order.order_id]
        return OrderPage(orders=orders, has_next=_bool(module.get("nextPage")))

    async def iter_sold(
        self, *, interval: float = 1.0, max_pages: int = 50
    ) -> AsyncIterator[SoldOrder]:
        """翻完所有页。每翻一页停 `interval` 秒。"""
        for page in range(1, max_pages + 1):
            result = await self.sold_page(page)
            for order in result.orders:
                yield order
            if not result.has_next or not result.orders:
                return
            await asyncio.sleep(interval)

    async def detail(self, order_id: str) -> OrderDetail:
        data = await self._mtop.call(
            DETAIL_API,
            {"tid": order_id},
            spm_cnt=SPM_ORDER,
            extra_params={"valueType": "string"},
        )
        return OrderDetail.parse(order_id, data)

    async def price_fields(self, order_id: str) -> list[PriceField]:
        """待付款订单能改的几项价格。网页端点"修改价格"时先调这个，按返回的项画输入框。"""
        data = await self._mtop.call(PRICE_RENDER_API, {"bizOrderId": order_id}, spm_cnt=SPM_ORDER)
        return [
            PriceField.parse(row)
            for row in data.get("modifyPriceRenderList") or []
            if isinstance(row, dict) and row.get("key")
        ]

    async def adjust_price(self, order_id: str, fees: dict[str, int]) -> None:
        """改待付款订单的价格。`fees` 是 {price_fields 里的 key: 新金额（分）}，要带上全部几项。"""
        for key, cents in fees.items():
            if cents < 0:
                raise ValueError(f"{key} 不能是负数")
        payload: dict[str, Any] = {key: str(cents) for key, cents in fees.items()}
        payload["orderId"] = order_id
        await self._mtop.call(ADJUST_PRICE_API, payload, spm_cnt=SPM_ORDER)


def money_cent(value: Any) -> int | None:
    """`"12.50"`、`"¥12.5"`、`12` → 分；空值或认不出时返回 None。"""
    if value is None or isinstance(value, bool):
        return None
    match = _MONEY.search(str(value).replace(",", ""))
    return yuan_to_cent(match.group()) if match else None


def parse_time(value: Any) -> float | None:
    """毫秒或秒级时间戳、`"2026-10-29 12:00:00"`（按北京时间）→ 秒级时间戳。"""
    if value in (None, "") or isinstance(value, bool):
        return None
    text = str(value).strip()
    if re.fullmatch(r"\d+(\.\d+)?", text):
        number = float(text)
        return number / 1000 if number > 1e11 else number
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y/%m/%d %H:%M:%S", "%Y.%m.%d %H:%M"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=_CHINA).timestamp()
        except ValueError:
            continue
    return None


def _first(sources: list[dict[str, Any]], *keys: str) -> str:
    """在几个字典里按顺序找第一个非空的字段。列表接口的字段名还没核对，先多认几个。"""
    for source in sources:
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return str(value)
    return ""


def _rich_text(value: Any) -> str:
    if isinstance(value, list):
        return "".join(str(seg.get("text") or "") for seg in value if isinstance(seg, dict))
    return str(value or "")


def _bool(value: Any) -> bool:
    return value is True or str(value).lower() in ("true", "1")


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
