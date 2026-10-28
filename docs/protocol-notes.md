# 闲鱼网页端协议笔记

> 来源：2026-10-28 阅读开源项目源码时整理（goofish-cli、Ydisks-Xianyu-Helper、goofish-client、XianyuAutoAgent）。
> 标了 ✅ 的已经用 `xianyu_core` 对线上接口实测过（2026-10-28，未登录状态）；其余还没验证，以抓包结果为准。

## 域名

| 用途 | 地址 |
|---|---|
| 网页端 | `https://www.goofish.com` |
| mtop 接口 | `https://h5api.m.goofish.com/h5/<api>/<version>/` |
| 登录 | `https://passport.goofish.com` |
| 即时消息 | `wss://wss-goofish.dingtalk.com/` |

## mtop 签名

```
token = cookie["_m_h5_tk"].split("_")[0]
t     = 毫秒时间戳
sign  = md5(f"{token}&{t}&{appKey}&{data}")      # data 是 JSON 字符串
```

- 网页端的 appKey 是 `34839810` ✅；
- ✅ 没有 token 时用空串签名，平台返回 `FAIL_SYS_TOKEN_EMPTY` 并下发 `_m_h5_tk`，用新 token 重签后能通过签名校验；
- 返回 `FAIL_SYS_TOKEN_EXOIRED`（平台原样拼写，有的项目也判断 `FAIL_SYS_TOKEN_EXPIRED`）时，同样用新下发的 `_m_h5_tk` 重签；返回 `FAIL_SYS_SESSION_EXPIRED` 时重签没用，要重新登录；
- 返回 `RGV587` 表示触发了风控，**应该熔断**，不要重试。

## 扫码登录（纯 HTTP）

0. ✅ 先调一次 mtop 接口（`mtop.gaia.nodejs.gaia.idle.data.gw.v2.index.get`）拿 `_m_h5_tk`，和浏览器先打开首页一样。这个接口现在要求 `piUrl` 参数，会报业务错误，但不影响拿令牌；
1. ✅ GET `passport.goofish.com/mini_login.htm`，从页面里的 `window.viewData.loginFormData` 取登录参数（`_csrf_token`、`umidToken`、`hsiz` 等），再加上 `umidTag=SERVER`；
2. ✅ GET `/newlogin/qrcode/generate.do`，参数就是上一步的 loginFormData，返回 `t`（数字毫秒时间戳）、`ck`、`codeContent`（一个 `qrcodeCheck.htm?lgToken=…` 链接，转成二维码给手机扫）；
3. ✅ POST `/newlogin/qrcode/query.do`，表单是 loginFormData 加上 `t`、`ck` 和几项浏览器环境字段，返回的 `qrCodeStatus` 依次是 `NEW` → `SCANED`（平台原样拼写）→ `CONFIRMED`，也可能是 `EXPIRED` 或 `CANCELED`；`hasError: true` 时立即重试，最多 5 次；
4. ✅ 确认时 query.do 的响应会种下登录 cookie，再访问一次 `https://www.goofish.com/im`，跳转链上会补齐其余 cookie。实测登录后拿到的是 `unb`、`cookie2`、`sgcookie`、`csg`、`tracknick`、`_tb_token_`、`_m_h5_tk`、`_m_h5_tk_enc`、`t` 等，这次没有要求额外验证；
5. 确认时如果带了 `iframeRedirect: true`，说明要做额外身份验证（`iv/mini/verify_modes.htm`、`ivCheckLogin.htm`），地址在 `iframeRedirectUrl`，这时要通知人工处理；
6. 还没做：Ydisks 在登录后还会调 `ac/account/setLoginSettings.do` 打开"保持登录"，能让 cookie 有效期更长。

## 登录态续期

- `passport.goofish.com/newlogin/hasLogin.do`、`silentHasLogin.do`
- goofish-cli 的做法：碰到 `SESSION_EXPIRED` 先尝试免密续期，不行再要求重新扫码。

## 即时消息

1. 调 `mtop.taobao.idlemessage.pc.login.token` 拿 accessToken；
2. 连接 WebSocket，发送 `/reg` 注册（带 appKey、token、设备 ID 等）；
3. 定时发心跳 `/!`；
4. 下行消息体是 base64 编码的 MessagePack，解出来才是消息内容；
5. 发消息走 `/r/MessageSend/sendByReceiverScope`。

设备 ID、mid、uuid 的生成规则可以参考 XianyuAutoAgent 的 `utils/xianyu_utils.py`（GPL，只看思路，不拷代码）。

## 开源项目里常见的 mtop 接口

| 接口 | 用途 |
|---|---|
| `mtop.taobao.idlemessage.pc.login.token` | 即时消息的 accessToken |
| `mtop.taobao.idlemessage.pc.loginuser.get` | 当前登录用户。✅ 只返回 `userId`（等于 cookie 里的 `unb`），没有昵称 |
| `mtop.idle.web.user.page.head` | 个人主页头部，`{"self": true}` 查自己。✅ 昵称在 `module.base.displayName` |
| `mtop.taobao.idlemessage.pc.user.query` | 查询用户 |
| `mtop.taobao.idlemessage.pc.session.sync` | 同步会话 |
| `mtop.taobao.idle.pc.detail` | 商品详情 |
| `mtop.idle.web.xyh.item.list` | 我的商品列表 |
| `mtop.idle.pc.idleitem.publish` | 发布商品 |
| `mtop.taobao.idle.kgraph.property.recommend` | 类目和属性推荐 |
| `mtop.taobao.idle.item.polish` / `mtop.idle.item.polish` | 擦亮 |
| `mtop.taobao.idle.trade.merchant.sold.get` | 卖出的订单 |
| `mtop.idle.web.trade.order.detail` | 订单详情 |
| `mtop.taobao.idle.trade.user.adjust.price` | 改价 |
| `mtop.taobao.idle.logistic.consign.dummy` | 虚拟发货 |
| `mtop.taobao.idle.rate.create` | 评价 |
| `mtop.taobao.idle.merchant.rate.list` | 评价列表 |
| `mtop.taobao.idlemtopsearch.pc.search` | 搜索 |
| `mtop.idle.web.user.page.nav` | 个人主页导航 |
