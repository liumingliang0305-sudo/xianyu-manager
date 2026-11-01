"""表结构。只给 Alembic 用（生成迁移、核对迁移和这里对不对得上），业务代码直接写 SQL。

改表：改这里，然后 `uv run alembic revision --autogenerate -m "说明"`，检查生成的迁移文件再提交。
金额一律存分（整数），时间一律存秒级时间戳（双精度浮点）。
"""

from __future__ import annotations

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    Double,
    Identity,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    text,
)

# 主键名和 PostgreSQL 默认起的一样，自动生成迁移时才不会对出差异
metadata = MetaData(naming_convention={"pk": "%(table_name)s_pkey"})

items = Table(
    "items",
    metadata,
    Column("account_id", Text, primary_key=True),  # 账号 unb
    Column("item_id", Text, primary_key=True),
    Column("title", Text, nullable=False),
    Column("price_cent", BigInteger, nullable=False),
    Column("status", Integer, nullable=False),  # 平台的 itemStatus：0 在售，1 已卖出
    Column("category_id", BigInteger),
    Column("pic_url", Text, nullable=False, server_default=""),
    Column("want_count", Integer),
    Column("browse_count", Integer),  # 浏览、收藏只有同步时带 --stats 才有
    Column("collect_count", Integer),
    # 最近一次同步时还在主页列表里
    Column("on_platform", Boolean, nullable=False, server_default=text("true")),
    Column("first_seen_at", Double, nullable=False),
    Column("synced_at", Double, nullable=False),
)

# 通过本系统做过的每次改动，成功失败都记
item_ops = Table(
    "item_ops",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("account_id", Text, nullable=False),
    Column("item_id", Text, nullable=False),
    Column("action", Text, nullable=False),  # price / desc / downshelf / delete
    Column("detail", Text, nullable=False),  # JSON：改前改后
    Column("ok", Boolean, nullable=False),
    Column("error", Text),
    Column("created_at", Double, nullable=False),
    Index("item_ops_item", "account_id", "item_id"),
)

# 卖出的订单
orders = Table(
    "orders",
    metadata,
    Column("account_id", Text, primary_key=True),
    Column("order_id", Text, primary_key=True),
    Column("item_id", Text, nullable=False, server_default=""),
    Column("title", Text, nullable=False, server_default=""),
    Column("pic_url", Text, nullable=False, server_default=""),
    Column("status", Text, nullable=False),  # 归一后的状态，见 xianyu_core/order.py
    Column("status_text", Text, nullable=False, server_default=""),  # 平台原文
    Column("price_cent", BigInteger),  # 订单金额
    Column("quantity", Integer, nullable=False, server_default=text("1")),
    Column("buyer_id", Text, nullable=False, server_default=""),
    Column("buyer_nick", Text, nullable=False, server_default=""),
    Column("created_at", Double),  # 下单时间
    Column("raw", Text, nullable=False, server_default="{}"),  # 平台最近一次返回的原始数据（JSON）
    Column("first_seen_at", Double, nullable=False),
    Column("synced_at", Double, nullable=False),
)

# 通过本系统对订单做过的改动
order_ops = Table(
    "order_ops",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("account_id", Text, nullable=False),
    Column("order_id", Text, nullable=False),
    Column("action", Text, nullable=False),  # price
    Column("detail", Text, nullable=False),  # JSON：改前改后
    Column("ok", Boolean, nullable=False),
    Column("error", Text),
    Column("created_at", Double, nullable=False),
    Index("order_ops_order", "account_id", "order_id"),
)

# 即时消息的会话（单聊），见 app/messages.py
conversations = Table(
    "conversations",
    metadata,
    Column("account_id", Text, primary_key=True),
    Column("cid", Text, primary_key=True),  # 会话 ID，不带 @goofish
    Column("peer_id", Text, nullable=False, server_default=""),
    Column("peer_name", Text, nullable=False, server_default=""),  # 对方昵称
    # 对方的用户类型，查昵称要用；普通用户是 "0"
    Column("peer_type", Text, nullable=False, server_default=""),
    Column("named_at", Double),  # 上次向平台查对方昵称的时间，没查过是 NULL
    # 网页版显示不显示（在 App 里删掉的会话是 false）
    Column("visible", Boolean, nullable=False, server_default=text("true")),
    Column("muted", Boolean, nullable=False, server_default=text("false")),  # 开了消息免打扰
    # "1" 是商品会话，别的是系统通知之类
    Column("biz_type", Text, nullable=False, server_default=""),
    Column("item_id", Text, nullable=False, server_default=""),
    Column("item_title", Text, nullable=False, server_default=""),
    Column("item_pic", Text, nullable=False, server_default=""),
    Column("order_id", Text, nullable=False, server_default=""),
    # 平台的红点数，收到对方新消息时加一
    Column("unread", Integer, nullable=False, server_default=text("0")),
    Column("updated_at", Double),  # 最后有动静的时间
    Column("fetched_at", Double),  # 最新消息补拉到了哪个时间点，没拉过是 NULL
    Column("older_cursor", BigInteger),  # 往前翻历史消息的游标（毫秒），没翻过是 NULL
    Column("older_more", Boolean),  # 平台上还有没有更早的消息，不知道是 NULL
    Column("first_seen_at", Double, nullable=False),
)

messages = Table(
    "messages",
    metadata,
    Column("account_id", Text, primary_key=True),
    Column("message_id", Text, primary_key=True),
    Column("cid", Text, nullable=False),
    Column("sender_id", Text, nullable=False, server_default=""),
    Column("sender_name", Text, nullable=False, server_default=""),
    Column("sent_at", Double),
    Column("content_type", Integer),  # 1 是文本
    # 文本消息是正文，别的类型是平台给的摘要
    Column("text", Text, nullable=False, server_default=""),
    Column("item_id", Text, nullable=False, server_default=""),
    # 原始数据（JSON），去掉了发送端的设备信息
    Column("raw", Text, nullable=False, server_default="{}"),
    Column("received_at", Double, nullable=False),
    Index("messages_conv", "account_id", "cid", "sent_at"),
)
