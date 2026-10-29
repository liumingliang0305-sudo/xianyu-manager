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
- 网页端的 mtop 中间件会把 data 里 BMP 以外的字符（emoji）写成 `\ud83d\ude00` 这样的转义再签名，`xianyu_core` 照做。

## 扫码登录（纯 HTTP）

0. ✅ 先调一次 mtop 接口（`mtop.gaia.nodejs.gaia.idle.data.gw.v2.index.get`）拿 `_m_h5_tk`，和浏览器先打开首页一样。这个接口现在要求 `piUrl` 参数，会报业务错误，但不影响拿令牌；
1. ✅ GET `passport.goofish.com/mini_login.htm`，从页面里的 `window.viewData.loginFormData` 取登录参数（`_csrf_token`、`umidToken`、`hsiz` 等），再加上 `umidTag=SERVER`；
2. ✅ GET `/newlogin/qrcode/generate.do`，参数就是上一步的 loginFormData，返回 `t`（数字毫秒时间戳）、`ck`、`codeContent`（一个 `qrcodeCheck.htm?lgToken=…` 链接，转成二维码给手机扫）；
3. ✅ POST `/newlogin/qrcode/query.do`，表单是 loginFormData 加上 `t`、`ck` 和几项浏览器环境字段，返回的 `qrCodeStatus` 依次是 `NEW` → `SCANED`（平台原样拼写）→ `CONFIRMED`，也可能是 `EXPIRED` 或 `CANCELED`；`hasError: true` 时立即重试，最多 5 次；
4. ✅ 确认时 query.do 的响应会种下登录 cookie，再访问一次 `https://www.goofish.com/im`，跳转链上会补齐其余 cookie。实测登录后拿到的是 `unb`、`cookie2`、`sgcookie`、`csg`、`tracknick`、`_tb_token_`、`_m_h5_tk`、`_m_h5_tk_enc`、`t` 等，这次没有要求额外验证；
5. 确认时如果带了 `iframeRedirect: true`，说明要做额外身份验证（`iv/mini/verify_modes.htm`、`ivCheckLogin.htm`），地址在 `iframeRedirectUrl`，这时要通知人工处理；
6. 还没做：Ydisks 在登录后还会调 `ac/account/setLoginSettings.do` 打开"保持登录"，能让 cookie 有效期更长。

## 商品管理

> 来源：2026-10-29 读网页端 xy-site 0.0.179 的 `p_personal-index.js`、`p_item-index.js`、`p_publish-index.js`，
> 并用已登录账号实测。✅ 是实测过的；🔍 只读过前端代码，没有在线上真正调用（都是写操作）。
> 网页端"我发布的"只给一个"扫码去 App 查看"的二维码，卖家管理能力比 App 少。

| 接口 | 版本 | 请求体 | 说明 |
|---|---|---|---|
| `mtop.idle.web.xyh.item.list` ✅ | 1.0 | `{needGroupInfo, pageNumber, userId, pageSize: 20}` | 个人主页的商品列表。`cardList[].cardData` 里有 `id`、`title`、`priceInfo.price`（元）、`itemStatus`、`categoryId`、`picInfo.picUrl`；"N人想要"在 `itemLabelDataVO` 的标签里。`nextPage` 判断翻页，`totalCount` 实测总是 0，不能用 |
| `mtop.taobao.idle.pc.detail` ✅ | 1.0 | `{itemId}` | 商品详情，`itemDO` 里有 `soldPrice`、`originalPrice`（元）、`quantity`、`itemStatus`/`itemStatusStr`、`browseCnt`、`wantCnt`、`collectCnt`、`desc`、`gmtCreate` |
| `mtop.idle.pc.idleitem.editDetail` ✅ | 1.0 | `{itemId}` | 编辑页的表单初值。价格单位是分（字符串），布尔值都是 `"true"`/`"false"` 字符串 |
| `mtop.idle.pc.idleitem.edit` ✅ | 1.0 | 见下 | 编辑后整体提交，成功返回 `{itemId}`。2026-10-29 实测改价 49→48→49 元 |
| `mtop.taobao.idle.item.downshelf` 🔍 | **2.0** | `{itemId}` | 下架 |
| `com.taobao.idle.item.delete` 🔍 | **1.1** | `{itemId}` | 删除，注意前缀是 `com.` |
| `mtop.idle.pc.idleitem.publish` 🔍 | 1.0 | 和 edit 一样，不带 itemId | 发布新商品（还没做，要先解决图片上传） |

`itemStatus`：列表里只见过 `0`（在售）和 `1`（已卖出，图上盖"卖掉了"）。下架后的商品**不在**个人主页列表里，
同步时只能标记成"不在主页了"。

### 编辑的请求体

网页端编辑页（`/publish?itemId=<id>`）的流程：`editDetail` → 转成表单 → 用户改 → 提交时再转回去调 `edit`。
表单只登记了 7 项：`imageInfoDOList`、`itemTextDTO`、`itemLabelExtList`、`itemPriceDTO`、`userRightsProtocols`、
`itemPostFeeDTO`、`itemAddrDTO`。提交体 = 默认值 `{freebies: false, itemTypeStr: "b", quantity: "1", simpleItem: "true"}`
\+ 表单值 + `itemCatDTO`（catId、catName、channelCatId、tbCatId）+ `itemId` + `uniqueCode`（毫秒时间戳接 0~999 随机数）
\+ `sourceId`/`bizcode`/`publishScene` 都是 `"pcMainPublish"`。

几个细节：
- 价格在表单里是元，提交时 ×100 转成分的字符串；为 0 的价格、邮费字段**不传**；
- 网页端只有描述框，提交时 `title` 和 `desc` 填同一串，`titleDescSeparate: false`，标题由平台截取；
- 多规格商品（`itemSkuList` 非空）和部分类目网页端不支持编辑，提示去 App 改；
- 图片的 `major`、尺寸、`type` 在 editDetail 里是字符串，表单里转成布尔和整数。

2026-10-29 在浏览器里取了编辑页表单的实际值，和 `build_edit_payload` 的输出逐字段比对：除 `itemLabelExtList` 外全部一致。
标签不一致是因为网页端会按类目推荐重新生成一遍（`properties` 相同，字段更多），我们直接带回 editDetail 里的。
数量字段我们用 editDetail 里的值，不用网页端写死的 `"1"`，避免把多件库存改成 1 件。

2026-10-29 用 `xym items price` 在一个在售商品上实测：49 → 48 → 49 元，两次都成功，详情页和 editDetail 都立即生效；
两次编辑前后比对 editDetail，除 `priceInCent` 外所有字段（图片、描述、标签、地址、邮费、类目）都没变，
说明直接带回 editDetail 里的标签是可行的。

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
| `mtop.taobao.idle.pc.detail` | 商品详情。✅ 见上面"商品管理" |
| `mtop.idle.web.xyh.item.list` | 我的商品列表。✅ 见上面"商品管理" |
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
