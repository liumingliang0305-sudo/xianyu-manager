"""命令行：只剩启动管理后台和管理后台的用户，别的都在页面上做。

uv run xym serve [--port 8000]                  启动管理后台，只监听本机
uv run xym users add <用户名>                    建管理后台的用户（提示输入密码）
                                                第一个用户顺带接手已有的闲鱼账号
uv run xym users list                           每个用户名下的闲鱼账号
uv run xym users passwd <用户名>                 重设密码，这个用户已经登录的地方都要重新登录
uv run xym users disable|enable <用户名>         停用 / 恢复

部署在服务器上时用 deploy/xym.sh 在服务器上跑。
数据库连接串取环境变量 DATABASE_URL（或 --db），没设时连本机 docker 里的（docker compose up -d）。
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import logging
import sys
from pathlib import Path
from types import FrameType
from typing import TYPE_CHECKING

import psycopg

from app import db
from app.accounts import DEFAULT_DIR, GUARD_PATH, Accounts, make_guard
from app.users import Users, normalize_username

if TYPE_CHECKING:
    from fastapi import FastAPI


def _read_password(prompt: str = "密码") -> str:
    """终端里输两遍，不回显；不是终端时从标准输入读一行（脚本里用）。"""
    if not sys.stdin.isatty():
        return sys.stdin.readline().rstrip("\n")
    password = getpass.getpass(f"{prompt}：")
    if getpass.getpass("再输一遍：") != password:
        raise ValueError("两次输的密码不一样")
    return password


async def run_users(args: argparse.Namespace) -> int:
    users = Users(db.connect(args.db))
    if args.users_command == "list":
        rows = users.list()
        if not rows:
            print("还没有用户，用 xym users add <用户名> 建一个")
            return 0
        print(f"{'用户':<16} {'状态':<4}  闲鱼账号")
        for user in rows:
            owned = ", ".join(sorted(users.account_ids(user.id))) or "-"
            print(f"{user.username:<16} {'停用' if user.disabled else '正常':<4}  {owned}")
        return 0
    if args.users_command == "add":
        name = normalize_username(args.username)
        if users.find(name):
            raise ValueError(f"用户 {name} 已经有了")
        first = not users.list()
        user = await users.create(name, _read_password())
        print(f"已建用户 {user.username}")
        if first:
            # 有用户以前登录的闲鱼账号都没有归属，谁都看不到，交给第一个用户
            ownerless = [a for a in Accounts(args.accounts).ids() if users.owner(a) is None]
            for account_id in ownerless:
                users.assign(account_id, user.id)
            if ownerless:
                print(f"已有的闲鱼账号归到 {user.username} 名下：{', '.join(ownerless)}")
        return 0

    user = users.find(args.username)
    if user is None:
        raise ValueError(f"没有用户 {args.username}")
    if args.users_command == "passwd":
        await users.set_password(user.id, _read_password("新密码"))
        print(f"{user.username} 的密码改好了，已经登录的地方要重新登录")
    elif args.users_command == "disable":
        users.set_disabled(user.id, True)
        print(f"已停用 {user.username}：登录不了，已经登录的地方马上失效，插件令牌也不认了")
    else:
        users.set_disabled(user.id, False)
        print(f"已恢复 {user.username}")
    return 0


def serve(args: argparse.Namespace) -> int:
    from app import places
    from app.api import WEB_DIST, create_app
    from app.keepalive import LOGIN_CHECK_INTERVAL

    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(
            f"警告：接口是明文 http，监听 {args.host} 时密码、令牌、闲鱼 cookie 都在网络上明文传，"
            "对外开要放在 HTTPS 反向代理后面",
            file=sys.stderr,
        )
    if not (WEB_DIST / "index.html").is_file():
        print("没有找到 web/dist，只启动接口。前端开发用 pnpm -C web dev", file=sys.stderr)
    conn = db.connect(args.db)  # 连不上、表结构不是最新的，启动前就报出来
    if not Users(conn).list():
        print("还没有用户，页面上登录不了，先运行 xym users add <用户名>", file=sys.stderr)
    conn.close()
    app = create_app(
        database_url=args.db,
        guard=make_guard(args.guard),
        accounts_dir=args.accounts,
        login_check_interval=LOGIN_CHECK_INTERVAL,
        messages=True,
        amap_key=places.load_key(),
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
    parser.add_argument("--accounts", type=Path, default=DEFAULT_DIR, help="凭证目录")
    parser.add_argument("--db", help="数据库连接串，默认取环境变量 DATABASE_URL")
    parser.add_argument("--guard", type=Path, default=GUARD_PATH, help="熔断状态文件")
    sub = parser.add_subparsers(dest="command", required=True)

    users = sub.add_parser("users", help="管理后台的用户").add_subparsers(
        dest="users_command", required=True
    )
    users.add_parser("list", help="每个用户名下的闲鱼账号")
    for name, help_text in (
        ("add", "建用户，提示输入密码；第一个用户顺带接手已有的闲鱼账号"),
        ("passwd", "重设密码"),
        ("disable", "停用"),
        ("enable", "恢复停用的用户"),
    ):
        users.add_parser(name, help=help_text).add_argument("username")

    p = sub.add_parser("serve", help="启动管理后台（HTTP 接口 + 打包好的前端）")
    p.add_argument("--host", default="127.0.0.1", help="明文 http，不要直接监听公网地址")
    p.add_argument("--port", type=int, default=8000)

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.command == "serve":
            return serve(args)
        return asyncio.run(run_users(args))
    except ValueError as exc:
        print(f"失败：{exc}", file=sys.stderr)
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
