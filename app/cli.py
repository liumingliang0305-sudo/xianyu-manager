"""业务命令行。

uv run xym accounts check                       检查各账号登录态，会话过期时先试免密续期
uv run xym accounts unblock [-y]                手动解除风控熔断（先在闲鱼 App 完成验证）
uv run xym items sync [--stats]                 从平台同步我的商品到本地库
uv run xym items list [--all] [--status sold]   看本地库里的商品
uv run xym items show <商品ID>                   拉一次实时详情
uv run xym items price <商品ID> <售价> [--orig 原价] [--force] [-y]
uv run xym items desc <商品ID> <文本文件> [-y]
uv run xym items down <商品ID> [-y]              下架
uv run xym items delete <商品ID> [-y]            删除，平台上不可恢复
uv run xym items log [商品ID]                     本系统做过的改动
uv run xym orders add <订单号或链接>              添加一笔卖出的订单（App「我卖出的」里复制）
uv run xym orders sync                          刷新本地还没结束的订单（专业卖家会整页同步）
uv run xym orders list [--status to_ship]       看本地库里的订单
uv run xym orders show <订单ID>                  拉一次实时详情
uv run xym orders price <订单ID> [项=金额 ...] [--force] [-y]
                                                不带金额时列出能改的价格项
uv run xym orders log [订单ID]                    本系统对订单做过的改动
uv run xym orders remove <订单ID> [-y]           从本地列表移除，不动平台上的订单
uv run xym messages list [--all]                看本地库里的会话（消息由 xym serve 在后台收）
uv run xym messages show <会话ID> [-n 30]        看一个会话最近的消息
uv run xym serve [--port 8000]                  启动管理后台，只监听本机
uv run xym import-sqlite [data/xianyu.db]       一次性：把以前 SQLite 库里的数据导进 PostgreSQL

有多个账号时用 --account <unb> 指定。改动类命令会先确认，-y 跳过确认。
数据库连接串取环境变量 DATABASE_URL（或 --db），没设时连本机 docker 里的（docker compose up -d）。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING

import psycopg

from app import db
from app.accounts import (
    DEFAULT_DIR,
    GUARD_PATH,
    AccountNotFoundError,
    Accounts,
    LoginState,
    LoginStatus,
    make_guard,
)
from app.items import ItemRecord, ItemService, PriceGuardError
from app.messages import ConversationRecord, MessageStore
from app.orders import OrderRecord, OrderService
from xianyu_core.clock import beijing_time
from xianyu_core.errors import CircuitOpenError, XianyuError
from xianyu_core.item import cent_to_yuan, status_text, yuan_to_cent
from xianyu_core.order import STATUS_TEXT as ORDER_STATUS_TEXT

if TYPE_CHECKING:
    from fastapi import FastAPI

STATUS_ARG = {"online": 0, "sold": 1}
LOGIN_TEXT = {
    LoginState.VALID: "有效",
    LoginState.EXPIRED: "已失效",
    LoginState.RISK: "触发风控",
    LoginState.ERROR: "没查出来",
}


def _yuan(cents: int | None) -> str:
    return "-" if cents is None else f"¥{cent_to_yuan(cents)}"


def _count(value: int | None) -> str:
    return "-" if value is None else str(value)


def _time(ts: float | None) -> str:
    return beijing_time(ts) if ts else "-"


def _confirm(question: str, yes: bool) -> bool:
    if yes:
        return True
    if not sys.stdin.isatty():
        print(f"{question}\n不是交互终端，加 -y 确认后再执行。", file=sys.stderr)
        return False
    return input(f"{question} [y/N] ").strip().lower() in ("y", "yes")


def _label(service: ItemService, account: str, item_id: str) -> str:
    """确认提示里显示的商品名：本地库里有就带上标题。"""
    record = service.get(account, item_id)
    return f"「{record.title[:20]}」({item_id})" if record else item_id


def _print_items(items: list[ItemRecord]) -> None:
    if not items:
        print("本地库里没有商品，先跑一次 xym items sync")
        return
    print(f"{'商品ID':<14} {'状态':<4} {'售价':>8} {'想要':>4} {'浏览':>4} {'收藏':>4}  标题")
    for it in items:
        state = status_text(it.status) if it.on_platform else "已下架"
        print(
            f"{it.item_id:<14} {state:<4} {_yuan(it.price_cent):>8} {_count(it.want_count):>4}"
            f" {_count(it.browse_count):>4} {_count(it.collect_count):>4}  {it.title[:30]}"
        )


def _print_orders(orders: list[OrderRecord]) -> None:
    if not orders:
        print("本地库里没有订单，先跑一次 xym orders sync")
        return
    print(f"{'订单ID':<20} {'状态':<5} {'金额':>8}  {'下单时间':<16}  商品")
    for o in orders:
        state = ORDER_STATUS_TEXT.get(o.status, o.status)
        title = o.title[:24] or o.item_id
        print(
            f"{o.order_id:<20} {state:<5} {_yuan(o.price_cent):>8}"
            f"  {_time(o.created_at):<16}  {title}"
        )


def _print_conversations(conversations: list[ConversationRecord]) -> None:
    if not conversations:
        print("本地库里没有会话。消息由 xym serve 在后台收，先启动一次管理后台")
        return
    print(f"{'会话ID':<14} {'未读':>4}  {'最后动静':<16}  {'对方':<10}  最后一条")
    for c in conversations:
        peer = c.peer_name or c.peer_id or "-"
        last = c.last_text.replace("\n", " ")[:30] or c.item_title[:20]
        when = _time(c.updated_at)
        print(f"{c.cid:<14} {c.unread or '':>4}  {when:<16}  {peer[:10]:<10}  {last}")


def _parse_fees(pairs: list[str]) -> dict[str, int]:
    """["modifyFee=99", "newTransportFee=0"] → {"modifyFee": 9900, "newTransportFee": 0}"""
    fees = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key.strip():
            raise ValueError(f"价格要写成 项=金额，比如 modifyFee=99：{pair!r}")
        fees[key.strip()] = yuan_to_cent(value)
    return fees


async def run(args: argparse.Namespace) -> int:
    if args.command == "accounts":
        if args.accounts_command == "unblock":
            return unblock_account(args)
        return await check_accounts(args)
    if args.command == "orders":
        return await run_orders(args)
    if args.command == "messages":
        return run_messages(args)
    if args.command == "import-sqlite":
        return run_import(args)
    return await run_items(args)


def run_import(args: argparse.Namespace) -> int:
    from app.sqlite_import import import_sqlite

    counts = import_sqlite(args.path, db.connect(args.db))
    for table, n in counts.items():
        print(f"{table:<14}{n} 行")
    print(f"导完了。确认管理后台里数据都在以后，{args.path} 可以删掉")
    return 0


def run_messages(args: argparse.Namespace) -> int:
    """只读本地库，不连平台。"""
    account = Accounts(args.accounts).resolve(args.account)
    store = MessageStore(db.connect(args.db))
    if args.messages_command == "list":
        conversations = store.conversations(account)
        if not args.all:
            conversations = [c for c in conversations if c.biz_type == "1"]
        _print_conversations(conversations)
        return 0

    conversation = store.conversation(account, args.cid)
    if conversation is None:
        print(f"本地没有会话 {args.cid}，用 xym messages list 看有哪些", file=sys.stderr)
        return 1
    records, more = store.messages(account, args.cid, limit=args.n)
    peer = conversation.peer_name or conversation.peer_id or "对方"
    print(f"{peer}  {conversation.item_title or conversation.item_id}")
    if more:
        print(f"（只显示最新 {args.n} 条，-n 可以看更多）")
    for m in records:
        who = "我" if m.mine else (m.sender_name or peer)
        print(f"{_time(m.sent_at)}  {who}：{m.text}")
    if conversation.stale:
        print("\n平台上还有本地没拉到的消息，在管理后台打开这个会话会补拉")
    return 0


def unblock_account(args: argparse.Namespace) -> int:
    guard = make_guard(args.guard)
    account = Accounts(args.accounts, guard=guard).resolve(args.account)
    block = guard.block(account)
    if not block:
        print(f"账号 {account} 没有在熔断期")
        return 0
    print(f"账号 {account} 熔断中：{_time(block.since)} 起，{_time(block.until)} 自动恢复")
    print(f"原因：{block.reason}")
    if not _confirm("确认已经在闲鱼 App 或网页上完成验证，现在解除？", args.yes):
        return 1
    guard.unblock(account)
    print("已解除")
    return 0


async def run_orders(args: argparse.Namespace) -> int:
    accounts = Accounts(args.accounts, guard=make_guard(args.guard))
    account = accounts.resolve(args.account)
    service = OrderService(db.connect(args.db), accounts)
    cmd = args.orders_command

    if cmd == "add":
        record, added = await service.add(account, args.order)
        state = ORDER_STATUS_TEXT.get(record.status, record.status)
        print(f"{'已添加' if added else '已在列表里，刷新了一次'}：{record.order_id}")
        print(f"{state}  {_yuan(record.price_cent)}  {record.title}")
    elif cmd == "remove":
        if not _confirm(f"从本地列表移除订单 {args.order_id}？平台上的订单不受影响。", args.yes):
            return 1
        if not service.remove(account, args.order_id):
            print(f"本地没有订单 {args.order_id}", file=sys.stderr)
            return 1
        print("已移除")
    elif cmd == "sync":
        result = await service.sync(account)
        if result.source == "detail":
            print(
                f"这个账号没有卖出订单列表的权限（只对闲鱼专业卖家开放），"
                f"刷新了本地 {len(result.updated) + len(result.failed)} 笔还没结束的订单："
                f"状态有变化 {len(result.changed)}，失败 {len(result.failed)}"
            )
            print("新订单用 xym orders add <订单号> 添加")
        else:
            print(
                f"同步完成：新增 {len(result.added)}，更新 {len(result.updated)}，"
                f"状态有变化 {len(result.changed)}"
            )
        _print_orders(service.list(account)[:20])
    elif cmd == "list":
        _print_orders(service.list(account, status=args.status))
    elif cmd == "show":
        d = await service.fetch_detail(account, args.order_id)
        print(f"{d.status_title or ORDER_STATUS_TEXT[d.status]}")
        if d.status_desc:
            print(d.status_desc)
        print(f"\n{d.title}{f'（{d.sku}）' if d.sku else ''} x{d.quantity}")
        print(f"{d.amount_title or '金额'}  {_yuan(d.amount_cent)}")
        for bill in d.bills:
            print(f"  {bill['title']}  {_yuan(bill['cent'])}")
        if d.income_cent is not None:
            print(f"{d.income_title or '到手'}  {_yuan(d.income_cent)}")
        if d.steps:
            print()
            for step in d.steps:
                mark = "✓" if step["done"] else "·"
                print(f"{mark} {step['title']:<6} {_time(step['time']) if step['time'] else ''}")
        if d.receiver_name or d.receiver_address:
            print(f"\n收货人  {d.receiver_name} {d.receiver_phone}")
            print(f"地址    {d.receiver_address}")
        print()
        for f in d.fields:
            print(f"{f['title']}  {f['value']}")
        if d.actions:
            print(f"\n平台上可以做的：{'、'.join(d.actions)}")
    elif cmd == "price":
        fields = await service.price_fields(account, args.order_id)
        if not args.fees:
            if not fields:
                print("这个订单现在不能改价，只有待付款的订单可以")
                return 1
            for f in fields:
                print(f"{f.key:<20} {f.name:<8} 当前 {_yuan(f.price_cent)}")
            print("\n改价：xym orders price <订单ID> 项=金额 ...，没写的项保持原值")
            return 0
        fees = _parse_fees(args.fees)
        names = {f.key: f.name for f in fields}
        changes = "、".join(f"{names.get(k, k)}改成 {_yuan(v)}" for k, v in fees.items())
        if not _confirm(f"订单 {args.order_id}：{changes}？", args.yes):
            return 1
        try:
            old, new = await service.change_price(account, args.order_id, fees, force=args.force)
        except PriceGuardError as exc:
            raise PriceGuardError(f"{exc}，确认没输错请加 --force") from exc
        print(f"已改价：订单总价 {_yuan(old)} → {_yuan(new)}")
    elif cmd == "log":
        ops = service.history(account, args.order_id)
        if not ops:
            print("还没有改动记录")
        for op in ops:
            when = beijing_time(op.created_at, "%Y-%m-%d %H:%M:%S")
            result = "成功" if op.ok else f"失败：{op.error}"
            change = ""
            if op.action == "price":
                change = f" {_yuan(op.detail.get('from'))} → {_yuan(op.detail.get('to'))}"
            print(f"{when}  {op.order_id}  {op.action}{change}  {result}")
    return 0


async def run_items(args: argparse.Namespace) -> int:
    accounts = Accounts(args.accounts, guard=make_guard(args.guard))
    account = accounts.resolve(args.account)
    service = ItemService(db.connect(args.db), accounts)
    cmd = args.items_command

    if cmd == "sync":
        result = await service.sync(account, with_stats=args.stats)
        print(
            f"同步完成：新增 {len(result.added)}，更新 {len(result.updated)}，"
            f"不在主页了 {len(result.gone)}"
        )
        _print_items(service.list(account))
    elif cmd == "list":
        status = STATUS_ARG.get(args.status) if args.status else None
        _print_items(service.list(account, status=status, include_gone=args.all))
    elif cmd == "show":
        d = await service.fetch_detail(account, args.item_id)
        created = _time(d.created_at)
        print(f"{d.title}\n")
        print(f"商品ID  {d.item_id}")
        print(f"状态    {d.status_text or status_text(d.status)}")
        orig = f"（原价 {_yuan(d.orig_price_cent)}）" if d.orig_price_cent else ""
        print(f"售价    {_yuan(d.price_cent)}{orig}")
        print(f"库存    {d.quantity}")
        print(f"浏览 {d.browse_count} · 想要 {d.want_count} · 收藏 {d.collect_count}")
        print(f"发布于  {created}")
        print(f"图片    {len(d.image_urls)} 张")
        print(f"\n{d.desc}")
    elif cmd == "price":
        price = yuan_to_cent(args.price)
        orig = yuan_to_cent(args.orig) if args.orig else None
        question = f"把 {_label(service, account, args.item_id)} 的售价改成 {_yuan(price)}"
        if orig is not None:
            question += f"、原价改成 {_yuan(orig)}"
        if not _confirm(question + "？", args.yes):
            return 1
        try:
            old = await service.change_price(
                account, args.item_id, price, orig_price_cent=orig, force=args.force
            )
        except PriceGuardError as exc:
            raise PriceGuardError(f"{exc}，确认没输错请加 --force") from exc
        print(f"已改价：{_yuan(old)} → {_yuan(price)}")
    elif cmd == "desc":
        text = Path(args.file).read_text(encoding="utf-8").strip()
        preview = text if len(text) <= 60 else text[:60] + "…"
        question = f"把 {_label(service, account, args.item_id)} 的描述改成：\n{preview}\n确认？"
        if not _confirm(question, args.yes):
            return 1
        await service.change_desc(account, args.item_id, text)
        print("描述已更新，标题由平台从描述里截取，下次同步时更新")
    elif cmd == "down":
        if not _confirm(f"下架 {_label(service, account, args.item_id)}？", args.yes):
            return 1
        await service.downshelf(account, args.item_id)
        print("已下架")
    elif cmd == "delete":
        question = f"删除 {_label(service, account, args.item_id)}？删除后平台上不可恢复。"
        if not _confirm(question, args.yes):
            return 1
        await service.delete(account, args.item_id)
        print("已删除")
    elif cmd == "log":
        ops = service.history(account, args.item_id)
        if not ops:
            print("还没有改动记录")
        for op in ops:
            when = beijing_time(op.created_at, "%Y-%m-%d %H:%M:%S")
            result = "成功" if op.ok else f"失败：{op.error}"
            change = ""
            if op.action == "price":
                change = f" {_yuan(op.detail.get('from'))} → {_yuan(op.detail.get('to'))}"
            print(f"{when}  {op.item_id}  {op.action}{change}  {result}")
    return 0


def _print_login(account_id: str, status: LoginStatus) -> None:
    note = "（刚做了免密续期）" if status.renewed else ""
    print(f"{account_id}  {LOGIN_TEXT[status.state]}{note}")
    if status.keep_login_until:
        print(f"  保持登录到 {beijing_time(status.keep_login_until)}")
    else:
        print("  没有开启保持登录，会话过期后要重新扫码")
    if status.message:
        print(f"  {status.message}")
    if status.state is LoginState.EXPIRED:
        print("  运行 uv run xianyu login 重新扫码")
    elif status.state is LoginState.RISK:
        print("  先暂停操作，到闲鱼 App 或网页完成验证后再检查")


async def check_accounts(args: argparse.Namespace) -> int:
    accounts = Accounts(args.accounts, guard=make_guard(args.guard))
    ids = [accounts.resolve(args.account)] if args.account else accounts.ids()
    if not ids:
        raise AccountNotFoundError(f"{accounts.root} 下没有账号，先用 xianyu login 登录")
    ok = True
    for account_id in ids:
        status = await accounts.check(account_id)
        _print_login(account_id, status)
        ok = ok and status.state is LoginState.VALID
    return 0 if ok else 1


def serve(args: argparse.Namespace) -> int:
    from app.api import WEB_DIST, create_app
    from app.keepalive import LOGIN_CHECK_INTERVAL

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"警告：接口没有登录鉴权，监听 {args.host} 意味着别人也能操作你的闲鱼账号",
            file=sys.stderr,
        )
    if not (WEB_DIST / "index.html").is_file():
        print("没有找到 web/dist，只启动接口。前端开发用 pnpm -C web dev", file=sys.stderr)
    db.connect(args.db).close()  # 连不上、表结构不是最新的，启动前就报出来
    app = create_app(
        database_url=args.db,
        guard=make_guard(args.guard),
        accounts_dir=args.accounts,
        login_check_interval=LOGIN_CHECK_INTERVAL,
        messages=True,
    )
    run_server(app, args.host, args.port)
    return 0


def run_server(app: FastAPI, host: str, port: int) -> None:
    """打开着的消息页面占着一条推送流（SSE），uvicorn 退出时要等它结束。
    Ctrl+C 时先让推送流都结束；万一还有没结束的，最多等 3 秒。"""
    import uvicorn

    class Server(uvicorn.Server):
        def handle_exit(self, sig: int, frame: FrameType | None) -> None:
            hub = getattr(app.state, "messages", None)
            if hub:
                hub.close_streams()
            super().handle_exit(sig, frame)

    config = uvicorn.Config(app, host=host, port=port, timeout_graceful_shutdown=3)
    Server(config).run()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xym", description="闲鱼管理系统")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    parser.add_argument("--account", help="账号 unb，只有一个账号时可省略")
    parser.add_argument("--accounts", type=Path, default=DEFAULT_DIR, help="凭证目录")
    parser.add_argument("--db", help="数据库连接串，默认取环境变量 DATABASE_URL")
    parser.add_argument("--guard", type=Path, default=GUARD_PATH, help="熔断状态文件")
    sub = parser.add_subparsers(dest="command", required=True)

    accounts = sub.add_parser("accounts", help="账号").add_subparsers(
        dest="accounts_command", required=True
    )
    accounts.add_parser("check", help="检查登录态，会话过期时先试免密续期")
    p = accounts.add_parser("unblock", help="手动解除风控熔断")
    p.add_argument("-y", "--yes", action="store_true", help="不再确认")

    items = sub.add_parser("items", help="商品管理").add_subparsers(
        dest="items_command", required=True
    )
    p = items.add_parser("sync", help="从平台同步我的商品")
    p.add_argument("--stats", action="store_true", help="逐个拉详情，补上浏览和收藏数")

    p = items.add_parser("list", help="看本地库里的商品")
    p.add_argument("--all", action="store_true", help="包括已经不在主页上的")
    p.add_argument("--status", choices=sorted(STATUS_ARG), help="只看在售或已卖出")

    p = items.add_parser("show", help="拉一次实时详情")
    p.add_argument("item_id")

    p = items.add_parser("price", help="改价")
    p.add_argument("item_id")
    p.add_argument("price", help="新售价，单位元")
    p.add_argument("--orig", help="同时改原价，单位元")
    p.add_argument("--force", action="store_true", help="降价超过一半或涨价超过一倍时需要")
    p.add_argument("-y", "--yes", action="store_true", help="不再确认")

    p = items.add_parser("desc", help="改描述")
    p.add_argument("item_id")
    p.add_argument("file", help="新描述所在的文本文件")
    p.add_argument("-y", "--yes", action="store_true", help="不再确认")

    for name, help_text in (("down", "下架"), ("delete", "删除（不可恢复）")):
        p = items.add_parser(name, help=help_text)
        p.add_argument("item_id")
        p.add_argument("-y", "--yes", action="store_true", help="不再确认")

    p = items.add_parser("log", help="本系统做过的改动")
    p.add_argument("item_id", nargs="?")

    orders = sub.add_parser("orders", help="订单管理（卖出的）").add_subparsers(
        dest="orders_command", required=True
    )
    p = orders.add_parser("add", help="添加一笔卖出的订单")
    p.add_argument("order", help="订单号，或者订单详情页的链接")

    p = orders.add_parser("remove", help="从本地列表移除（不动平台上的订单）")
    p.add_argument("order_id")
    p.add_argument("-y", "--yes", action="store_true", help="不再确认")

    orders.add_parser("sync", help="刷新还没结束的订单（专业卖家整页同步）")

    p = orders.add_parser("list", help="看本地库里的订单")
    p.add_argument("--status", choices=sorted(ORDER_STATUS_TEXT), help="只看某个状态")

    p = orders.add_parser("show", help="拉一次实时详情")
    p.add_argument("order_id")

    p = orders.add_parser("price", help="给待付款订单改价")
    p.add_argument("order_id")
    p.add_argument("fees", nargs="*", metavar="项=金额", help="比如 modifyFee=99，单位元")
    p.add_argument("--force", action="store_true", help="总价降一半以上或涨一倍以上时需要")
    p.add_argument("-y", "--yes", action="store_true", help="不再确认")

    p = orders.add_parser("log", help="本系统对订单做过的改动")
    p.add_argument("order_id", nargs="?")

    messages = sub.add_parser("messages", help="消息（只读本地库）").add_subparsers(
        dest="messages_command", required=True
    )
    p = messages.add_parser("list", help="看本地库里的会话")
    p.add_argument("--all", action="store_true", help="包括系统通知之类的非商品会话")

    p = messages.add_parser("show", help="看一个会话最近的消息")
    p.add_argument("cid", help="会话ID，xym messages list 里第一列")
    p.add_argument("-n", type=int, default=30, help="显示多少条，默认 30")

    p = sub.add_parser("import-sqlite", help="一次性：把以前 SQLite 库里的数据导进 PostgreSQL")
    p.add_argument("path", nargs="?", type=Path, default=Path("data/xianyu.db"))

    p = sub.add_parser("serve", help="启动管理后台（HTTP 接口 + 打包好的前端）")
    p.add_argument("--host", default="127.0.0.1", help="接口没有登录鉴权，不要监听公网地址")
    p.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.command == "serve":
            return serve(args)
        return asyncio.run(run(args))
    except (XianyuError, AccountNotFoundError, ValueError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        if isinstance(exc, CircuitOpenError):
            print(
                "先到闲鱼 App 或网页完成验证；完成后可以用 xym accounts unblock 提前解除",
                file=sys.stderr,
            )
        return 1
    except (db.SchemaOutdatedError, psycopg.OperationalError) as exc:
        print(f"数据库：{exc}", file=sys.stderr)
        if isinstance(exc, psycopg.OperationalError):
            print("本机开发先启动数据库：docker compose up -d", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
