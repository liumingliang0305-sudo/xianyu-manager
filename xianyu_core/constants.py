"""闲鱼网页端的固定参数。平台改版时优先检查这里。"""

APP_KEY = "34839810"  # 网页端 mtop 的 appKey

WEB_HOST = "https://www.goofish.com"
MTOP_HOST = "https://h5api.m.goofish.com"
PASSPORT_HOST = "https://passport.goofish.com"

# 登录 cookie 都种在这个域上，h5api / passport / www 共用
COOKIE_DOMAIN = ".goofish.com"

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)

BROWSER_HEADERS = {
    "user-agent": USER_AGENT,
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "sec-ch-ua": '"Chromium";v="146", "Not-A.Brand";v="24", "Google Chrome";v="146"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
}

# 判定"已登录"至少要有的 cookie：unb 是用户 ID，cookie2 是会话，_m_h5_tk 用来签名
LOGIN_COOKIES = ("unb", "cookie2", "_m_h5_tk")
