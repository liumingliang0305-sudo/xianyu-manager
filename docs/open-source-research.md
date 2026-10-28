# 闲鱼开源项目调研

> 调研日期：2026-10-28。数据来自 GitHub 搜索（`xianyu` / `goofish` / `闲鱼`，按 star 排序）和对 11 个候选仓库的源码阅读。star 数和更新时间都是当天的。

## 一、先说结论

1. **所谓"逆向底座"其实很小。**各项目共用的就五样东西：
   - mtop 接口签名：`md5(token & t & appKey & data)`，token 取 cookie `_m_h5_tk` 下划线前那段；
   - WebSocket 即时消息：先调 `mtop.taobao.idlemessage.pc.login.token` 换 accessToken，再连 `wss://wss-goofish.dingtalk.com/`，消息体是 base64 + MessagePack；
   - 扫码登录：`passport.goofish.com/newlogin/qrcode/generate.do` 取码，`query.do` 轮询，确认后拿到 cookie；
   - 登录态续期：`hasLogin.do` / `silentHasLogin.do`；
   - 风控处理：识别 `RGV587`、滑块 / punish 页面。

   真正花功夫的是**跟着平台改版维护**和**控制风控**，算法本身不难。

2. **许可证决定了能拿谁的代码。**star 最多的几个完整系统都是 AGPL / GPL；签名 JS 的源头 `cv-cat/XianYuApis` **没有许可证**，而它的 `goofish_js_version_2.js` 被原样拷进了好几个 Apache 项目（`goofish-cli` 里那份和原文件逐字节相同）。

3. **建议：自己写一个 Python 底座 `xianyu_core`**，模块划分和可复用代码参考 `goofish-cli`（Apache-2.0），签名和 MessagePack 解码用 Python 自己实现，不引入那份没有许可证的 JS；业务功能全部自研。备选方案是 Go，直接拷 `Ydisks-Xianyu-Helper` 的协议层。详见第四节。

## 二、候选项目

### A. 可以当底座 / 协议库的

