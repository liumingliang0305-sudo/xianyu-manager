"""测试共用的假平台。"""

import json
import time
from urllib.parse import parse_qs

import httpx

UNB = "42"


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
        # item_id → {"title", "price": 分, "status"}
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

    def __call__(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "passport.goofish.com":
            return self.passport(request)
        api = request.url.params["api"]
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        data = json.loads(form["data"])
        self.calls.append((api, data))
        self.headers.append(request.headers)
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
        return {"userId": int(UNB)}

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

    def mtop_taobao_idle_pc_detail(self, data):
        it = self.items[data["itemId"]]
        return {
            "itemDO": {
                "itemId": int(data["itemId"]),
                "title": it["title"],
                "soldPrice": str(it["price"] / 100),
                "itemStatus": it["status"],
                "browseCnt": 100,
                "wantCnt": 5,
                "collectCnt": 2,
            }
        }

    def mtop_idle_pc_idleitem_editDetail(self, data):
        it = self.items[data["itemId"]]
        return {
            "itemId": data["itemId"],
            "itemTextDTO": {"desc": it["title"]},
            "itemPriceDTO": {"priceInCent": str(it["price"])},
        }

    def mtop_idle_pc_idleitem_edit(self, data):
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

    # ---- 订单 ----

    def _order_total(self, order):
        return order["price"] + order.get("post", 0)

    def mtop_taobao_idle_trade_merchant_sold_get(self, data):
        page, size = data["pageNumber"], data["rowsPerPage"]
        ids = sorted(self.orders, key=lambda oid: -self.orders[oid]["created"])
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
                        {"title": "买家昵称" if seller else "卖家昵称", "value": "买家甲"},
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
