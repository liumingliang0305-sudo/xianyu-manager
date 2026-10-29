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
- 返回 `FAIL_SYS_TOKEN_EXOIRED`（平台原样拼写，有的项目也判断 `FAIL_SYS_TOKEN_EXPIRED`）时，同样用新下发的 `_m_h5_tk` 重签；返回 `FAIL_SYS_SESSION_EXPIRED` 时重签没用，要免密续期或重新登录（见下面"登录态续期"）；
- 返回 `RGV587` 表示触发了风控，**应该熔断**，不要重试。
- 网页端的 mtop 中间件会把 data 里 BMP 以外的字符（emoji）写成 `\ud83d\ude00` 这样的转义再签名，`xianyu_core` 照做。

## 扫码登录（纯 HTTP）

0. ✅ 先调一次 mtop 接口（`mtop.gaia.nodejs.gaia.idle.data.gw.v2.index.get`）拿 `_m_h5_tk`，和浏览器先打开首页一样。这个接口现在要求 `piUrl` 参数，会报业务错误，但不影响拿令牌；
1. ✅ GET `passport.goofish.com/mini_login.htm`，从页面里的 `window.viewData.loginFormData` 取登录参数（`_csrf_token`、`umidToken`、`hsiz` 等），再加上 `umidTag=SERVER`；
2. ✅ GET `/newlogin/qrcode/generate.do`，参数就是上一步的 loginFormData，返回 `t`（数字毫秒时间戳）、`ck`、`codeContent`（一个 `qrcodeCheck.htm?lgToken=…` 链接，转成二维码给手机扫）；
3. ✅ POST `/newlogin/qrcode/query.do`，表单是 loginFormData 加上 `t`、`ck` 和几项浏览器环境字段，返回的 `qrCodeStatus` 依次是 `NEW` → `SCANED`（平台原样拼写）→ `CONFIRMED`，也可能是 `EXPIRED` 或 `CANCELED`；`hasError: true` 时立即重试，最多 5 次；
4. ✅ 确认时 query.do 的响应会种下登录 cookie，再访问一次 `https://www.goofish.com/im`，跳转链上会补齐其余 cookie。实测登录后拿到的是 `unb`、`cookie2`、`sgcookie`、`csg`、`tracknick`、`_tb_token_`、`_m_h5_tk`、`_m_h5_tk_enc`、`t` 等，这次没有要求额外验证；
5. 确认时如果带了 `iframeRedirect: true`，说明要做额外身份验证（`iv/mini/verify_modes.htm`、`ivCheckLogin.htm`），地址在 `iframeRedirectUrl`，这时要通知人工处理；
6. 落到 `/im` 之后打开"保持登录"，会话过期后才能免密续期，见下面"登录态续期"。失败只记日志，不影响登录。

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

## 订单

> 来源：2026-10-29 读网页端 xy-site 0.0.179 的 `p_order-detail-index.js`、`p_im-index.js`、`p_bought-index.js`，
> 卖家工作台 seller-workbench 0.1.68，以及 Ydisks-Xianyu-Helper（Apache-2.0）的 `internal/xianyu/mtop/`。
> ✅ 是用个人卖家账号实测过的（只读）；🔍 是网页端代码里看到的调用，没在线上调过；📦 只在开源实现里见过。

网页版主站只有"我买到的"（`mtop.idle.web.trade.bought.list`），**没有卖出的订单列表**；卖出的订单在卖家工作台
`seller.goofish.com/?site=COMMONPRO#/seller-trade/order-manage`，工作台是微前端，订单页的代码是动态加载的，没读到。
发货在网页版也没有入口（订单卡片上的"发货"按钮是弹二维码让去 App）。

**2026-10-29 用个人卖家账号实测（只读）：个人卖家在网页端拿不到卖出的订单列表。**
- `merchant.sold.get` 返回 `PERMISSION_EXCEPTION::无权限访问`。工作台加载时先调
  `mtop.alibaba.idle.seller.platform.user.business.identity.get`（`{}`）查商家身份 ✅，这个账号返回 `data: []`，
  工作台前端遇到空列表就跳 `/no-permission`。也就是说卖家工作台只对"闲鱼专业卖家"开放；