| 项目 | ⭐ | 语言 | 许可证 | 最近更新 | 扫码登录 | 协议层 | 评价 |
|---|---|---|---|---|---|---|---|
| [fancyboi999/goofish-cli](https://github.com/fancyboi999/goofish-cli) | 380 | Python | Apache-2.0（NOTICE 另有限制） | 10-26 | Playwright 打开登录页扫码，另有免密续期 | `core/` 下 mtop、ws、sign、token、refresh、limiter、guard 拆得很干净，共约 3200 行；17 个命令，支持 CLI 和 MCP 两种入口 | **最适合参考的 Python 底座。**缺点：签名走 execjs 调 cv-cat 的 JS，要装 Node；NOTICE 写明"不允许做成针对闲鱼的 SaaS 转售" |
| [Christ9038/Ydisks-Xianyu-Helper](https://github.com/Christ9038/Ydisks-Xianyu-Helper) | 2099 | Go + React | Apache-2.0 | 10-28 | 纯 HTTP，`internal/xianyu/qrlogin` | `internal/xianyu/{mtop,ws,protocol,cookierefresh,renew}` 全是 Go，没有 JS 依赖 | **工程化程度最高**，测试很多，功能也最全（见 B 类）。协议层在 `internal/` 下，别的模块 import 不了，只能整包拷过来 |
| [11273/goofish-client](https://github.com/11273/goofish-client) | 69 | TypeScript | GPL-3.0 | 07-15 | 纯 HTTP 扫码，也支持密码登录 | 类型完整的 SDK：sign、msgpack、ws、mtop 服务（商品 / 订单 / 搜索 / 用户）都有 | 写得规整，可惜是 GPL |
| [shaxiu/XianyuAutoAgent](https://github.com/shaxiu/XianyuAutoAgent) | 9537 | Python | GPL-3.0 | 06-10 | 不支持，要手动粘 cookie | `utils/xianyu_utils.py` 用 335 行纯 Python 实现了签名、设备 ID 和 MessagePack 解码 | **签名和消息解码最好读的参考实现**，只看思路，不拷代码 |
| [cv-cat/XianYuApis](https://github.com/cv-cat/XianYuApis) | 1636 | JS / Python | **无许可证** | 08-18 | — | 签名 JS 的源头；另有 `gen_tfstk.js`、`et_f.js` 生成风控参数 | 没有 LICENSE 就是保留所有权利，**不能拷代码** |
| [Kaguya233qwq/myfish](https://github.com/Kaguya233qwq/myfish) | 110 | Python | MIT | 10-27 | 纯 HTTP 扫码 | 签名核心是闭源的 `.so` / `.dll`，**不支持 macOS** | 不适合当底座 |

### B. 完整的管理系统：拿来参考功能设计，不建议 fork

| 项目 | ⭐ | 技术栈 | 许可证 | 说明 |
|---|---|---|---|---|
| [zhinianboke/xianyu-auto-reply](https://github.com/zhinianboke/xianyu-auto-reply) | 8014 | Python / FastAPI | AGPL-3.0 | 功能最全：自动回复、自动发货、擦亮、评价、发布。现在已经拆成 backend-web / websocket / scheduler，还带充值、结算、广告这些商业模块。有一个[公开漏洞](https://releasealert.dev/github/zhinianboke/xianyu-auto-reply/cves)：`/api/v1/users/` 没做鉴权 |
| [GuDong2003/xianyu-auto-reply-fix](https://github.com/GuDong2003/xianyu-auto-reply-fix) | 2847 | Python | AGPL-3.0 | 上面那个项目的单体修复版，扫码登录在 `utils/qr_login.py`，走纯 HTTP |
| [Christ9038/Ydisks-Xianyu-Helper](https://github.com/Christ9038/Ydisks-Xianyu-Helper) | 2099 | Go + React | Apache-2.0 | 多账号、卡密、自动发货、改价、砍价、评价、擦亮、批量铺货、AI 客服、看板、多渠道通知。**拿来当功能清单很合适** |
| [23Star/xianyu-super-butler](https://github.com/23Star/xianyu-super-butler) | 1369 | Python | AGPL-3.0 | 自动发货、评价、擦亮 |
| [IAMLZY2018/XianYuAssistant](https://github.com/IAMLZY2018/XianYuAssistant) | 396 | Java（Spring Boot 3）+ Vue 3 | Apache-2.0 | 想用 Java 栈的话可以参考 |
| [dameng2026/xianyu-pilot](https://github.com/dameng2026/xianyu-pilot) | 148 | FastAPI + Vue | Apache-2.0 | 小而全，有 Docker Compose |

### C. 官方渠道和其他

| 项目 | ⭐ | 许可证 | 说明 |
|---|---|---|---|
| [XIE7654/goofish_api](https://github.com/XIE7654/goofish_api) | 123 | MIT | **闲鱼开放平台（闲管家）官方 API** 的 Python SDK。用 AppKey 授权，能管商品、订单、发货和卡密，**没有封号风险**；但要先入驻，而且**没有聊天接口** |
| [Usagi-org/ai-goofish-monitor](https://github.com/Usagi-org/ai-goofish-monitor) | 15279 | MIT | 买家这一侧的搜索和监控，用 Playwright 加 AI 筛选。以后要做选品或比价可以参考 |
| [cv-cat/catbus](https://github.com/cv-cat/catbus) | 528 | MIT | 用一套命令操作多个平台的 CLI，闲鱼是其中之一 |

## 三、许可证对我们意味着什么

| 许可证 | 我们复用它的代码之后 |
|---|---|
| **AGPL-3.0** | 只要以网络服务的形式给别人用，**整个系统都必须开源**。别拷 |
| **GPL-3.0** | 一旦分发，整个系统也要开源。只看思路，别拷代码 |
| **Apache-2.0 / MIT** | 可以闭源，也可以商用，保留原版权声明就行（Apache 还要求保留 NOTICE） |
| **没有许可证** | 默认保留所有权利，**不能拷** |

## 四、选型建议

### 方案一（推荐）：Python 自建底座

```
xianyu_core/            ← 底座：自己写，结构参考 goofish-cli
  auth/                 扫码登录（纯 HTTP generate.do / query.do）、cookie 存储、续期
  mtop/                 签名（md5）+ 请求客户端 + 错误码映射（RGV587 / SESSION_EXPIRED）
  im/                   WebSocket 长连接、心跳、重连、MessagePack 解码、发消息
  guard/                按账号和业务限流、熔断（参考 goofish-cli 的 limiter / guard）
app/                    ← 业务层：FastAPI，全部自研
  账号 / 商品 / 订单 / 消息 / 自动回复 / 自动发货 / 通知 ...
web/                    ← 管理后台
```

- **为什么选 Python**：闲鱼相关的开源项目大多是 Python，出了问题能对照的代码最多；接 AI 自动回复也方便。
- **为什么自己写签名**：签名就是一次 md5，MessagePack 解码直接用 `msgpack` 库。自己写一两百行就够，许可证干净，也省掉 Node 运行时。
- **为什么不用浏览器扫码**：纯 HTTP 扫码在服务端就能跑，适合多账号；只有触发风控验证时才需要人工开浏览器处理。

### 方案二：Go，直接拷 Ydisks 的协议层

把 `internal/xianyu` 整包拷进 `third_party/`，保留 Apache 声明，业务层自己写。好处是协议层现成、成熟，测试也全；坏处是跟它的内部结构绑在一起，上游有更新只能手动合并。

### 不建议的做法

- fork `zhinianboke/xianyu-auto-reply` 再改：AGPL，掺着商业模块，还有安全问题；
- 拷 `cv-cat` 的 JS：它没有许可证。

## 五、风险

- **平台规则和封号**：除了闲管家开放平台，这些都是非官方协议。底座必须带限流和熔断，`goofish-cli` 的做法可以照搬：经营类接口每分钟 1 次，媒体类每分钟 9 次，遇到 RGV587 自动熔断。
- **滑块和验证码**：底座只负责**发现验证并通知人工处理**，不做自动过滑块。自动过滑块正是风控重点打击的行为，最容易连累账号。
- **协议变动**：平台随时可能改版。底座要写集成测试，在 `third_party/` 里记下参考的上游 commit，方便对照上游修复。
- **凭证安全**：cookie 等同于账号密码，存库要加密（Ydisks 用的是 AES-256-GCM），日志里要脱敏。

## 附：调研时看过的上游版本

| 仓库 | 克隆时间 |
|---|---|
| fancyboi999/goofish-cli、Christ9038/Ydisks-Xianyu-Helper、11273/goofish-client、shaxiu/XianyuAutoAgent、cv-cat/XianYuApis、Kaguya233qwq/myfish、zhinianboke/xianyu-auto-reply、GuDong2003/xianyu-auto-reply-fix、dameng2026/xianyu-pilot、IAMLZY2018/XianYuAssistant、XIE7654/goofish_api | 2026-10-28（`--depth 1`，各仓库默认分支） |
