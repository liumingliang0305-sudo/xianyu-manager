"""发布商品：先在本地存草稿，改好了再发到闲鱼。编辑已有的商品也是一个草稿，表单一样。

草稿的图片存在本机（app/images.py），发布时才传到闲鱼，传过的地址记在草稿里，发布失败重试时不再传。
分类和属性用平台的推荐（xianyu_core/publish.py）：先按描述推荐一次，改了哪一项再推荐一次，
推荐结果原样存在草稿里。发布成功以后草稿留着，记下商品 ID，商品先记进本地商品表。
每次发布（成功失败都算）记进 item_ops，action 是 publish。

编辑已有商品（`edit_item`）：拿一次编辑页的表单初值（editDetail，卖家自己的接口）存成草稿，
原样记在 `origin` 里。闲鱼上已有的图片不下载，草稿里只记地址。改好了提交（edit 接口），请求体照
origin 改，表单里没有的（数量、服务……）原样带回。提交记进 item_ops，action 是 edit。
编辑草稿不在发布页的列表里。

重新上架（`relist_item`）：网页端没有让下架、卖掉的商品回到在售的接口，这里是照原商品再发一个新商品。
建草稿和编辑一样拿一次 editDetail 存进 origin，草稿在发布页的列表里，改好了发布。
请求体照 origin 拼，和编辑的一样（原来的图不重传，属性、服务、App 里设的运费模板原样带上），
去掉 itemId、数量改成 1 件，调发布接口。发出去的商品 ID 是新的，原来的商品不动。
记进 item_ops 的 action 是 relist。
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from typing import Any

from app.accounts import Accounts
from app.db import Database, Row
from app.images import ImageStore
from app.items import ONLINE, ItemService
from xianyu_core.item import (
    ItemApi,
    ItemCard,
    build_edit_payload,
    image_from_detail,
    post_fee_from_detail,
)
from xianyu_core.publish import (
    CATEGORY,
    KEEP_POST,
    MAX_IMAGES,
    NORMAL_CARD,
    POST_MODES,
    PublishApi,
    UploadedImage,
    address_from_poi,
    build_publish_payload,
    check_desc,
    check_listing,
    choose,
    image_info,
    labels_from_cards,
    post_fee,
    post_mode_from,
    submit_payload,
    unsupported_cards,
)

ADDRESS_KEYS = ("area", "city", "divisionId", "gps", "poiId", "poiName", "prov")
TITLE_LENGTH = 30  # 发布后先记进商品表的标题取描述的前多少个字，同步时换成平台截的
# 编辑提交时比较的几块请求体 → 改动记录里的名字
EDIT_FIELDS = {
    "itemTextDTO": "描述",
    "imageInfoDOList": "图片",
    "itemPriceDTO": "价格",
    "itemCatDTO": "分类",
    "itemLabelExtList": "属性",
    "itemPostFeeDTO": "邮费",
    "itemAddrDTO": "发货地",
}


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
    edit_item_id: str | None  # 编辑已有商品的草稿：改的是哪个商品
    origin: dict[str, Any] | None = field(repr=False)  # 建草稿时的 editDetail
    relist_item_id: str | None  # 重新上架的草稿：照哪个商品抄的

    @classmethod
    def from_row(cls, row: Row) -> Draft:
        return cls(
            **{
                **row,
                "images": [DraftImage(**img) for img in json.loads(row["images"])],
                "cards": json.loads(row["cards"]),
                "category": json.loads(row["category"]) if row["category"] else None,
                "address": json.loads(row["address"]) if row["address"] else None,
                "origin": json.loads(row["origin"]) if row["origin"] else None,
            }
        )

    def current_labels(self) -> list[dict[str, str]]:
        """编辑（重新上架）的商品、还没重新推荐时，商品现在的属性（不含分类）：[{name, value}]。"""
        if self.cards or not self.origin:
            return []
        result = []
        for label in self.origin.get("itemLabelExtList") or []:
            # "属性ID##属性名:值ID##值名"
            prop, _, value = str(label.get("properties") or "").partition(":")
            prop_id, _, prop_name = prop.partition("##")
            if prop_id != CATEGORY and prop_name:
                result.append(
                    {"name": prop_name, "value": label.get("text") or value.partition("##")[2]}
                )
        return result

    def can_keep_post(self) -> bool:
        """编辑、重新上架的商品原来的邮费对不上四种选法（比如 App 里设的运费模板），可以沿用。"""
        post = (self.origin or {}).get("itemPostFeeDTO") or {}
        return bool(self.origin) and post_mode_from(post)[0] == KEEP_POST

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
        """发新商品的草稿，还没发布的在前，各自按最近改动排。编辑已有商品的不在里面。"""
        rows = self._db.execute(
            "SELECT * FROM drafts WHERE account_id = %s AND edit_item_id IS NULL"
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
            " AND edit_item_id IS NULL ORDER BY updated_at DESC LIMIT 1",
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
        if post_mode not in POST_MODES and not (post_mode == KEEP_POST and draft.can_keep_post()):
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
        try:
            return self._images.read(sha)
        except FileNotFoundError:
            # 编辑的商品在闲鱼上原有的图，只记了地址
            raise DraftNotFoundError("这张图在闲鱼上，本机没有") from None

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
            if poi and all(r["key"] != str(poi.get("poiId")) for r in result):
                result.append(address_option(poi))
        return result

    async def edit_item(self, account_id: str, item_id: str) -> Draft:
        """编辑已经在闲鱼上的商品：拿一次编辑页的表单初值（editDetail）存成草稿。

        这个商品有还没提交的编辑草稿就接着用，不请求平台；以前提交过的删掉。
        """
        row = self._db.execute(
            "SELECT * FROM drafts WHERE account_id = %s AND edit_item_id = %s AND item_id IS NULL"
            " ORDER BY id DESC LIMIT 1",
            (account_id, item_id),
        ).fetchone()
        if row:
            return Draft.from_row(row)
        async with self._accounts.mtop(account_id) as mtop:
            detail = await ItemApi(mtop).edit_detail(item_id)
        if detail.get("itemSkuList"):
            raise ValueError("多规格商品网页端不支持编辑，请在闲鱼 App 里改")

        for old in self._db.execute(
            "SELECT id FROM drafts WHERE account_id = %s AND edit_item_id = %s",
            (account_id, item_id),
        ).fetchall():
            self.delete(account_id, old["id"])
        return self._insert_from_detail(account_id, detail, edit_item_id=item_id)

    async def relist_item(self, account_id: str, item_id: str) -> Draft:
        """重新上架一个下架或卖掉的商品：照它在闲鱼上的样子（editDetail）抄一个发新商品的草稿。

        发布出去是一个新商品（新的商品 ID，想要、浏览从零算），原来的商品不动。
        这个商品有还没发的重新上架草稿就接着用，不请求平台；发过的留着，再上架就再抄一个。
        """
        record = self._items.get(account_id, item_id)
        if record and record.on_platform and record.status == ONLINE:
            raise ValueError("这个商品还在卖，不用重新上架（App 里下架了的话先同步一次）")
        row = self._db.execute(
            "SELECT * FROM drafts WHERE account_id = %s AND relist_item_id = %s AND item_id IS NULL"
            " ORDER BY id DESC LIMIT 1",
            (account_id, item_id),
        ).fetchone()
        if row:
            return Draft.from_row(row)
        async with self._accounts.mtop(account_id) as mtop:
            detail = await ItemApi(mtop).edit_detail(item_id)
        if detail.get("itemSkuList"):
            raise ValueError("多规格商品网页端发不了，请在闲鱼 App 里重新上架")
        return self._insert_from_detail(account_id, detail, relist_item_id=item_id)

    def _insert_from_detail(
        self,
        account_id: str,
        detail: dict[str, Any],
        *,
        edit_item_id: str | None = None,
        relist_item_id: str | None = None,
    ) -> Draft:
        """editDetail → 一个新草稿，原样记在 origin 里。闲鱼上的图不下载，只记地址。"""
        price = detail.get("itemPriceDTO") or {}
        post = detail.get("itemPostFeeDTO") or {}
        post_mode, post_price = post_mode_from(post)
        images = [
            DraftImage(
                sha=hashlib.sha256(img["url"].encode()).hexdigest(),
                width=_int_or_none(img.get("widthSize")) or 0,
                height=_int_or_none(img.get("heightSize")) or 0,
                url=img["url"],
            )
            for img in detail.get("imageInfoDOList") or []
            if img.get("url")
        ]
        category = detail.get("itemCatDTO")
        address = detail.get("itemAddrDTO")
        now = time.time()
        row = self._db.execute(
            "INSERT INTO drafts (account_id, edit_item_id, relist_item_id, origin, description,"
            " price_cent, orig_price_cent, post_mode, post_price_cent, take_self, images, category,"
            " address, created_at, updated_at)"
            " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) RETURNING *",
            (
                account_id,
                edit_item_id,
                relist_item_id,
                json.dumps(detail, ensure_ascii=False),
                (detail.get("itemTextDTO") or {}).get("desc") or "",
                _int_or_none(price.get("priceInCent")) or None,
                _int_or_none(price.get("origPriceInCent")) or None,
                post_mode,
                post_price,
                post.get("onlyTakeSelf") in (True, "true"),
                _images_json(images),
                json.dumps(category, ensure_ascii=False) if category else None,
                json.dumps({key: address.get(key) for key in ADDRESS_KEYS}, ensure_ascii=False)
                if address
                else None,
                now,
                now,
            ),
        ).fetchone()
        return Draft.from_row(row)

    async def publish(self, account_id: str, draft_id: int, *, force: bool = False) -> Draft:
        """发到闲鱼。先在本地把能查的都查一遍（不请求平台），再查发布限制和违禁词、传图、发布。

        编辑已有商品的草稿是把改动提交上去（`_submit_edit`），`force` 是改价超过保护线也提交。
        重新上架的草稿也是发新商品，只是请求体照原商品拼（见 `_publish_payload`）。
        """
        draft = self._editable(account_id, draft_id)
        if draft.edit_item_id:
            return await self._submit_edit(draft, force=force)
        # 本地检查，图还没传，地址先空着
        if draft.relist_item_id:
            self._edit_payload(draft)
        else:
            self._payload(draft, [UploadedImage("", img.width, img.height) for img in draft.images])
        action = "relist" if draft.relist_item_id else "publish"
        detail: dict[str, Any] = {"draft_id": draft.id, "price": draft.price_cent}
        if draft.relist_item_id:
            detail["from_item"] = draft.relist_item_id
        try:
            async with self._accounts.mtop(account_id) as mtop:
                api = PublishApi(mtop)
                await api.check_limit()
                await api.check_words(draft.description, account_id)
                await self._upload(api, draft)
                item_id = await api.publish(await self._publish_payload(api, draft))
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            self._items.log_op(account_id, "", action, detail, error=message)
            self._db.execute("UPDATE drafts SET error = %s WHERE id = %s", (message, draft.id))
            raise
        self._items.log_op(account_id, item_id, action, detail, error=None)
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
            done = "这些改动已经提交到闲鱼了" if draft.edit_item_id else "这个草稿已经发布了"
            raise ValueError(f"{done}，不能再改")
        return draft

    async def _upload(self, api: PublishApi, draft: Draft) -> None:
        """把还没传过的图传到闲鱼。传一张记一张，失败重试时不用再传。"""
        for img in draft.images:
            if img.url:
                continue
            content = self._images.read(img.sha)
            img.url = (await api.upload(content, f"{img.sha[:16]}.jpg")).url
            self._save_images(draft.id, draft.images)

    async def _submit_edit(self, draft: Draft, *, force: bool) -> Draft:
        """把编辑草稿提交到闲鱼。

        先在本地查一遍、看改了哪几块，什么都没改就不提交。描述改了才查违禁词，分类换了才重新查服务。
        """
        account_id, item_id = draft.account_id, draft.edit_item_id or ""
        origin = draft.origin or {}
        changed = _changed(origin, self._edit_payload(draft))
        if not changed:
            raise ValueError("和闲鱼上的一样，没有要提交的改动")
        old_price = _int_or_none((origin.get("itemPriceDTO") or {}).get("priceInCent")) or 0
        detail: dict[str, Any] = {"draft_id": draft.id, "fields": changed}
        if "价格" in changed:
            if not force:
                self._items.check_price(old_price, draft.price_cent or 0)
            detail.update({"from": old_price, "to": draft.price_cent})
        try:
            async with self._accounts.mtop(account_id) as mtop:
                api = PublishApi(mtop)
                if "描述" in changed:
                    await api.check_words(draft.description, account_id)
                await self._upload(api, draft)
                payload = self._edit_payload(draft)
                if "分类" in changed:
                    payload["userRightsProtocols"] = await api.services(payload, draft.cards)
                await ItemApi(mtop).submit(payload)
        except Exception as exc:
            message = str(exc) or type(exc).__name__
            self._items.log_op(account_id, item_id, "edit", detail, error=message)
            self._db.execute("UPDATE drafts SET error = %s WHERE id = %s", (message, draft.id))
            raise
        self._items.log_op(account_id, item_id, "edit", detail, error=None)
        now = time.time()
        self._db.execute(
            "UPDATE drafts SET item_id = %s, error = NULL, published_at = %s, updated_at = %s"
            " WHERE id = %s",
            (item_id, now, now, draft.id),
        )
        self._items.update_listing(
            account_id,
            item_id,
            title=_title(draft.description),
            price_cent=draft.price_cent or 0,
            pic_url=draft.images[0].url or "",
        )
        return self.get(account_id, draft.id)

    async def _publish_payload(self, api: PublishApi, draft: Draft) -> dict[str, Any]:
        """图传完以后的发布请求体，和网页端发布页的一样。

        重新上架的照原商品的 editDetail 拼，和编辑的一样，去掉 itemId 就是发新商品（网页端的发布
        和编辑请求体就差这一项）；数量和发布页一样是 1 件，原来的可能是卖完的 0 件。
        换了分类才重新查服务。
        """
        if not draft.relist_item_id:
            uploaded = [UploadedImage(img.url or "", img.width, img.height) for img in draft.images]
            payload = self._payload(draft, uploaded)
            payload["userRightsProtocols"] = await api.services(payload, draft.cards)
            return submit_payload(payload)
        payload = self._edit_payload(draft)
        del payload["itemId"]
        if "分类" in _changed(draft.origin or {}, payload):
            payload["userRightsProtocols"] = await api.services(payload, draft.cards)
        return {**payload, "quantity": "1", "defaultPrice": False}

    def _edit_payload(self, draft: Draft) -> dict[str, Any]:
        """编辑（重新上架）草稿 → edit 的请求体。还没传的图地址是空的，只用来检查和比较。"""
        if draft.price_cent is None:
            raise ValueError("还没填售价")
        if not draft.address:
            raise ValueError("还没选发货地")
        check_listing(
            desc=draft.description,
            image_count=len(draft.images),
            price_cent=draft.price_cent,
            orig_price_cent=draft.orig_price_cent,
            category=draft.category or {},
            cards=draft.cards,
        )
        origin = draft.origin or {}
        # 闲鱼上原有的图照 editDetail 带回去，新加的按发布页的写法
        known = {img.get("url"): img for img in origin.get("imageInfoDOList") or []}
        images = [
            {**image_from_detail(known[img.url]), "major": index == 0}
            if img.url in known
            else image_info(UploadedImage(img.url or "", img.width, img.height), index == 0)
            for index, img in enumerate(draft.images)
        ]
        # 邮费没换就照 editDetail 带回去（网页端编辑页也是这样，包邮时也带着 templateId）
        same_post = post_mode_from(origin.get("itemPostFeeDTO") or {})
        if draft.post_mode == KEEP_POST or (draft.post_mode, draft.post_price_cent) == same_post:
            post = {**post_fee_from_detail(origin), "onlyTakeSelf": draft.take_self}
        else:
            post = post_fee(draft.post_mode, draft.post_price_cent, draft.take_self)
        return build_edit_payload(
            origin,
            price_cent=draft.price_cent,
            orig_price_cent=draft.orig_price_cent or 0,
            desc=draft.description,
            images=images,
            labels=labels_from_cards(draft.cards) if draft.cards else None,
            category=draft.category,
            post=post,
            address=draft.address,
        )

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
        # 编辑的商品第一次推荐时还没有卡片，带上它现在的标签和类目
        labels = None
        if draft.origin and not cards:
            labels = draft.origin.get("itemLabelExtList") or []
        async with self._accounts.mtop(draft.account_id) as mtop:
            result = await PublishApi(mtop).recommend(
                draft.description,
                cards,
                labels=labels,
                category=draft.category if labels is not None else None,
            )
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


def address_option(poi: dict[str, Any]) -> dict[str, Any]:
    """闲鱼 poi.get 格式的一个地址（高德搜出来的也转成这个格式）→ 页面上的一个选项。

    `address` 是 itemAddrDTO，页面选中后原样放进草稿。
    """
    dto = address_from_poi(poi)
    parts = [str(poi.get(key) or "") for key in ("prov", "city", "area", "address")]
    if parts[0] == parts[1]:  # 直辖市：北京市北京市东城区
        del parts[1]
    key = str(poi.get("poiId"))
    return {"key": key, "name": dto["poiName"], "detail": "".join(parts), "address": dto}


def _changed(origin: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    """编辑的请求体和闲鱼上原来的比，改了哪几块（EDIT_FIELDS 里的名字）。"""
    base = build_edit_payload(origin)
    return [
        name
        for key, name in EDIT_FIELDS.items()
        if _comparable(key, payload.get(key)) != _comparable(key, base.get(key))
    ]


def _comparable(key: str, value: Any) -> Any:
    """比较时不管数字是数还是字符串；标签只比选了什么（重新推荐以后字段会多几个）。"""
    if key == "itemLabelExtList":
        return sorted(str(label.get("properties")) for label in value or [])
    return json.loads(json.dumps(value), parse_int=str)


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