- 网页版个人中心的"我卖出的"是灰的，跳 App（`fleamarket://SoldItems`）；手机 H5 的"我发布的"
  （`moyu-project-idle-my-publish` 0.0.167）里也只有商品管理的接口，没有卖出订单；
- 消息页：`mtop.taobao.idlemessage.pc.session.sync` v3.0、`{fetchNum: 50}` ✅ 返回最近 50 个会话
  （`hasMore` 为真时更早的要靠长连接推送），`session.itemInfo.itemId` 和 `itemInfo.sellerInfo.userId` 能分出
  自己是卖家的会话。`mtop.idle.trade.pc.message.headinfo` ✅（`{itemId, sessionId, sessionType: 1}`，
  `valueType=string`）返回聊天窗口顶部的卡片：没有订单时 `template: "common"`，`commonData` 只有
  `itemId`、`itemPreInfo`、`supportPCTrade`；按网页端代码，有订单时 `commonData` 里有 `orderId` 和
  `orderDetailUrl`，`right.data.btnList` 是订单按钮。实测查了 11 个自己当卖家的会话（含已卖出商品的 3 个），
  都没有订单。
- "我买到的" `mtop.idle.web.trade.bought.list` ✅（`{pageNumber, orderStatus: "ALL"}`，`valueType=string`；
  `orderStatus` 还可以是 `NOT_PAY` / `NOT_SHIP` / `SHIPPED` / `NOT_RATE` / `REFUND`）：返回 `items[]`（`commonData`、
  `head`、`content`、`tail`）、`nextPage`、`lastEndRow`，`totalCount` 总是 0。`commonData` 有 `orderId`、`itemId`、
  `seller`、`tradeStatusEnum`（见过 `trade_success`、`refund_success`）等。实测里 `seller` 全是 false，只有买到的订单；
- 照着它试了 `mtop.idle.web.trade.sold.list`、`.sell.list`、`.seller.list`，都是 `FAIL_SYS_API_NOT_FOUNDED`。
  App 里"我卖出的"是原生页面，走 App 端签名的接口，网页这套签名调不了。

| 接口 | 版本 | 请求体 | 说明 |
|---|---|---|---|
| `mtop.taobao.idle.trade.merchant.sold.get` 📦 | 1.0 | `{pageNumber, rowsPerPage, orderIds: "", queryCode: "ALL", orderSearchParam: "{}"}` | 卖出的订单。和别的接口不一样：URL 参数 `type=json`、`valueType=string`，请求头 `idle_site_biz_code: COMMONPRO`，Origin / Referer 是 seller.goofish.com。返回 `data.module.items[]` 和 `nextPage`；每条里 `commonData` 有 `orderId`、`itemId`、`orderStatus`（中文）、`inRefund`，`buyerInfoVO.buyerId`，金额在 `priceVO`。标题、图片、下单时间的字段名 Ydisks 也是猜的 |
| `mtop.idle.web.trade.order.detail` ✅ | 1.0 | `{tid: 订单号}`，`valueType=string` | 订单详情，买卖双方共用，见下 |
| `mtop.taobao.idle.trade.order.modify.price.render` 🔍 | 1.0 | `{bizOrderId}` | 改价弹窗的初值：`modifyPriceRenderList[]`，每项 `{key, name, price}`，`price` 是元。Ydisks 里 key 是 `modifyFee`（商品价）和 `newTransportFee`（运费） |
| `mtop.taobao.idle.trade.user.adjust.price` 🔍 | 1.0 | `{<key>: 分的字符串, ..., orderId}` | 改价。网页端把弹窗里每一项都提交，没改的用初值，元 ×100 转成分 |
| `mtop.taobao.idle.trade.order.close.reason.get` 🔍 | 1.0 | `{bizOrderId}` | 关闭订单的可选原因 `closeReasons[]`（还没做） |
| `mtop.taobao.idle.trade.close.by.seller` 🔍 | **2.0** | `{tid, closeReason, bizOrderId}` | 卖家关闭订单（还没做） |
| `mtop.taobao.idle.trade.seller.delay.confirm` 🔍 | 1.0 | `{delayTime: 秒数字符串, orderId}` | 卖家给买家延长收货（还没做） |
| `mtop.taobao.idle.logistic.consign.dummy` 📦 | 1.0 | `{orderId, tradeText, picList, newUnconsign: true}` | 虚拟商品"确认发货"（还没做） |

