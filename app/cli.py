"""业务命令行。

uv run xym items sync [--stats]                 从平台同步我的商品到本地库
uv run xym items list [--all] [--status sold]   看本地库里的商品
uv run xym items show <商品ID>                   拉一次实时详情
uv run xym items price <商品ID> <售价> [--orig 原价] [--force] [-y]
uv run xym items desc <商品ID> <文本文件> [-y]
uv run xym items down <商品ID> [-y]              下架
uv run xym items delete <商品ID> [-y]            删除，平台上不可恢复
uv run xym items log [商品ID]                     本系统做过的改动

有多个账号时用 --account <unb> 指定。改动类命令会先确认，-y 跳过确认。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from pathlib import Path

from app import db
from app.accounts import DEFAULT_DIR, AccountNotFoundError, Accounts
from app.items import ItemRecord, ItemService
from xianyu_core.errors import XianyuError
from xianyu_core.item import cent_to_yuan, status_text, yuan_to_cent

STATUS_ARG = {"online": 0, "sold": 1}


def _yuan(cents: int | None) -> str:
    return "-" if cents is None else f"¥{cent_to_yuan(cents)}"


def _count(value: int | None) -> str:
    return "-" if value is None else str(value)


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


async def run(args: argparse.Namespace) -> int:
    accounts = Accounts(args.accounts)
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
        created = (
            time.strftime("%Y-%m-%d %H:%M", time.localtime(d.created_at)) if d.created_at else "-"
        )
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
        old = await service.change_price(
            account, args.item_id, price, orig_price_cent=orig, force=args.force
        )
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
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(op.created_at))
            result = "成功" if op.ok else f"失败：{op.error}"
            change = ""
            if op.action == "price":
                change = f" {_yuan(op.detail.get('from'))} → {_yuan(op.detail.get('to'))}"
            print(f"{when}  {op.item_id}  {op.action}{change}  {result}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xym", description="闲鱼管理系统")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    parser.add_argument("--account", help="账号 unb，只有一个账号时可省略")
    parser.add_argument("--accounts", type=Path, default=DEFAULT_DIR, help="凭证目录")
    parser.add_argument("--db", type=Path, default=db.DEFAULT_PATH, help="本地数据库文件")
    sub = parser.add_subparsers(dest="command", required=True)

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

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        return asyncio.run(run(args))
    except (XianyuError, AccountNotFoundError, ValueError) as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
