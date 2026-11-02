"""搜索闲鱼上的商品（别人的），给管理后台的搜索页用。

只是转一次平台的搜索，结果不进本地库。用哪个账号搜就算哪个账号的请求：
走这个账号的限流和熔断，被风控时熔断的也是它。
"""

from __future__ import annotations

from app.accounts import Accounts
from xianyu_core.search import SearchApi, SearchPage, SearchQuery


class SearchService:
    def __init__(self, accounts: Accounts):
        self._accounts = accounts

    async def search(self, account_id: str, query: SearchQuery, page: int = 1) -> SearchPage:
        """搜一页（30 条）。条件不对时抛 ValueError，不请求平台。"""
        async with self._accounts.mtop(account_id) as mtop:
            return await SearchApi(mtop).search(query, page)
