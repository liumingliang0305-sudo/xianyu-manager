"""调试用的命令行入口。

uv run xianyu login              扫码登录，凭证存到 data/accounts/<unb>.json
uv run xianyu whoami <凭证文件>   用凭证调一次接口，确认登录态还有效（会话过期时先试免密续期）
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import subprocess
import sys
import time
from pathlib import Path

from xianyu_core.auth import QRLogin, QRStatus
from xianyu_core.credential import Credential
from xianyu_core.errors import AuthRequiredError, VerificationRequiredError, XianyuError
from xianyu_core.mtop import MtopClient

DEFAULT_DIR = Path("data/accounts")
# 实测（2026-10-28）：loginuser.get 只返回 userId，
# 昵称要从 user.page.head 的 module.base.displayName 取
LOGIN_USER_API = "mtop.taobao.idlemessage.pc.loginuser.get"
USER_HEAD_API = "mtop.idle.web.user.page.head"

STATUS_TEXT = {
    QRStatus.NEW: "等待扫码…",
    QRStatus.SCANNED: "已扫码，请在手机上确认登录",
    QRStatus.CONFIRMED: "已确认，正在获取登录凭证…",
}


def _mask(value: str) -> str:
    return value if len(value) <= 4 else f"{value[:2]}***{value[-2:]}"


def _keep_login_text(credential: Credential) -> str:
    until = credential.keep_login_until
    if not until:
        return "没有开启保持登录，会话过期后要重新扫码"
    return f"保持登录到 {time.strftime('%Y-%m-%d %H:%M', time.localtime(until))}"


async def _whoami(credential: Credential) -> tuple[str, bool]:
    """确认登录态有效且属于这个账号，返回昵称和这次有没有做免密续期。

    平台在响应里续期的 cookie 会更新到 credential 上，出错时也一样，调用方记得存回去。
    """
    async with MtopClient(credential) as mtop:
        try:
            user = await mtop.call(LOGIN_USER_API)
            if str(user.get("userId", "")) != credential.unb:
                raise AuthRequiredError("登录态对应的账号和凭证里的 unb 不一致")
            head = await mtop.call(USER_HEAD_API, {"self": True})
        finally:
            credential.cookies = mtop.credential.cookies
        nick = ((head.get("module") or {}).get("base") or {}).get("displayName") or ""
        return nick, bool(mtop.renewal and mtop.renewal.ok)


async def login(out_dir: Path, *, invert: bool, open_image: bool) -> int:
    async with QRLogin() as session:
        qr = await session.start()
        out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        svg_path = out_dir / "login-qrcode.svg"
        svg_path.write_bytes(qr.to_svg())

        print(qr.to_ascii(invert=invert))
        print(f"用闲鱼 App 扫上面的二维码。扫不出来就打开图片：{svg_path}")
        print("（终端是浅色背景的话，加 --no-invert 再试）\n")
        if open_image and sys.platform == "darwin":
            subprocess.run(["open", str(svg_path)], check=False)

        try:
            credential = await session.wait(
                on_status=lambda s: print(STATUS_TEXT.get(s, s.value), flush=True)
            )
        except VerificationRequiredError as exc:
            print(f"\n平台要求额外身份验证：{exc.verification_url or '（没有给出验证地址）'}")
            print("请先在电脑浏览器打开 goofish.com 正常登录一次、完成验证，再重新扫码。")
            return 2
        finally:
            svg_path.unlink(missing_ok=True)
        keep_login = session.keep_login

    # 先落盘，再验证：验证失败也不会丢掉刚拿到的凭证
    path = credential.save(out_dir / f"{credential.unb}.json")
    missing = credential.missing_login_cookies()
    if missing:
        print(f"注意：凭证里缺少 {', '.join(missing)}")

    nick, _ = await _whoami(credential)
    credential.save(path)
    print(f"\n登录成功：{nick or '（未返回昵称）'}（unb {_mask(credential.unb)}）")
    if keep_login is None:
        print("注意：开启保持登录时出错，会话过期后要重新扫码（加 -v 看原因）")
    elif not keep_login.enabled and not keep_login.can_open:
        print("注意：平台不给这次登录开保持登录，会话过期后要重新扫码")
    else:
        print(_keep_login_text(credential))
    print(f"凭证已保存到 {path}（含账号 cookie，不要外传）")
    return 0


async def whoami(path: Path) -> int:
    credential = Credential.load(path)
    try:
        nick, renewed = await _whoami(credential)
    finally:
        # 响应里可能续期了 _m_h5_tk、会话 cookie 等，失败时也可能更新了 sdkSilent，都写回去
        credential.save(path)
    note = "（刚做了免密续期）" if renewed else ""
    print(f"登录态有效{note}：{nick or '（未返回昵称）'}（unb {_mask(credential.unb)}）")
    print(_keep_login_text(credential))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xianyu", description="xianyu_core 调试命令")
    parser.add_argument("-v", "--verbose", action="store_true", help="输出调试日志")
    sub = parser.add_subparsers(dest="command", required=True)

    p_login = sub.add_parser("login", help="扫码登录")
    p_login.add_argument("--out", type=Path, default=DEFAULT_DIR, help="凭证保存目录")
    p_login.add_argument(
        "--no-invert", dest="invert", action="store_false", help="浅色背景终端用这个"
    )
    p_login.add_argument(
        "--open", dest="open_image", action="store_true", help="同时用系统看图打开二维码图片"
    )

    p_who = sub.add_parser("whoami", help="检查凭证是否有效")
    p_who.add_argument("credential", type=Path, help="凭证文件路径")

    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )
    try:
        if args.command == "login":
            return asyncio.run(login(args.out, invert=args.invert, open_image=args.open_image))
        return asyncio.run(whoami(args.credential))
    except XianyuError as exc:
        print(f"失败：{exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
