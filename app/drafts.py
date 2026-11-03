"""发布商品：先在本地存草稿，改好了再发到闲鱼。

草稿的图片存在本机（app/images.py），发布时才传到闲鱼，传过的地址记在草稿里，发布失败重试时不再传。
分类和属性用平台的推荐（xianyu_core/publish.py）：先按描述推荐一次，改了哪一项再推荐一次，
推荐结果原样存在草稿里。发布成功以后草稿留着，记下商品 ID，商品先记进本地商品表。
每次发布（成功失败都算）记进 item_ops，action 是 publish。
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from app.accounts import Accounts
from app.db import Database, Row
from app.images import ImageStore
from app.items import ItemService
from xianyu_core.item import ItemCard
from xianyu_core.publish import (
    CATEGORY,
    MAX_IMAGES,
    NORMAL_CARD,
    POST_MODES,
    PublishApi,
    UploadedImage,
    address_from_poi,
    build_publish_payload,
    check_desc,
    choose,
    submit_payload,
    unsupported_cards,
)

ADDRESS_KEYS = ("area", "city", "divisionId", "gps", "poiId", "poiName", "prov")
TITLE_LENGTH = 30  # 发布后先记进商品表的标题取描述的前多少个字，同步时换成平台截的


class DraftNotFoundError(LookupError):
    pass


@dataclass
class DraftImage:
    sha: str
    width: int
    height: int
    url: str | None = None  # 传到闲鱼以后的地址，还没传是 None


@dataclass
class Draft:
    id: int
    account_id: str
    description: str
    price_cent: int | None
    orig_price_cent: int | None
    post_mode: str
    post_price_cent: int | None
    take_self: bool
    images: list[DraftImage]
    cards: list[dict[str, Any]] = field(repr=False)
    category: dict[str, Any] | None
    address: dict[str, Any] | None
    item_id: str | None
    error: str | None
    created_at: float
    updated_at: float
    published_at: float | None

    @classmethod
    def from_row(cls, row: Row) -> Draft:
        return cls(
            **{
                **row,
                "images": [DraftImage(**img) for img in json.loads(row["images"])],
                "cards": json.loads(row["cards"]),
                "category": json.loads(row["category"]) if row["category"] else None,
                "address": json.loads(row["address"]) if row["address"] else None,
            }
        )

    def properties(self) -> list[dict[str, Any]]:
        """推荐出来的分类和属性，给页面选。

        [{id, name, multiple, options: [{key, name}], selected: [key]}]
        """
        result = []
        for card in self.cards:
            data = card.get("cardData") or {}
            values = data.get("valuesList") or []
            if card.get("cardType") != NORMAL_CARD or not values:
                continue
            is_cat = data.get("propertyId") == CATEGORY
            options = [
                {
                    "key": str(v.get("channelCatId" if is_cat else "valueId")),
                    "name": v.get("catName" if is_cat else "valueName") or "",
                }
                for v in values
            ]
            result.append(
                {
                    "id": data.get("propertyId"),
                    "name": data.get("propertyName") or "",
                    "multiple": data.get("isMultiple") in (True, "true"),
                    "options": options,
                    "selected": [
                        opt["key"]
                        for opt, v in zip(options, values, strict=True)
                        if v.get("isClicked") == "1"
                    ],
                }
            )
        return result

    def unsupported(self) -> list[str]:
        return unsupported_cards(self.cards)


class DraftService:
    def __init__(self, db: Database, accounts: Accounts, items: ItemService, images: ImageStore):
        self._db = db
        self._accounts = accounts
        self._items = items
        self._images = images

    # ---- 本地 ----

    def list(self, account_id: str) -> list[Draft]:
        """还没发布的在前，各自按最近改动排。"""
        rows = self._db.execute(
            "SELECT * FROM drafts WHERE account_id = %s"
            " ORDER BY item_id IS NOT NULL, updated_at DESC",
            (account_id,),
        )
        return [Draft.from_row(r) for r in rows]

    def get(self, account_id: str, draft_id: int) -> Draft:
        row = self._db.execute(
            "SELECT * FROM drafts WHERE account_id = %s AND id = %s", (account_id, draft_id)
        ).fetchone()
        if row is None:
            raise DraftNotFoundError(f"找不到草稿 {draft_id}")
        return Draft.from_row(row)

    def create(self, account_id: str, description: str = "") -> Draft:
        """新建一个空草稿。发货地沿用这个账号最近一个选过发货地的草稿。"""
        last = self._db.execute(
            "SELECT address FROM drafts WHERE account_id = %s AND address IS NOT NULL"
            " ORDER BY updated_at DESC LIMIT 1",
            (account_id,),
        ).fetchone()
        now = time.time()
        row = self._db.execute(
            "INSERT INTO drafts (account_id, description, address, created_at, updated_at)"
            " VALUES (%s, %s, %s, %s, %s) RETURNING *",
            (account_id, description, last["address"] if last else None, now, now),
        ).fetchone()
        return Draft.from_row(row)

    def update(
        self,
        account_id: str,
        draft_id: int,
        *,
        description: str,
        price_cent: int | None,
        orig_price_cent: int | None,
        post_mode: str,
        post_price_cent: int | None,
        take_self: bool,
        image_order: list[str],
        address: dict[str, Any] | None,
    ) -> Draft:
        """保存页面上的表单（整个提交）。`image_order` 是图片的新顺序，图片本身另外加删。"""
        draft = self._editable(account_id, draft_id)
        if post_mode not in POST_MODES:
            raise ValueError(f"不认识的邮费方式：{post_mode}")
        for name, value in (
            ("售价", price_cent),
            ("原价", orig_price_cent),
            ("邮费", post_price_cent),
        ):
            if value is not None and value < 0:
                raise ValueError(f"{name}不能是负数")
        by_sha = {img.sha: img for img in draft.images}
        if sorted(image_order) != sorted(by_sha):
            raise ValueError("图片和草稿里的对不上，刷新页面再试")
        images = [by_sha[sha] for sha in image_order]
        self._db.execute(
            "UPDATE drafts SET description = %s, price_cent = %s, orig_price_cent = %s,"
            " post_mode = %s, post_price_cent = %s, take_self = %s, images = %s, address = %s,"
            " updated_at = %s WHERE id = %s",
            (
                description,
                price_cent,
                orig_price_cent,
                post_mode,
                post_price_cent if post_mode == "fixed" else None,
                take_self,
                _images_json(images),
                json.dumps(_clean_address(address), ensure_ascii=False) if address else None,
                time.time(),
                draft_id,
            ),
        )
        return self.get(account_id, draft_id)

    def add_image(self, account_id: str, draft_id: int, data: bytes) -> Draft:
        draft = self._editable(account_id, draft_id)
        if len(draft.images) >= MAX_IMAGES:
            raise ValueError(f"最多 {MAX_IMAGES} 张图片")
        stored = self._images.save(data)
        if any(img.sha == stored.sha for img in draft.images):
            raise ValueError("这张图片已经加过了")
        images = [*draft.images, DraftImage(stored.sha, stored.width, stored.height)]
        self._save_images(draft_id, images)
        return self.get(account_id, draft_id)

    def remove_image(self, account_id: str, draft_id: int, sha: str) -> Draft:
        draft = self._editable(account_id, draft_id)
        images = [img for img in draft.images if img.sha != sha]
        if len(images) == len(draft.images):
            raise DraftNotFoundError("草稿里没有这张图片")
        self._save_images(draft_id, images)
        self._drop_unused([sha])
        return self.get(account_id, draft_id)

    def image(self, account_id: str, draft_id: int, sha: str) -> bytes:
        draft = self.get(account_id, draft_id)
        if all(img.sha != sha for img in draft.images):
            raise DraftNotFoundError("草稿里没有这张图片")
        return self._images.read(sha)

    def delete(self, account_id: str, draft_id: int) -> None:
        """删掉草稿和只有它用的图片。已经发布的商品不受影响。"""
        draft = self.get(account_id, draft_id)
        self._db.execute("DELETE FROM drafts WHERE id = %s", (draft_id,))
        self._drop_unused([img.sha for img in draft.images])

    # ---- 要请求平台的 ----

    async def recommend(self, account_id: str, draft_id: int) -> Draft:
        """按描述推荐分类和属性。已经推荐过的，带上之前的选择一起推荐（和网页端一样）。"""
        draft = self._editable(account_id, draft_id)
        if not draft.description.strip():
            raise ValueError("先写描述再推荐分类")
        return await self._recommend(draft, draft.cards)

    async def choose(
        self, account_id: str, draft_id: int, property_id: str, keys: list[str]
    ) -> Draft:
        """改一项分类或属性的选择，再推荐一次刷新别的属性（网页端改了选项也是这样）。"""
        draft = self._editable(account_id, draft_id)
        return await self._recommend(draft, choose(draft.cards, property_id, keys))

    async def addresses(self, account_id: str) -> list[dict[str, Any]]:
        """可选的发货地：上次用的排第一，后面是常用地址。"""
        async with self._accounts.mtop(account_id) as mtop:
            selected, common = await PublishApi(mtop).addresses()
        result: list[dict[str, Any]] = []
        for poi in [selected, *common] if selected else common:
            if not poi or any(r["key"] == str(poi.get("poiId")) for r in result):
                continue
            dto = address_from_poi(poi)
            detail = "".join(str(poi.get(k) or "") for k in ("prov", "city", "area", "address"))
            key = str(poi.get("poiId"))
            result.append({"key": key, "name": dto["poiName"], "detail": detail, "address": dto})
        return result

    async def publish(self, account_id: str, draft_id: int) -> Draft:
        """发到闲鱼。先在本地把能查的都查一遍（不请求平台），再查发布限制和违禁词、传图、发布。"""
        draft = self._editable(account_id, draft_id)
        self._payload(draft, [UploadedImage("", img.width, img.height) for img in draft.images])
        detail = {"draft_id": draft.id, "price": draft.price_cent}
        try:
            async with self._accounts.mtop(account_id) as mtop:
                api = PublishApi(mtop)
                await api.check_limit()
                await api.check_words(draft.description, account_id)
                for img in draft.images:
                    if img.url:
                        continue
                    content = self._images.read(img.sha)
                    img.url = (await api.upload(content, f"{img.sha[:16]}.jpg")).url
                    # 传一张记一张，发布失败重试时不用再传
                    self._save_images(draft.id, draft.images)
                uploaded = [UploadedImage(i.url or "", i.width, i.height) for i in draft.images]
                payload = self._payload(draft, uploaded)
                payload["userRightsProtocols"] = await api.services(payload, draft.cards)
                item_id = await api.publish(submit_payload(payload))
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            self._items.log_op(account_id, "", "publish", detail, error=message)
            self._db.execute("UPDATE drafts SET error = %s WHERE id = %s", (message, draft.id))
            raise
        self._items.log_op(account_id, item_id, "publish", detail, error=None)
        now = time.time()
        self._db.execute(
            "UPDATE drafts SET item_id = %s, error = NULL, published_at = %s, updated_at = %s"
            " WHERE id = %s",
            (item_id, now, now, draft.id),
        )
        self._items.add_published(
            account_id,
            ItemCard(
                item_id=item_id,
                title=_title(draft.description),
                price_cent=draft.price_cent or 0,
                status=0,
                category_id=_int_or_none((draft.category or {}).get("catId")),
                pic_url=draft.images[0].url or "",
                post_info="",
                want_count=None,
            ),
        )
        return self.get(account_id, draft.id)

    # ---- 内部 ----

    def _editable(self, account_id: str, draft_id: int) -> Draft:
        draft = self.get(account_id, draft_id)
        if draft.item_id:
            raise ValueError("这个草稿已经发布了，不能再改")
        return draft

    def _payload(self, draft: Draft, images: list[UploadedImage]) -> dict[str, Any]:
        check_desc(draft.description)
        if draft.price_cent is None:
            raise ValueError("还没填售价")
        if not draft.address:
            raise ValueError("还没选发货地")
        return build_publish_payload(
            desc=draft.description,
            images=images,
            cards=draft.cards,
            category=draft.category or {},
            price_cent=draft.price_cent,
            orig_price_cent=draft.orig_price_cent,
            post_mode=draft.post_mode,
            post_price_cent=draft.post_price_cent,
            take_self=draft.take_self,
            address=draft.address,
        )

    async def _recommend(self, draft: Draft, cards: list[dict[str, Any]]) -> Draft:
        async with self._accounts.mtop(draft.account_id) as mtop:
            result = await PublishApi(mtop).recommend(draft.description, cards)
        self._db.execute(
            "UPDATE drafts SET cards = %s, category = %s, updated_at = %s WHERE id = %s",
            (
                json.dumps(result.cards, ensure_ascii=False),
                json.dumps(result.category, ensure_ascii=False),
                time.time(),
                draft.id,
            ),
        )
        return self.get(draft.account_id, draft.id)

    def _save_images(self, draft_id: int, images: list[DraftImage]) -> None:
        self._db.execute(
            "UPDATE drafts SET images = %s, updated_at = %s WHERE id = %s",
            (_images_json(images), time.time(), draft_id),
        )

    def _drop_unused(self, shas: list[str]) -> None:
        """别的草稿都没用到的图片文件删掉。"""
        for sha in shas:
            used = self._db.execute(
                "SELECT 1 FROM drafts WHERE images LIKE %s LIMIT 1", (f'%"{sha}"%',)
            ).fetchone()
            if not used:
                self._images.delete(sha)


def _images_json(images: list[DraftImage]) -> str:
    return json.dumps([asdict(img) for img in images])


def _clean_address(address: dict[str, Any]) -> dict[str, Any]:
    """页面传回来的发货地只留 itemAddrDTO 的几项。"""
    missing = [key for key in ("city", "divisionId", "gps", "poiId") if not address.get(key)]
    if missing:
        raise ValueError("发货地不完整，重新选一个")
    return {key: address.get(key) for key in ADDRESS_KEYS}


def _title(desc: str) -> str:
    first = desc.strip().splitlines()[0] if desc.strip() else ""
    return first[:TITLE_LENGTH]


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
