"""测试共用的假平台。"""

import json
from urllib.parse import parse_qs

import httpx

UNB = "42"


class FakePlatform:
    """按接口名模拟商品相关的 mtop 接口，商品状态存在 self.items 里。"""

    def __init__(self, items):
        # item_id → {"title", "price": 分, "status"}
        self.items = items
        self.calls: list[tuple[str, dict]] = []
        self.fail: dict[str, str] = {}  # 接口名 → 要返回的错误码

    def __call__(self, request: httpx.Request) -> httpx.Response:
        api = request.url.params["api"]
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        data = json.loads(form["data"])
        self.calls.append((api, data))
        if api in self.fail:
            return httpx.Response(200, json={"ret": [self.fail[api]]})
        handler = getattr(self, api.replace(".", "_"))
        return httpx.Response(
            200,
            json={"ret": ["SUCCESS::调用成功"], "data": handler(data)},
            headers={"set-cookie": "_m_h5_tk=renewed_1; Domain=.goofish.com; Path=/"},
        )

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
