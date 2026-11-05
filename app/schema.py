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
    ForeignKey,
    Identity,
    Index,
    Integer,
    MetaData,
    Table,
    Text,
    text,
)

# 主键、外键名和 PostgreSQL 默认起的一样，自动生成迁移时才不会对出差异
metadata = MetaData(
    naming_convention={
        "pk": "%(table_name)s_pkey",
        "fk": "%(table_name)s_%(column_0_name)s_fkey",
    }
)

# 管理后台的用户，见 app/users.py。由命令行 xym users add 建，没有注册入口
users = Table(
    "users",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("username", Text, nullable=False),
    Column("password_hash", Text, nullable=False),  # argon2id
    # 改密码、停用时加一，之前签发的访问令牌跟着作废
    Column("token_version", Integer, nullable=False, server_default=text("0")),
    Column("disabled", Boolean, nullable=False, server_default=text("false")),
    # 登录同步插件用的令牌，只存 SHA-256；没生成过是 NULL
    Column("plugin_token_hash", Text),
    Column("plugin_token_at", Double),
    Column("created_at", Double, nullable=False),
    Index("users_username", "username", unique=True),
    Index("users_plugin_token", "plugin_token_hash", unique=True),
)

# 刷新令牌：一次登录一个，放在浏览器的 HttpOnly cookie 里，库里只存 SHA-256。
# 用一次往后顺延，太久没用或者到了最长期限就过期；退出登录时删掉
refresh_tokens = Table(
    "refresh_tokens",
    metadata,
    Column("token_hash", Text, primary_key=True),
    Column("user_id", BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", Double, nullable=False),
    Column("used_at", Double, nullable=False),
    Index("refresh_tokens_user", "user_id"),
)

# 闲鱼账号归哪个用户：一个账号只归一个用户。没有归属的账号（有用户以前就登录过的）谁都看不到，
# 建第一个用户时归给他；之后谁在管理后台登录（扫码、导入浏览器登录）就归谁
account_owners = Table(
    "account_owners",
    metadata,
    Column("account_id", Text, primary_key=True),  # 账号 unb
    Column("user_id", BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
    Column("created_at", Double, nullable=False),
    Index("account_owners_user", "user_id"),
)

# 账号矩阵（app/matrix.py）里每个账号的资料：昵称、头像这些从平台个人主页拉来的，备注是自己写的。
# 移除账号时一起删
account_profiles = Table(
    "account_profiles",
    metadata,
    Column("account_id", Text, primary_key=True),
    Column("nick", Text, nullable=False, server_default=""),
    Column("avatar", Text, nullable=False, server_default=""),
    Column("ip_location", Text, nullable=False, server_default=""),
    Column("seller_credit", Text, nullable=False, server_default=""),  # "卖家信用极好"
    Column("followers", Integer),
    Column("fetched_at", Double),  # 上次从平台拉资料的时间，没拉到过是 NULL
    Column("remark", Text, nullable=False, server_default=""),  # 自己写的备注
)

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

# 发布商品的草稿，见 app/drafts.py。发布成功以后留着，记下发出去的商品 ID。编辑已有商品也是一个草稿
drafts = Table(
    "drafts",
    metadata,
    Column("id", BigInteger, Identity(), primary_key=True),
    Column("account_id", Text, nullable=False),
    Column("description", Text, nullable=False, server_default=""),  # 网页端只有描述，标题由平台截
    Column("price_cent", BigInteger),  # 还没填是 NULL
    Column("orig_price_cent", BigInteger),
    # 邮费：free 包邮 / distance 按距离计费 / fixed 一口价 / none 无需邮寄
    Column("post_mode", Text, nullable=False, server_default="free"),
    Column("post_price_cent", BigInteger),  # 一口价的邮费
    Column("take_self", Boolean, nullable=False, server_default=text("false")),  # 支持自提
    # JSON：[{sha, width, height, url}]，文件在 data/images/<sha>.jpg；url 是传到闲鱼以后的地址
    Column("images", Text, nullable=False, server_default="[]"),
    # JSON：平台推荐的分类和属性卡片（cardList 原样，选中的值 isClicked 是 "1"）
    Column("cards", Text, nullable=False, server_default="[]"),
    Column("category", Text),  # JSON：itemCatDTO，还没推荐过是 NULL
    Column("address", Text),  # JSON：itemAddrDTO，还没选是 NULL
    Column("item_id", Text),  # 发布成功后的商品 ID；编辑草稿提交成功后是改的那个商品
    Column("error", Text),  # 上次发布失败的原因
    # 编辑已有商品的草稿：改的是哪个商品，和建草稿时 editDetail 的返回（JSON，提交时照它改）。
    # 发新商品的草稿是 NULL
    Column("edit_item_id", Text),
    Column("origin", Text),
    # 重新上架的草稿：照哪个商品抄的（origin 是那个商品的 editDetail），发布出去是个新商品
    Column("relist_item_id", Text),
    # 照拼多多货源建的草稿：哪个拼多多商品（pdd_goods.goods_id），别的是 NULL
    Column("pdd_goods_id", Text),
    Column("created_at", Double, nullable=False),
    Column("updated_at", Double, nullable=False),
    Column("published_at", Double),
    Index("drafts_account", "account_id", "updated_at"),
)

# 订单：卖出的和买到的
orders = Table(
    "orders",
    metadata,
    Column("account_id", Text, primary_key=True),
    Column("order_id", Text, primary_key=True),
    Column("side", Text, nullable=False, server_default="sold"),  # sold 卖出的，bought 买到的
    Column("item_id", Text, nullable=False, server_default=""),
    Column("title", Text, nullable=False, server_default=""),
    Column("pic_url", Text, nullable=False, server_default=""),
    Column("status", Text, nullable=False),  # 归一后的状态，见 xianyu_core/order.py
    Column("status_text", Text, nullable=False, server_default=""),  # 平台原文
    Column("price_cent", BigInteger),  # 订单金额
    Column("quantity", Integer, nullable=False, server_default=text("1")),
    # 对方：卖出的订单是买家，买到的订单是卖家
    Column("peer_id", Text, nullable=False, server_default=""),
    Column("peer_nick", Text, nullable=False, server_default=""),
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
    # 平台的 unreadCount：还有几个接收方没读。自己发的消息上 0 就是对方读了；平台没给是 NULL
    Column("unread_count", Integer),
    Index("messages_conv", "account_id", "cid", "sent_at"),
)

# 自动同步（app/autosync.py）：每个账号每种同步一行，存设置和上次跑的结果。
# 手动点同步也记在这里，定时任务从这次往后顺延
sync_jobs = Table(
    "sync_jobs",
    metadata,
    Column("account_id", Text, primary_key=True),
    Column("job", Text, primary_key=True),  # items 商品 / sold 卖出的进行中订单 / bought 买到的订单
    Column("enabled", Boolean, nullable=False, server_default=text("true")),
    Column("interval", Double),  # 定时同步的间隔（秒），NULL 是默认值
    Column("last_run_at", Double),  # 上次跑完的时间，成功失败都算
    Column("last_ok_at", Double),  # 上次成功的时间
    Column("last_trigger", Text),  # schedule 定时 / visit 打开页面 / message 交易消息 / manual 手动
    Column("last_error", Text),  # 上次失败的原因，成功了清掉
    Column("last_result", Text, nullable=False, server_default="{}"),  # JSON：上次成功时各项几个
    Column("next_run_at", Double),  # 下次定时同步的时间，NULL 是到期了（从没跑过）
)

# 拼多多货源（app/pdd.py）：浏览器插件从拼多多商品页导入的商品，按管理后台的用户存，不跟闲鱼账号走
pdd_goods = Table(
    "pdd_goods",
    metadata,
    Column("user_id", BigInteger, ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
    Column("goods_id", Text, primary_key=True),  # 拼多多的商品 ID
    Column("title", Text, nullable=False),
    Column("mall_name", Text, nullable=False, server_default=""),  # 店铺名
    Column("min_price_cent", BigInteger),  # 拼单价的区间，页面上没有是 NULL
    Column("max_price_cent", BigInteger),
    # JSON：主图、详情图、视频、规格、商品参数这些（见 PddGoods.to_data），
    # 图片下载下来以后带 sha，文件在 data/images/<sha>.jpg
    Column("data", Text, nullable=False, server_default="{}"),
    Column("imported_at", Double, nullable=False),  # 第一次导入
    Column("updated_at", Double, nullable=False),  # 最近一次导入
    Index("pdd_goods_user", "user_id", "updated_at"),
)
