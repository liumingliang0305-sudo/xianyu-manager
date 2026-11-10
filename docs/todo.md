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

## 2. 违规词的后续

**想要的**：写描述以外的地方也提醒违规词（[issue #4](https://github.com/liumingliang0305-sudo/xianyu-manager/issues/4) 第 6 项）。

**现在**：发布页、编辑页的描述有本地词表提醒和平台检查（见[功能说明](features.md#违规词)）。下面这些还没做：
- AI 助手（[app/agent.py](../app/agent.py)）的提示词里没提违规词，它写的文案可能带"全网最低"、平台名；
- 回消息前不查外联词，私聊发联系方式也算引导站外交易；
- 照拼多多货源建草稿时不清理"拼多多""百亿补贴""官方旗舰店"、店铺名（描述里本地词表会提醒）；
- 传图前不检测二维码，网页端在浏览器里就不让传带二维码的图，我们后台传图不经过那一步。

**动手前要先定的**：
- `badwords.prepubcheck` 有没有"提醒但能发"的等级：抓一次线上真实的返回再定要不要放宽现在的拦法；
- 本地词表能不能直接给消息回复用：私聊里"加我""转账"的误报会更多，可能要单独一份。
