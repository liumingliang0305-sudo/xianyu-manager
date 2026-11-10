"""测试共用的假平台，和管理后台的测试用户。"""

import asyncio
import io
import json
import time
from urllib.parse import parse_qs

import httpx
from PIL import Image

from app import db
from app.users import Users

UNB = "42"
PASSWORD = "test-password"


def add_user(database_url, username="tester", accounts=()):
    """建管理后台的用户（已经有就直接用），把 `accounts` 里的闲鱼账号归给这个用户。返回用户 ID。"""
    conn = db.connect(database_url)
    try:
        users = Users(conn)
        user = users.find(username) or asyncio.run(users.create(username, PASSWORD))
        for account_id in accounts:
            users.assign(account_id, user.id)
        return user.id
    finally:
        conn.close()


def sign_in(client, username="tester", password=PASSWORD):
    """登录，之后这个 client 的请求都带上访问令牌。返回访问令牌。"""
    resp = client.post("/api/auth/login", json={"username": username, "password": password})
    assert resp.status_code == 200, resp.text
    token = resp.json()["access_token"]
    client.headers["authorization"] = f"Bearer {token}"
    return token


RENEW_OK = {"content": {"data": {"processFinished": True, "resultCode": 100}}}
SHARED = "Domain=.goofish.com; Path=/"


def keep_login_cookies(hours: float = 24) -> dict[str, str]:
    """开了保持登录的账号才有的长登录标记，hours 小时后到期（负数是已经过期）。"""
    return {"havana_lgc_exp": str(int((time.time() + hours * 3600) * 1000))}


