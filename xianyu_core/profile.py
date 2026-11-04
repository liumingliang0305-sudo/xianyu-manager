"""自己账号的个人资料：昵称、头像、IP 属地、信用、粉丝。

接口是网页端个人主页头部（goofish.com/personal）同一个，`{"self": true}` 查自己。只读。
✅ 2026-11-04 实测返回的样子（见协议笔记"账号资料"）：

- `module.base`：`displayName` 昵称、`avatar.avatar` 头像（http 开头）、`ipLocation`（"广东省"）、
  `introduction` 简介、`ylzTags` 信用标签（`code` 是 `cs_seller_level` / `cs_buyer_level`，
  `text` 是"卖家信用极好"这样的字，`attributes.level` 是等级）；
- `module.social`：`followers`、`following`，是数字字符串；
- `module.tabs`：`item.number` 主页上的宝贝数，`rate.number` 评价数（字符串）。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from xianyu_core.mtop import MtopClient

PROFILE_API = "mtop.idle.web.user.page.head"
SPM_PERSONAL = "a21ybx.personal.0.0"


@dataclass(frozen=True)
class Profile:
    nick: str
    avatar: str  # 头像地址，换成了 https；没有是空字符串
    ip_location: str
    seller_credit: str  # "卖家信用极好"，没有是空字符串
    followers: int | None


def _number(value: Any) -> int | None:
    try:
        return int(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


def _https(url: str) -> str:
    if url.startswith("//"):
        return "https:" + url
    if url.startswith("http://"):
        return "https://" + url.removeprefix("http://")
    return url


def parse_profile(data: dict[str, Any]) -> Profile:
    module = data.get("module") if isinstance(data.get("module"), dict) else {}
    base = module.get("base") if isinstance(module.get("base"), dict) else {}
    social = module.get("social") if isinstance(module.get("social"), dict) else {}
    avatar = base.get("avatar")
    if isinstance(avatar, dict):
        avatar = avatar.get("avatar")
    seller_credit = ""
    for tag in base.get("ylzTags") or []:
        if isinstance(tag, dict) and tag.get("code") == "cs_seller_level":
            seller_credit = str(tag.get("text") or "")
    return Profile(
        nick=str(base.get("displayName") or ""),
        avatar=_https(str(avatar or "")),
        ip_location=str(base.get("ipLocation") or ""),
        seller_credit=seller_credit,
        followers=_number(social.get("followers")),
    )


async def fetch_profile(mtop: MtopClient) -> Profile:
    data = await mtop.call(PROFILE_API, {"self": True}, spm_cnt=SPM_PERSONAL)
    return parse_profile(data)
