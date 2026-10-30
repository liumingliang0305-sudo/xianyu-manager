# 以后要做

只是记下来，没说要做之前不动。每一项写清楚想要什么、现在是什么样、动手前要先定的事。

## 1. 设备指纹，降低风控

**想要的**：请求带上设备信息，看起来更像真人在用，少碰风控；最好扫码登录也能开保持登录。

**现在**：
- 纯 HTTP 扫码登录带的是登录页给的 `umidToken` 加 `umidTag=SERVER`，没有浏览器 um.js 采集的设备信息。
  2026-10-30 实测同一个账号在浏览器里能开保持登录、扫码登录不给开，差别很可能就在这里
  （见[协议笔记](protocol-notes.md#扫码登录时的保持登录状态弹窗)）；
- mtop 请求也没有设备相关的参数，只有请求头像浏览器；
- 到现在一次风控都没碰到过（[docs/guard.md](guard.md)）。

**开源项目怎么做**（2026-10-30 翻 10-28 克隆的 11 个项目，见[调研](open-source-research.md)）：
- 纯 HTTP 扫码的项目（Ydisks、zhinianboke、xianyu-auto-reply-fix、myfish、goofish-client、xianyu-pilot、XianyuAutoAgent）
  都填 `umidTag=SERVER`，和我们一样，没有一个在纯 HTTP 里模拟 um.js。扫码登录开不了保持登录应该是大家共同的问题；
- 需要浏览器环境的环节交给真浏览器：
  - Ydisks 用自带的 Chromium 读一次真实的 UA、`sec-ch-ua`、平台，让 HTTP 请求头和它一致；官网静默续期在 Chromium 里做；
  - goofish-cli 主要用 `browser_cookie3` 从本机 Chrome 读 cookie；要开浏览器时用系统 Chrome，不用 Playwright 自带的
    Chromium（特征太"裸"），而且开窗口，作者实测无头模式会被判"非法访问"；
- 共识是**不伪造、不随机指纹**：Ydisks 的代码注释说随机改 canvas、WebGL 会让同一个账号每次续期都像换了设备，
  本身就是强风险信号；zhinianboke 的浏览器特征脚本也说伪造的特征互相矛盾，比只隐藏自动化标记更容易触发风控；
- 风控恢复（goofish-cli 的说明）：在浏览器里手动登录，正常逛 3～5 分钟，让 `x5sec`（风控通行证）、`isg`、`l`、
  `mtop_partitioned_detect` 下发，再把 cookie 导过来。我们的插件同步 goofish.com 下全部 cookie，这几个都会带上；
- 同一个 IP 或同一个浏览器 profile 登多个号会被判关联账号；频繁重新申请登录凭证本身会触发风控（Ydisks README）；
- 分歧在自动过滑块：Ydisks、zhinianboke 用 Playwright 自动过（带鼠标轨迹），goofish-cli 和我们交给人工。
  自动过滑块是平台重点打击的行为，不做。

**可以做的第一步**：后台请求的浏览器标识和用户真实的 Chrome 一致（Ydisks 的思路）。现在 `xianyu_core/constants.py`
写死 Chrome 146 的 UA 和 `sec-ch-ua`，登录 cookie 却来自用户自己的 Chrome，版本对不上时同一份登录像来自两个浏览器。
插件同步时顺带发 `navigator.userAgent` 和 `navigator.userAgentData` 里的品牌、平台，后台按账号存下来、请求时用它；
没有时退回写死的值。不编造任何东西，只是两边一致。

**动手前要先定的**：
- **边界**：自己编造或模拟设备指纹（照着 um.js 算、生成假的设备参数、随机改 canvas / WebGL）是在绕平台的风控检测，
  之前定过不做：被识别出来风控只会更严，也违反平台规则。能做的方向是用真浏览器自己的环境，比如需要设备信息的环节
  交给真浏览器做（现在的登录同步插件就是这条路），或者沿用浏览器里已有的标识，不每次新生成；
- **先确认风控和设备有关**：碰到第一次风控时记下当时在做什么、频率多高、返回了什么，再判断该改限流还是该改设备信息。

## 2. 登录同步插件推到远程服务器

**想要的**：插件能把登录推给不在本机的管理后台，比如部署在云服务器上的。

**现在**：
- 插件只允许推给 `http://127.0.0.1` 和 `http://localhost`（`extension/lib.js` 的 `normalizeServer`），
  `manifest.json` 的 `host_permissions` 也只有本机；
- 管理后台没有登录鉴权，只监听本机；`xym serve --host` 改成对外监听时只打一句警告。

**要做的**（cookie 等于账号密码，下面几条缺一不可）：
- 后台加鉴权，至少一个只有自己知道的令牌，插件推送时带上；有鉴权以后才允许对外监听，没配令牌就拒绝启动；
- 远程地址只允许 `https`，明文 http 传 cookie 等于公开；
- 插件改用 `optional_host_permissions`，填了远程地址时再向用户申请那个域名的权限，不默认要所有网站的权限；
- 插件弹窗里能填地址和令牌，令牌存 `chrome.storage.local`；
- 远程后台被别人拿到令牌就能操作账号，README 里写清楚风险和令牌怎么换。