class FakePlatform:
    """按接口名模拟商品和订单相关的 mtop 接口，状态存在 self.items、self.orders 里。

    `session_expired` 为 True 时 mtop 接口都返回会话过期，免密续期成功后恢复。
    """

    def __init__(self, items, orders=None):
        # item_id → {"title", "price": 分, "status", "post": editDetail 的邮费（可选）,
        #            "sku": 多规格（可选）, "desc": 描述（可选，没有就用标题）}
        self.items = items
        # order_id → {"item_id", "status": 状态文字, "price": 分, "post": 运费分, "created": 毫秒,
        #             "bought": 当前账号是买家（可选）}
        self.orders = orders or {}
        self.calls: list[tuple[str, dict]] = []
        self.headers: list[httpx.Headers] = []
        self.fail: dict[str, str] = {}  # 接口名 → 要返回的错误码
        self.session_expired = False
        self.renew_ok = True
        self.renews = 0  # silentHasLogin 被调了几次
        self.im_tokens = 0  # 即时消息的 accessToken 发了几个
        self.user_queries: list[dict] = []  # 查会话对方昵称的请求
        self.nicks: dict[str, str] = {}  # 会话 ID → 对方的闲鱼昵称，没有的回打码的账号名
        # 发布
        self.uploads: list[bytes] = []  # 传上来的图片（multipart 请求体）
        self.published: list[dict] = []  # publish 收到的请求体
        self.edited: list[dict] = []  # edit 收到的请求体
        self.publish_limited = False
        self.bad_words: list[str] = []
        self.amap_queries: list[dict] = []  # 高德关键字搜索收到的参数
        self.amap_info = "OK"  # 不是 OK 时高德返回出错（key 不对、额度用完）
        # 账号矩阵：unb → 个人主页的昵称（没有的回"鱼友<unb>"）
        self.nicknames: dict[str, str] = {}
        self.unb = UNB  # 这次请求 cookie 里的 unb
        self.login_user: str | None = None  # 设了的话"当前登录用户"总是它，不看 cookie

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "passport.goofish.com":
            return self.passport(request)
        if request.url.host == "stream-upload.goofish.com":
            return self.upload(request)
        if request.url.host == "restapi.amap.com":
            return self.amap_search(request)
        api = request.url.params["api"]
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        data = json.loads(form["data"])
        self.calls.append((api, data))
        self.headers.append(request.headers)
        self.unb = _jar(request).get("unb") or UNB
        if self.session_expired:
            return httpx.Response(200, json={"ret": ["FAIL_SYS_SESSION_EXPIRED::Session过期"]})
        if api in self.fail:
            return httpx.Response(200, json={"ret": [self.fail[api]]})
        handler = getattr(self, api.replace(".", "_"))
        try:
            body = handler(data)
        except KeyError:  # 查的商品或订单不存在
            return httpx.Response(200, json={"ret": ["FAIL_BIZ_NOT_EXIST::数据不存在"]})
        return httpx.Response(
            200,
            json={"ret": ["SUCCESS::调用成功"], "data": body},
            headers={"set-cookie": "_m_h5_tk=renewed_1; Domain=.goofish.com; Path=/"},
        )

    def passport(self, request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/newlogin/silentHasLogin.do"
        self.renews += 1
        if not self.renew_ok:
            return httpx.Response(200, json={"content": {"data": {"resultCode": 0}}})
        self.session_expired = False
        return httpx.Response(
            200, json=RENEW_OK, headers={"set-cookie": f"cookie2=renewed; {SHARED}"}
        )

    def mtop_taobao_idlemessage_pc_loginuser_get(self, data):
        return {"userId": int(self.login_user or self.unb)}

    def mtop_idle_web_user_page_head(self, data):
        assert data == {"self": True}
        return {
            "baseInfo": {"kcUserId": self.unb, "self": True},
            "module": {
                "base": {
                    "displayName": self.nicknames.get(self.unb, f"鱼友{self.unb}"),
                    "avatar": {"avatar": f"http://img.alicdn.com/bao/uploaded/{self.unb}.jpg"},
                    "ipLocation": "广东省",
                    "introduction": "",
                    "ylzTags": [
                        {"code": "cs_seller_level", "text": "卖家信用极好", "attributes": {}},
                        {"code": "cs_buyer_level", "text": "买家信用良好", "attributes": {}},
                    ],
                },
                "social": {"followStatus": 1, "followers": "3", "following": "8"},
                "tabs": {"item": {"number": 2, "name": "宝贝"}},
            },
        }

    def mtop_taobao_idlemessage_pc_login_token(self, data):
        self.im_tokens += 1
        expires_ms = int((time.time() + 86400) * 1000)
        return {"accessToken": f"im{self.im_tokens}", "accessTokenExpiredTime": str(expires_ms)}

    def mtop_taobao_idlemessage_pc_user_query(self, data):
        self.user_queries.append(data)
        nick = self.nicks.get(data["sessionId"], "")
        return {"userInfo": {"fishNick": nick, "nick": "x***9", "type": data["type"], "logo": ""}}

    def mtop_taobao_idlemtopsearch_pc_search(self, data):
        """标题里有搜索词的商品，一页全给。"""
        rows = [
            {
                "data": {
                    "item": {
                        "main": {
                            "exContent": {"itemId": item_id, "title": it["title"]},
                            "clickParam": {"args": {"price": str(it["price"] / 100)}},
                        }
                    }
                }
            }
            for item_id, it in self.items.items()
            if data["keyword"] in it["title"]
        ]
        return {
            "resultList": rows,
            "resultInfo": {"hasNextPage": False, "searchResControlFields": {"numFound": len(rows)}},
        }

    def mtop_idle_web_xyh_item_list(self, data):
        cards = [
            {
                "cardData": {
                    "id": item_id,
                    "title": it["title"],
                    "itemStatus": it["status"],
                    "priceInfo": {"price": str(it["price"] / 100)},
                    "picInfo": {"picUrl": ""},
                }
            }
            for item_id, it in self.items.items()
            if it["status"] in (0, 1)
        ]
        return {"cardList": cards, "nextPage": False}

    # 没有 mtop_taobao_idle_pc_detail：买家看的商品详情接口后台不调（真平台会要滑块），
    # 哪里又调了它，这里找不到处理函数，测试直接报错

    def mtop_idle_pc_idleitem_editDetail(self, data):
        """结构照实测的 editDetail 裁剪（见 test_item.py 的 EDIT_DETAIL），数值是编的。"""
        item_id = data["itemId"]
        it = self.items[item_id]
        detail = {
            "itemId": item_id,
            "itemTypeStr": "b",
            "quantity": "1",
            "imageInfoDOList": [
                {
                    "major": "true",
                    "templateIndex": "27923_1",
                    "widthSize": "800",
                    "heightSize": "600",
                    "type": "0",
                    "url": f"http://img.example/{item_id}.jpg",
                    "extraInfo": {"raw": "false", "isT": "false", "isH": "false"},
                },
            ],
            "itemTextDTO": {
                "titleDescSeparate": "false",
                "title": it["title"],
                "desc": it.get("desc", it["title"]),
            },
            "itemLabelExtList": [
                {
                    "propertyId": "-10000",
                    "text": "洗衣机",
                    "properties": "-10000##分类:126856141##洗衣机",
                },
                {
                    "propertyId": "20879",
                    "text": "几乎全新",
                    "properties": "20879##成色:21458##几乎全新",
                },
            ],
            "itemPriceDTO": {"origPriceInCent": "0", "priceInCent": str(it["price"])},
            "userRightsProtocols": [{"enable": "true", "serviceCode": "OLD"}],
            "itemPostFeeDTO": it.get("post")
            or {
                "canFreeShipping": "true",
                "onlyTakeSelf": "false",
                "supportFreight": "true",
                "templateId": "0",
                "postPriceInCent": "0",
            },
            "itemAddrDTO": {
                "area": "龙岗区",
                "city": "深圳",
                "prov": "广东",
                "divisionId": "440307",
                "gps": "22.6,114.1",
                "poiId": "P1",
                "poiName": "某广场",
            },
            "itemCatDTO": {
                "catId": "50025413",
                "leafId": "872",
                "catName": "洗衣机",
                "tbCatId": "350301",
                "channelCatId": "126856141",
            },
        }
        if it.get("sku"):
            detail["itemSkuList"] = it["sku"]
        return detail

    def mtop_idle_pc_idleitem_edit(self, data):
        self.edited.append(data)
        it = self.items[data["itemId"]]
        it["price"] = int(data["itemPriceDTO"]["priceInCent"])
        it["title"] = data["itemTextDTO"]["desc"]
        return {"itemId": data["itemId"]}

    def mtop_taobao_idle_item_downshelf(self, data):
        self.items[data["itemId"]]["status"] = -1  # 下架后不在主页列表里
        return {}

    def com_taobao_idle_item_delete(self, data):
        del self.items[data["itemId"]]
        return {}

    # ---- 发布 ----

    def upload(self, request: httpx.Request) -> httpx.Response:
        assert request.url.params["appkey"] == "fleamarket"
        self.uploads.append(request.content)
        url = f"https://img.example/up{len(self.uploads)}.jpg"
        return httpx.Response(200, json={"success": True, "object": {"url": url, "pix": "80x60"}})

    def amap_search(self, request: httpx.Request) -> httpx.Response:
        """高德关键字搜索：名字里有搜索词的地点。"""
        query = dict(request.url.params)
        self.amap_queries.append(query)
        if self.amap_info != "OK":
            return httpx.Response(200, json={"status": "0", "info": self.amap_info})
        pois = [p for p in AMAP_POIS if query["keywords"] in p["name"]]
        return httpx.Response(200, json={"status": "1", "info": "OK", "pois": pois})

    def mtop_idle_pc_idleitem_prepublish_check(self, data):
        if self.publish_limited:
            info = {"title": "发布受限", "content": "请到闲鱼 App 查看"}
            return {"limited": True, "publishLimitInfo": info}
        return {"limited": False}

    def mtop_taobao_idleitem_badwords_prepubcheck(self, data):
        if not self.bad_words:
            return {"result": False, "forbidPublish": False}
        words = {"items": [{"words": self.bad_words}]}
        return {"result": True, "forbidPublish": True, "msg": "含违禁词", "badWordAlertInfo": words}

    def mtop_taobao_idle_local_poi_get(self, data):
        return {"selectedPoi": POI_HOME, "commonAddresses": [POI_HOME, POI_SHOP]}

    def mtop_taobao_idle_kgraph_property_recommend(self, data):
        return recommend_result(data)

    def mtop_idle_item_publish_service_cards_list(self, data):
        json.loads(data["itemInfoJson"])["itemCatDTO"]  # 没有类目平台报 item cat info miss
        return {"services": [{"code": "SVC", "defaultEnable": True}, {"code": "AI_SALE"}]}

    def mtop_idle_pc_idleitem_publish(self, data):
        self.published.append(data)
        item_id = str(9000 + len(self.published))
        title = data["itemTextDTO"]["desc"][:30]
        price = int(data["itemPriceDTO"]["priceInCent"])
        self.items[item_id] = {"title": title, "price": price, "status": 0}
        return {"itemId": item_id}

    # ---- 订单 ----

    def _order_total(self, order):
        return order["price"] + order.get("post", 0)

    def _newest(self, bought: bool) -> list[str]:
        ids = [oid for oid, o in self.orders.items() if o.get("bought", False) == bought]
        return sorted(ids, key=lambda oid: -self.orders[oid]["created"])

    def mtop_taobao_idle_trade_merchant_sold_get(self, data):
        page, size = data["pageNumber"], data["rowsPerPage"]
        ids = self._newest(bought=False)
        rows = [
            {
                "commonData": {
                    "orderId": oid,
                    "itemId": self.orders[oid]["item_id"],
                    "orderStatus": self.orders[oid]["status"],
                    "inRefund": self.orders[oid].get("in_refund", False),
                    "orderCreateTime": str(self.orders[oid]["created"]),
                },
                "buyerInfoVO": {"buyerId": "7", "buyerNick": "买家甲"},
                "priceVO": {"totalPrice": f"{self._order_total(self.orders[oid]) / 100:.2f}"},
            }
            for oid in ids[(page - 1) * size : page * size]
        ]
        return {"module": {"items": rows, "nextPage": page * size < len(ids)}}

    def mtop_idle_web_trade_bought_list(self, data):
        """我买到的，一页 10 条；第二页起要带上一页的 lastEndRow（offsetRow）。"""
        page = data["pageNumber"]
        assert page == 1 or data["offsetRow"] == str((page - 1) * 10)
        ids = self._newest(bought=True)
        rows = [
            {
                "commonData": {
                    "orderIdStr": oid,
                    "itemId": int(self.orders[oid]["item_id"]),
                    "peerUserId": 8,
                    "seller": False,
                },
                "head": {
                    "data": {
                        "createTime": str(self.orders[oid]["created"]),
                        "statusViewMsg": self.orders[oid]["status"],
                        "userInfo": {"userId": "8", "userNick": "卖家乙"},
                    }
                },
                "content": {
                    "data": {
                        "detailInfo": {
                            "auctionTitle": self.items.get(self.orders[oid]["item_id"], {}).get(
                                "title", ""
                            ),
                            "auctionPic": "http://img.example/b.jpg",
                        },
                        "priceInfo": {
                            "price": f"{self._order_total(self.orders[oid]) / 100:.2f}",
                            "buyAmount": 1,
                        },
                    }
                },
            }
            for oid in ids[(page - 1) * 10 : page * 10]
        ]
        more = page * 10 < len(ids)
        return {"items": rows, "nextPage": str(more).lower(), "lastEndRow": str(page * 10)}

    def mtop_idle_web_trade_order_detail(self, data):
        """结构照 2026-10-29 实测的订单详情裁剪：已发货后没有收货地址区块，地址在订单信息里。"""
        order = self.orders[data["tid"]]
        item = self.items.get(order["item_id"], {})
        total = self._order_total(order)
        seller = not order.get("bought", False)
        unpaid = order["status"] == "等待买家付款"
        components = [
            {
                "render": "orderStatusVO",
                "data": {
                    "orderStatusInfo": {"title": order["status"], "descRichText": []},
                    "orderStatusNodeList": [
                        {"title": "已拍下", "completed": True, "time": "2026-10-29 12:00:00"},
                        {"title": "已付款", "completed": not unpaid, "time": None},
                    ],
                },
            },
            {
                "render": "orderInfoVO",
                "data": {
                    "itemInfo": {
                        "title": item.get("title", ""),
                        "itemMainPictCdnUrl": "http://img.example/x.jpg",
                        "buyAmount": "1",
                    },
                    "priceInfo": {
                        "amount": {"title": "成交价", "value": f"{total / 100:.2f}"},
                        "billList": [
                            {"title": "商品总价", "value": f"{order['price'] / 100:.2f}"},
                            {"title": "运费", "value": f"{order.get('post', 0) / 100:.2f}"},
                        ],
                        "softwareBill": {"title": "软件服务费", "value": "-1.00"},
                        "expectBill": {"title": "预估到手", "value": f"{total / 100 - 1:.2f}"},
                    },
                    "orderInfoList": [
                        {"title": "订单编号", "value": data["tid"], "copyable": True},
                        {"title": "买家昵称", "value": "买家甲"}
                        if seller
                        else {"title": "卖家昵称", "value": "卖家乙"},
                        {"title": "下单时间", "value": "2026-10-29 12:00:00"},
                    ],
                },
            },
        ]
        if unpaid:
            address = {"name": "张三", "phoneNumber": "138****0000", "address": "某地某路"}
            components.insert(1, {"render": "addressInfoVO", "data": address})
        return {
            "itemId": int(order["item_id"]),
            "orderId": int(data["tid"]),
            "seller": seller,
            "peerUserId": "7",
            "commonInfo": {"createTime": str(order["created"])},
            "utArgs": {"orderStatusName": f"{order['status']}（说明）", "seller": str(seller)},
            "bottomBarVO": {"buttonList": [{"name": "修改价格", "tradeAction": "MODIFY_PRICE"}]},
            "components": components,
        }

    def mtop_taobao_idle_trade_order_modify_price_render(self, data):
        order = self.orders[data["bizOrderId"]]
        if order["status"] != "等待买家付款":
            return {"modifyPriceRenderList": []}
        return {
            "modifyPriceRenderList": [
                {"key": "modifyFee", "name": "商品价格", "price": f"{order['price'] / 100:.2f}"},
                {"key": "newTransportFee", "name": "运费", "price": f"{order['post'] / 100:.2f}"},
            ]
        }

    def mtop_taobao_idle_trade_user_adjust_price(self, data):
        order = self.orders[data["orderId"]]
        order["price"] = int(data["modifyFee"])
        order["post"] = int(data["newTransportFee"])
        return {}


# ---- 发布 ----


def _jar(request: httpx.Request) -> dict[str, str]:
    cookies = {}
    for part in request.headers.get("cookie", "").split(";"):
        name, _, value = part.strip().partition("=")
        cookies[name] = value
    return cookies


def png(color="red", size=(80, 60), mode="RGB") -> bytes:
    out = io.BytesIO()
    Image.new(mode, size, color).save(out, "PNG")
    return out.getvalue()


POI_HOME = {
    "poi": "光谷苑",
    "poiId": "B0FFGFC0N7",
    "area": "光明区",
    "city": "深圳",
    "prov": "广东",
    "divisionId": 440311,
    "latitude": "22.735524",
    "longitude": "113.929284",
    "address": "同业路340号",
}
POI_SHOP = {**POI_HOME, "poi": "万达广场", "poiId": "B0GDVC8H1H", "address": "龙岗大道"}

# 高德关键字搜索的返回（v3/place/text），没有值的字段是空数组
AMAP_POIS = [
    {
        "id": "B000A7BD6C",
        "name": "北京站",
        "address": "毛家湾胡同甲13号",
        "location": "116.427287,39.903030",
        "pname": "北京市",
        "cityname": "北京市",
        "adname": "东城区",
        "adcode": "110101",
    },
    {
        "id": "B0FFFAB6J2",
        "name": "北京路步行街",
        "address": [],
        "location": "113.269295,23.125148",
        "pname": "广东省",
        "cityname": "广州市",
        "adname": "越秀区",
        "adcode": "440104",
    },
    {"id": "", "name": "北京市", "address": [], "location": [], "adcode": "110000"},
]

# 假的类目推荐：两个分类、一个品牌属性。描述里有"充电宝"时多一张要填 3C 证书的卡片（网页端不支持）
CATEGORIES = [("126858403", "落地灯", "50025403"), ("126858404", "台灯", "50025404")]
BRANDS = [("6277054", "IKEA/宜家"), ("11", "飞利浦")]


def _clicked_key(cards, property_id, key):
    for card in cards or []:
        if card.get("propertyId") == property_id:
            for value in card.get("valuesList") or []:
                if value.get("isClicked") == "1":
                    return value[key]
    return None


def recommend_result(data):
    """选过的分类、品牌照旧（带了 currentCardList 时），没选过的选第一个。"""
    current = data.get("currentCardList")
    cat_key = _clicked_key(current, "-10000", "channelCatId") or CATEGORIES[0][0]
    brand_key = _clicked_key(current, "20000", "valueId") or BRANDS[0][0]
    category = {
        "cardType": "20401",
        "cardData": {
            "propertyId": "-10000",
            "propertyName": "分类",
            "valuesList": [
                {
                    "catId": cat_id,
                    "catName": name,
                    "channelCatId": key,
                    "tbCatId": "50019939",
                    "leafId": "1044",
                    "propertyId": "-10000",
                    "propertyName": "分类",
                    "isClicked": "1" if key == cat_key else "0",
                    "isUserClick": "0",
                    "transportData": {"channelCateId": key, "propertyId": "-10000"},
                }
                for key, name, cat_id in CATEGORIES
            ],
        },
    }
    brand = {
        "cardType": "20401",
        "cardData": {
            "propertyId": "20000",
            "propertyName": "品牌",
            "valuesList": [
                {
                    "valueId": key,
                    "valueName": name,
                    "propertyId": "20000",
                    "propertyName": "品牌",
                    "isClicked": "1" if key == brand_key else "0",
                    "isUserClick": "0",
                    "transportData": {"valueId": key, "valueName": name, "propertyId": "20000"},
                }
                for key, name in BRANDS
            ],
        },
    }
    cards = [category, brand]
    if "充电宝" in data["description"]:
        cert = {"propertyId": "230186843", "propertyName": "3C证书编号"}
        cards.append({"cardType": "7001", "cardData": cert})
    predicted = {"catId": 50025403, "catName": "落地灯", "channelCatId": 126858403}
    return {"cardList": cards, "categoryPredictResult": predicted, "result": "OKAY"}


# ---- 扫码登录 ----

LOGIN_FORM = {
    "appName": "xianyu",
    "appEntrance": "web",
    "_csrf_token": "csrf-abc",
    "umidToken": "umid-xyz",
    "hsiz": "hs-1",
    "isMobile": False,
    "returnUrl": "",
    "fromSite": 77,
}
# 故意让 JSON 字符串里出现 "};"，验证解析不会提前截断
VIEW_DATA = {"loginFormData": LOGIN_FORM, "title": "登录};"}
MINI_LOGIN_HTML = (
    '<html><meta name="eagleeye-trace" content="trace-1">'
    "<script>window.viewData = " + json.dumps(VIEW_DATA, ensure_ascii=False) + ";"
    "window.other = {};</script></html>"
)

# "保持登录状态"弹窗各按钮的地址，平台给的是相对地址
KEEP_LOGIN_DIALOG_URLS = {
    "dialogConfirmLoginUrl": "/newlogin/keepLoginConfirm.do?choice=keep&token=tk",
    "dialogRefuseLoginUrl": "/newlogin/keepLoginConfirm.do?choice=refuse&token=tk",
}


class FakeXianyu:
    """按 URL 模拟扫码登录用到的接口；query.do 按 statuses 顺序返回，只剩一个时一直返回它。"""

    def __init__(self, statuses, *, confirm_extra=None, keep_login=True, keep_login_dialog=False):
        self.statuses = list(statuses)
        self.confirm_extra = confirm_extra or {}
        self.keep_login = keep_login  # False 时打开保持登录的接口报错
        # True 时像线上的账号：确认时要登录框弹"保持登录状态"，个人信息里的开关不给开
        self.keep_login_dialog = keep_login_dialog
        self.dialog_result = {"resultCode": 100}  # 弹窗里点"保持"的返回
        self.long_login_on = False
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        host, path = request.url.host, request.url.path
        if host == "h5api.m.goofish.com":
            return httpx.Response(
                200,
                json={"ret": ["FAIL_SYS_TOKEN_EMPTY::令牌为空"]},
                headers={"set-cookie": f"_m_h5_tk=boot_1; {SHARED}"},
            )
        if path == "/mini_login.htm":
            return httpx.Response(200, text=MINI_LOGIN_HTML)
        if path == "/newlogin/qrcode/generate.do":
            return httpx.Response(
                200,
                json={
                    "content": {
                        "success": True,
                        "data": {
                            "t": 1761600000123,
                            "ck": "ck-1",
                            "codeContent": "https://qr.example/x",
                        },
                    }
                },
            )
        if path == "/newlogin/qrcode/query.do":
            status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
            if status == "HAS_ERROR":
                return httpx.Response(200, json={"hasError": True})
            data = {"qrCodeStatus": status}
            headers = []
            if status == "CONFIRMED":
                if self.keep_login_dialog:
                    data |= {
                        "dialogAction": "keepLoginConfirm",
                        "feedbackPayload": {"dynamicUrl": KEEP_LOGIN_DIALOG_URLS},
                    }
                data |= self.confirm_extra
                headers = [
                    ("set-cookie", f"cookie2=c2; {SHARED}; HttpOnly"),
                    ("set-cookie", f"unb=42; {SHARED}"),
                ]
            return httpx.Response(200, json={"content": {"data": data}}, headers=headers)
        if path == "/newlogin/keepLoginConfirm.do":
            choice = request.url.params["choice"]
            headers = []
            if choice == "keep" and self.dialog_result.get("resultCode") == 100:
                self.long_login_on = True
                headers = [("set-cookie", f"havana_lgc_exp=4102444800000; {SHARED}")]
            return httpx.Response(
                200, json={"content": {"data": self.dialog_result}}, headers=headers
            )
        if host == "www.goofish.com" and path == "/im":
            return httpx.Response(
                200, text="<html>im</html>", headers=[("set-cookie", f"sgcookie=sg; {SHARED}")]
            )
        if path == "/ac/account/setLoginSettings.do":
            if not self.keep_login:
                return httpx.Response(500)
            self.long_login_on = True
            return httpx.Response(
                200,
                json={"success": True},
                headers=[("set-cookie", f"havana_lgc_exp=4102444800000; {SHARED}")],
            )
        if path == "/ac/account/queryLoginSettings.do":
            value = {
                "canOpenLongLogin": not self.keep_login_dialog,
                "hasLongTokenLogin": self.long_login_on,
            }
            return httpx.Response(200, json={"returnValue": value, "success": True})
        return httpx.Response(404)

    def find(self, path):
        return [r for r in self.requests if r.url.path == path]
