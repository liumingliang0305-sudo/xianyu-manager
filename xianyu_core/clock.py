"""时间统一按北京时间：闲鱼平台给的、我们显示的都是北京时间，不跟电脑的时区走。"""

from datetime import datetime, timedelta, timezone

BEIJING = timezone(timedelta(hours=8))


def beijing_time(seconds: float, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """秒级时间戳 → 北京时间的字符串"""
    return datetime.fromtimestamp(seconds, BEIJING).strftime(fmt)