### 订单详情的返回

2026-10-29 用一笔真实的已完成订单（卖家视角）核对过，`OrderDetail.parse` 全部字段都对得上：
- 顶层：`seller`（布尔，当前账号是不是卖家，用来拒绝误加买到的订单）、`peerUserId`（对方 ID）、`itemId`、`orderId`、
  `status`（数字，交易成功是 4）、`commonInfo.createTime`（毫秒）、`utArgs`（`orderStatus`、
  `orderStatusName` 比如"买家已确认收货，交易成功"、`orderMainTitle`）、`bottomBarVO.buttonList[]`（`name`、`tradeAction`，
  已完成订单是 查看评价 / 查看钱款 / 删除订单）；
- `components[]` 按 `render` 分块：`orderStatusVO`（`orderStatusInfo.title`、`descRichText`（已完成时是空数组）、
  `orderStatusNodeList[]` 的 `title`/`completed`/`time`）、`userGuideVO`、`orderInfoVO`；
- `orderInfoVO`：`itemInfo`（`title`、`itemMainPictCdnUrl`、`buyAmount`、`price`）；`priceInfo.amount`（`title` 是"成交价"、`value`），
  `billList[]`（`code` 有 `ITEM_TOTAL_FEE`、`POST_FEE`），`softwareBill`（"软件服务费"，负数），`expectBill`（"预估到手"）；
  `orderInfoList[]` 是 订单编号 / 交易快照 / 支付宝交易号 / 收货地址 / 买家昵称 / 下单时间 / 付款时间 / 发货时间 / 成交时间；
- 已完成的订单**没有** `addressInfoVO`，地址在 `orderInfoList` 的"收货地址"里。按网页端代码，没发货的订单才有
  `addressInfoVO`（`name`、`phoneNumber`、`address`）。

订单状态只给中文文字，`xianyu_core.order.order_status` 按关键词归成 unpaid / to_ship / shipped / done / closed / refund，
认不出的算 other。

## 登录态续期

> 来源：2026-10-29 读网页端的续期插件 `o.alicdn.com/vip/goofish-auto-login/plugin.js`（SHA-256 `b946a6db…`，和 Ydisks
> 审计时的同一版）、xy-site 0.0.179 的 `main.js` 和 `p_layout.js`，对照 Ydisks 的 `internal/xianyu/renew`，
> 再用已登录账号做了只读验证。✅ 是实测过的；🔍 只读过代码，没有在线上真正调用。

分两块：网页端个人信息弹窗里的"保存登录信息"开关（下面叫保持登录），和续期插件 `window.lib.refreshAutoLogin`（免密续期）。
免密续期要靠保持登录下发的长登录标记，没开保持登录就续不了，会话过期只能重新扫码。

### 保持登录

| 接口 | 请求 | 说明 |
|---|---|---|
| `ac/account/queryLoginSettings.do` ✅ | POST，query 是 `fromSite=77&appName=xianyu&bizEntrance=web`，空表单 | 返回 `{"code":0,"returnValue":{"status":200,"canOpenLongLogin":…,"hasLongTokenLogin":…,"occurError":false},"success":true}`。`hasLongTokenLogin` 是这次会话开没开；网页端只有 `canOpenLongLogin` 为 true 才显示开关 |
| `ac/account/setLoginSettings.do` 🔍 | 同样的 query，表单 `status=0`（打开）或 `status=1`（关闭） | 成功看 `success`，失败原因在 `msgInfo`。打开后平台下发长登录标记 `havana_lgc_exp` 或 `cookie3_bak_exp`（值是到期的毫秒时间戳） |

两个请求都从 `www.goofish.com` 发（`origin`、`referer` 是网页端页面），带登录 cookie。

`xianyu login` 扫码后的做法：先查一次，`canOpenLongLogin` 为 true 且还没开时才提交 `status=0`，提交后再查一次确认。
网页端不显示开关的时候（`canOpenLongLogin` 为 false）不提交，免得发出网页端不会发的请求。

