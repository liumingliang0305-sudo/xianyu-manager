"""发货地搜索：用自己的高德"Web 服务" key 调高德的关键字搜索。

闲鱼网页端发布页的"宝贝所在地"是在浏览器里加载高德地图、用闲鱼自己的 key 搜的，
闲鱼没有搜地址的接口。这里换成自己的 key，在后台调高德的 REST 接口，key 不发给浏览器。
搜出来的是高德的地点，和网页端选中后存的一样：poiId 是高德的地点 ID，divisionId 是行政区编码，
坐标是高德用的坐标系（和闲鱼一致）。

key 取环境变量 AMAP_KEY（服务器上写在 /etc/xym.env），没有就读 data/amap.key
（本机开发用，文件里一行就是 key）。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

KEY_PATH = Path("data/amap.key")
SEARCH_URL = "https://restapi.amap.com/v3/place/text"
PAGE_SIZE = 10


class PlacesUnavailableError(RuntimeError):
    """没配高德 key，或者高德那边出错（key 不对、额度用完）。"""


def load_key(path: Path = KEY_PATH) -> str | None:
    key = os.environ.get("AMAP_KEY", "").strip()
    if key:
        return key
    try:
        return path.read_text(encoding="utf-8").strip() or None
    except FileNotFoundError:
        return None


class PlaceSearch:
    def __init__(self, key: str | None, *, transport: httpx.AsyncBaseTransport | None = None):
        self._key = key
        self._transport = transport  # 测试注入 httpx.MockTransport

    @property
    def enabled(self) -> bool:
        return bool(self._key)

    async def search(self, keyword: str, city: str = "") -> list[dict[str, Any]]:
        """按名字搜地点，最多 10 个。

        返回的格式和闲鱼 poi.get 里的地址一样，可以直接转成 itemAddrDTO。
        """
        keyword = keyword.strip()
        if not keyword:
            return []
        if not self._key:
            raise PlacesUnavailableError("后台没配高德 key，只能从常用地址里选")
        params = {
            "key": self._key,
            "keywords": keyword,
            "city": city,
            "offset": str(PAGE_SIZE),
            "page": "1",
            # base 不给行政区编码（adcode），发布要用它当 divisionId
            "extensions": "all",
        }
        async with httpx.AsyncClient(timeout=10.0, transport=self._transport) as client:
            try:
                resp = await client.get(SEARCH_URL, params=params)
                body = resp.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise PlacesUnavailableError(f"高德搜索没响应：{exc}") from exc
        if body.get("status") != "1":
            raise PlacesUnavailableError(f"高德搜索出错：{body.get('info') or '没说原因'}")
        return [p for p in (_poi(raw) for raw in body.get("pois") or []) if p]


def _poi(raw: dict[str, Any]) -> dict[str, Any] | None:
    """高德的一个地点 → 闲鱼 poi.get 的地址格式。没有坐标、行政区的（比如只是一个城市名）不要。"""
    lng, _, lat = _text(raw.get("location")).partition(",")
    adcode = _text(raw.get("adcode"))
    if not (lng and lat and adcode.isdigit() and _text(raw.get("id"))):
        return None
    return {
        "poi": _text(raw.get("name")),
        "poiId": _text(raw.get("id")),
        "address": _text(raw.get("address")),
        "prov": _text(raw.get("pname")),
        "city": _text(raw.get("cityname")),
        "area": _text(raw.get("adname")),
        "divisionId": int(adcode),
        "longitude": lng,
        "latitude": lat,
    }


def _text(value: Any) -> str:
    """高德的字段没有值时给的是空数组，不是空串。"""
    return value if isinstance(value, str) else ""
