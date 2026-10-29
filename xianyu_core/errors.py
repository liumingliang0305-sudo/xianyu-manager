"""xianyu_core 的异常。业务层按异常类型分流即可，不需要自己解析平台错误码。"""

from __future__ import annotations

from typing import Any


class XianyuError(Exception):
    """所有底座异常的基类。`ret` 是平台返回的原始错误码列表，`raw` 是完整响应。"""

    def __init__(
        self,
        message: str,
        *,
        ret: list[str] | None = None,
        raw: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.ret = ret or []
        self.raw = raw


class AuthRequiredError(XianyuError):
    """登录态失效（cookie 过期或缺失），需要重新扫码。"""


class RiskControlError(XianyuError):
    """触发风控（RGV587、punish 页、用户验证）。应暂停这个账号的请求，交给人工处理。"""


class SignError(XianyuError):
    """平台判定签名或请求参数非法。"""


class MtopError(XianyuError):
    """其他 mtop 业务错误。"""


class QRLoginError(XianyuError):
    """扫码登录失败：取码失败、二维码过期、用户取消、平台持续报错等。"""

    def __init__(self, message: str, *, status: str = "", **kwargs: Any):
        super().__init__(message, **kwargs)
        self.status = status


class VerificationRequiredError(QRLoginError):
    """扫码确认后平台要求额外身份验证（人脸、短信等），需要人工处理。"""

    def __init__(self, message: str, *, verification_url: str = "", **kwargs: Any):
        super().__init__(message, status="CONFIRMED", **kwargs)
        self.verification_url = verification_url


class RateLimitedError(XianyuError):
    """本地限流：这个账号请求太频繁，请求没有发出去。`retry_after` 秒后再试。"""

    def __init__(self, message: str, *, retry_after: float, **kwargs: Any):
        super().__init__(message, **kwargs)
        self.retry_after = retry_after


class CircuitOpenError(XianyuError):
    """账号触发风控后处在熔断期，请求没有发出去。

    到 `until`（秒级时间戳）自动恢复，也可以手动解除。
    """

    def __init__(self, message: str, *, until: float, reason: str = "", **kwargs: Any):
        super().__init__(message, **kwargs)
        self.until = until
        self.reason = reason