2026-10-29 实测：当天扫码登录的账号查出来是 `canOpenLongLogin: false`、`hasLongTokenLogin: false`，凭证里也没有长登录标记。
同一天用新代码重新扫码（落到 `/im` 后马上查）也一样是 `canOpenLongLogin: false`，所以没有提交 `setLoginSettings.do`，
新凭证里还是没有 `havana_lgc_exp` / `cookie3_bak_exp`，也没有 `sdkSilent`。
🔍 为什么不能开还不知道：可能是账号本身不支持，也可能和登录方式（纯 HTTP 扫码、`umidTag=SERVER`、没走登录框的
`keepLoginConfirm` 弹窗）有关。可以在浏览器里正常登录一次 goofish.com，看右上角个人信息弹窗里有没有"保存登录信息"开关来区分。

### 免密续期

插件的流程（`xianyu_core/renew.py` 照搬）：

1. `sdkSilent`（毫秒时间戳）还没到 → 不续，不发请求（插件报 "request limit by fatigue"，平台在限制续期频率）；
2. 等 2 秒；
3. 选分支：`havana_lgc_exp` 没过期走 havana（加 `ltl=true`），否则 `cookie3_bak_exp` 没过期走 cookie3（加 `skipSessionFilter=true&c2r=true`），
   都没有 → 不续，不发请求（插件报 "cookie expired"）；
4. POST `passport.goofish.com/newlogin/silentHasLogin.do?documentReferer=<当前页面地址去掉 query>&appName=xianyu&appEntrance=xianyu_sdkSilent&fromSite=0&<分支参数>`，
   没有请求体，带 cookie；
5. 结果在 `data.content.data`，没有就看 `content.data`：`processFinished === true && resultCode === 100` 才算成功，新 cookie 在 Set-Cookie 里。

- 网页端每打开一个页面就调一次（`main.js`，成功后刷新页面），靠 `sdkSilent` 限频；
- ✅ 没有长登录标记时不发请求：2026-10-29 在上面那个凭证上调 `silent_renew`，直接返回"没有开启保持登录"，一个请求都没发；
- 🔍 真正调 `silentHasLogin.do` 还没在线上验证过，要先有开了保持登录的凭证（上面的账号两次登录都开不了）。
- `hasLogin.do` 是登录框（`mini_login.htm`）自己用的"已登录 / 快速进入"检查，插件和 Ydisks 现在的续期都不调它，我们也不用。
- goofish-cli 不走接口：用 Playwright 打开首页，弹出登录框时点"快速进入"。

### xianyu_core 的做法

- `MtopClient` 碰到会话失效（`FAIL_SYS_SESSION_EXPIRED`、`FAIL_SYS_ILLEGAL_ACCESS`）时按上面的流程试一次续期，一个实例最多一次；
  成功就重签重试一次，不行抛 `AuthRequiredError`，消息里写明为什么没续（没开保持登录、限频、平台拒绝）；
- 风控（`RGV587`、`FAIL_SYS_USER_VALIDATE`、punish 页）一律不续期；续期请求本身被跳到 punish 页也按风控处理；
- 和插件一样遵守 `sdkSilent` 限频，会话过期时也不绕过（Ydisks 在会话过期时会绕过，我们不这么做）；
- 续期拿到的 cookie（包括 `sdkSilent`）都在 cookie jar 里，业务层 `Accounts.mtop()` 用完写回凭证文件，续期失败也写。

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
| `mtop.taobao.idle.trade.merchant.sold.get` | 卖出的订单。见上面"订单" |
| `mtop.idle.web.trade.order.detail` | 订单详情。见上面"订单" |
| `mtop.taobao.idle.trade.user.adjust.price` | 订单改价。见上面"订单" |
| `mtop.taobao.idle.logistic.consign.dummy` | 虚拟发货 |
| `mtop.taobao.idle.rate.create` | 评价 |
| `mtop.taobao.idle.merchant.rate.list` | 评价列表 |
| `mtop.taobao.idlemtopsearch.pc.search` | 搜索 |
| `mtop.idle.web.user.page.nav` | 个人主页导航 |
