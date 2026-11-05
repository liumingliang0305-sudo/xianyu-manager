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
| `mtop.idle.pc.idleitem.publish` 🔍 | 1.0 | 和 edit 一样，不带 itemId | 发布新商品，见下面"发布商品" |

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

本系统的编辑页（[app/drafts.py](../app/drafts.py) 的 `edit_item`）：打开时调一次 `editDetail`，原样存进草稿，表单和发布页一样；
提交时请求体照这份 editDetail 改：原有的图照 editDetail 带回（换了顺序就重标 `major`），新加的图按发布页的写法；
邮费没换就原样带回（包邮时也带着 `templateId: "0"`，和网页端编辑页一样），对不上四种选法的（App 里设的运费模板）只能沿用；
重新推荐分类时，第一次带上 editDetail 里的标签和类目当 `selectedList`、`catId`（🔍 写法和推荐过以后再推荐一样，
没对照过网页端编辑页的请求）；描述改了才查违禁词，分类换了才重新查服务。拉初值**不用** `pc.detail`：那是买家看的
商品详情接口，2026-11-03 改价后几秒调它就被 RGV587 风控，`editDetail` 和 `edit` 没事。

### 重新上架

网页端没有重新上架：2026-11-03 翻了 xy-site 0.0.179 的 `main.js` 和 15 个页面脚本，商品的写接口只有 `edit`、
`downshelf`、`delete`、`publish` 和闲鱼自己的草稿箱，没有 upshelf 之类的接口，"我发布的"里也看不到下架的商品。
App 里让原商品回到在售的接口没抓过，不猜接口名去调（写操作，调错了容易被风控）。

本系统的做法（[app/drafts.py](../app/drafts.py) 的 `relist_item`）是照原商品发一个新商品：用 `editDetail` 拉原商品的表单
存进草稿（和编辑一样记在 origin），发布时请求体照编辑的拼，去掉 `itemId`、`quantity` 改成 `"1"`（卖完的商品可能是 0）、
补上发布页有的 `defaultPrice: false`，调 `publish`。网页端发布和编辑的请求体本来就只差 `itemId`（见上面"编辑的请求体"）。
原来的图直接用闲鱼上的地址，不重传；属性、服务、App 里设的运费模板原样带上，换了分类才重新查服务。
🔍 还没在线上试过：下架、卖掉的商品 `editDetail` 拿不拿得到，`publish` 收不收 editDetail 格式的图片（多了 `imgPath`、
`templateIndex` 这些字段），以第一次实测为准。

## 发布商品

> 来源：2026-11-03 读网页端发布页（goofish.com/publish）xy-site 0.0.179 的 `p_publish-index.js`，并用已登录账号
> 调了一遍只读的接口。✅ 是调过的；🔍 只读过前端代码，没有在线上调用（会真的传图、发出商品）。
> 开源项目 xianyu-auto-reply-fix（AGPL，只看思路）也是纯 HTTP 发布，流程一样，传图用的是聊天的 appkey。

网页端发布页的流程：打开页面先查发布限制 → 选图时立即传到闲鱼 → 描述或图片一变就推荐分类和属性 →
输入描述时查违禁词 → 分类定了以后查能开的服务 → 点发布。

| 接口 | 版本 | 请求体 | 说明 |
|---|---|---|---|
| `mtop.idle.pc.idleitem.prepublish.check` ✅ | 1.0 | `{}` | 发布限制。`limited` 为真且有 `publishLimitInfo`（`title`、`content`、`qrCodeUrl`）时网页端不让发，弹二维码去 App。实测 `{limited: false}` |
| `mtop.idle.pc.idleitem.preget` ✅ | 1.0 | `{}` | 发布页配置：`supportSkuOrInventory`（能不能发多规格，个人账号是 false）、`commissionConfig`（基础软件服务费 0.6%，最高 60 元） |
| `mtop.taobao.idle.kgraph.property.recommend` ✅ | **2.0** | 见下 | 按描述（和图片）推荐分类和属性 |
| `mtop.taobao.idle.kgraph.property.search` 🔍 | 1.0 | `{inputText, propertyId, description, catId…, scene: "mainPublish"}` | 属性下拉框里搜别的值，本系统没用 |
| `mtop.taobao.idle.local.poi.get` ✅ | 1.0 | `{longitude, latitude}` | 发货地，带请求头 `EagleEye-UserData: spm-cnt=a21ybx`。见下 |
| `mtop.idle.item.publish.service.cards.list` ✅ | 1.0 | `{cpvList, itemInfoJson, param}`（都是 JSON 字符串） | 这个分类能开的服务。`itemInfoJson` 里没有 `itemCatDTO` 时报 `PARAM_ILLEGAL::item cat info miss`。实测这个账号返回 `{supportService: false}`，没有服务 |
| `mtop.taobao.idleitem.badwords.prepubcheck` ✅ | **2.0** | `{title: 描述, scene: "pcPublish", sessionId: 毫秒时间戳接 unb}` | 违禁词。`result` 为真时 `badWordAlertInfo.items[].words` 是命中的词，`forbidPublish` 为真不让发 |
| `mtop.idle.pc.idleitem.publish` 🔍 | 1.0 | 和编辑一样，不带 itemId | 成功返回 `{itemId}`。`FAIL_BIZ_STRONG_VALID_VERIFY_INFO` 是要先做实人认证（网页端弹二维码） |
| `mtop.idle.idleitem.draft.edit` / `draft.publish` 🔍 | 1.0 | | 闲鱼自己的草稿箱，网页端只在"这个分类网页端不支持"时存进去、扫码去 App 接着发。本系统没用 |

**传图** 🔍：`POST https://stream-upload.goofish.com/api/upload.api?floderId=0&appkey=fleamarket&_input_charset=utf-8`，
multipart 表单，文件字段叫 `file`，带登录 cookie（antd Upload，`withCredentials`，还会把 `Access-Control-Allow-Origin: *`
当请求头带上）。返回 `{success, object: {url, pix: "宽x高", …}}`。网页端发布页用 `appkey=fleamarket`，聊天发图是 `xy_chat`。
网页端在浏览器里先查一遍：单张不超过 10 MB，图里有二维码的不让传。

表单里的一张图转成 `imageInfoDOList` 的一项：
`{extraInfo: {isH: "false", isT: "false", raw: "false"}, isQrCode: false, url, heightSize, widthSize, major: 第一张, type: 0, status: "done"}`。
图片 1 到 9 张；描述必填、最多 1500 字、不能有 emoji（网页端用一个 emoji 正则拦）。

**类目推荐**：请求体 `{title: 描述, lockCpv: false, multiSKU: false, publishScene: "mainPublish", scene: "newPublishChoice",
description, imageInfos, uniqueCode}`；推荐过以后再推荐，还带上 `currentCardList`（上次的各张卡片的 `cardData`）、
`selectedList`（选中的值，格式同下面的标签）、`catId`/`catName`/`channelCatId`（选中的分类，没有时 catId 是 `50023914`）。
网页端描述停手 0.6 秒推荐一次；改了任何一个选项也推荐一次，平台按新的选择刷新别的属性（换了分类，属性就换一套）。
返回 `cardList`、`categoryPredictResult`（`catId`、`catName`、`channelCatId`、`leafId`、`tbCatId`）、`isBook`：

- 每张卡片 `{cardType, cardData: {propertyId, propertyName, isMultiple, valuesList}}`。`propertyId` 是 `-10000` 的是"分类"，
  值里有 `catId`、`catName`、`channelCatId`、`tbCatId`、`leafId`；别的是属性（品牌 `20000`、成色 `20879`……），值里有
  `valueId`、`valueName`。平台预先选好的值 `isClicked` 是 `"1"`，每个值带一个 `transportData`；
- `cardType` 不是 `20401`（普通的单选、多选）的卡片，或者卡片是图书时，网页端提示"当前分类不支持网页端发布"，不让发。
  实测"小米充电宝"推荐出一张 `cardType: 7001` 的"3C证书编号"（要填文字），这类商品网页端发不了；
- `itemCatDTO` 取分类卡片里选中的那项，没有就用 `categoryPredictResult`；
- `itemLabelExtList` 是每张卡片里选中的值：`{...transportData, text: 值的名字, isUserClick, properties: "属性ID##属性名:值ID##值名"}`，
  分类的值 ID 用 `channelCatId`、名字用 `catName`。和编辑页 editDetail 里的标签格式一样；
- 用户改了选项：选中的 `isClicked`/`isUserClick` 为 `"1"`，原来选中、这次取消的 `isUserCancel` 为 `"1"`，`transportData` 里同步改。

2026-11-03 实测"宜家落地灯，九成新"：分类预测落地灯（还给了 10 个候选），品牌预选 IKEA/宜家，成色预选几乎全新。

**发货地**：网页端用浏览器定位的坐标调 `poi.get`（拿不到定位就用北京东城的坐标），返回 `selectedPoi`（实测是上次发布用的地址，
和坐标无关）、`commonAddresses`（常用地址）、`nearbyAddresses`（坐标附近）。默认用 `selectedPoi`，没有就用常用地址第一个。
一个地址转成 `itemAddrDTO`：`{area, city, divisionId, gps: "纬度,经度", poiId, poiName: poi 或 "城市 区", prov}`。

"宝贝所在地"弹窗里能按名字搜：网页端在浏览器里加载高德地图 JS 接口 2.0（用的是闲鱼自己的高德 key），
搜索框接高德的输入提示（`AMap.AutoComplete`），选中一个地点后用 `AMap.PlaceSearch.searchNearBy` 搜周围 1 公里当"附近地址"。
选中的地点按 `{address, area: adname, city: cityname, divisionId: adcode, latitude, longitude, poi: name, poiId: id, prov: pname}`
转成上面的格式。闲鱼自己没有按名字搜地址的接口（几个前端文件里和地址有关的只有 `poi.get`），存进商品的就是高德的地点：
`poiId` 是高德的地点 ID，`divisionId` 是高德的行政区编码。`poi.get` 传任意坐标都会返回那附近的地址。

本系统用自己的高德"Web 服务" key 在后台调高德的关键字搜索（`restapi.amap.com/v3/place/text`，[app/places.py](../app/places.py)），
不用闲鱼的 key。要带 `extensions=all`：`base` 不给 `adcode`。没有值的字段高德给空数组。2026-11-03 实测搜"光谷苑"，
第一个结果的地点 ID、坐标、行政区编码和闲鱼常用地址里的那条完全一样；省市名带"省""市"（`poi.get` 里没有），
和网页端搜出来存的一样。

**邮费**（`itemPostFeeDTO`，四选一，另有"支持自提" `onlyTakeSelf`）：包邮 `{canFreeShipping: true, supportFreight: true}`；
按距离计费 `{canFreeShipping: false, supportFreight: true, templateId: "-100"}`；一口价 `{canFreeShipping: false, supportFreight: true,
templateId: "0", postPriceInCent}`（最多 1000 元）；无需邮寄 `{canFreeShipping: false, supportFreight: false, templateId: "0"}`。

**服务**：`cards.list` 返回的 `services[]` 转成 `userRightsProtocols`：`{enable: code 不是 AI_SALE 且 defaultEnable, serviceCode: code}`。

提交的请求体和编辑一样（见上面"编辑的请求体"）：`{freebies: false, itemTypeStr: "b", quantity: "1", simpleItem: "true"}`
\+ 图片、描述（`title` 和 `desc` 同一串）、标签、价格（分，字符串，0 不传）、服务、邮费、地址、`defaultPrice: false`、`itemCatDTO`
\+ `uniqueCode`、`sourceId`/`bizcode`/`publishScene` 都是 `"pcMainPublish"`。

本系统的做法（[xianyu_core/publish.py](../xianyu_core/publish.py)、[app/drafts.py](../app/drafts.py)）：草稿和图片先存在本机
（图片在 `data/images`），点发布时依次查发布限制、违禁词，把还没传过的图传到闲鱼（传过的地址记在草稿里，重试不再传），
查服务，最后发布。推荐只按描述推荐，不带图（图还在本机）。发布在限流里算"改动"，传图算"读"。
发货地可以从 `poi.get` 的常用地址里选，也可以用高德按名字搜（见上面"发货地"）。

## 订单

> 来源：2026-10-29 读网页端 xy-site 0.0.179 的 `p_order-detail-index.js`、`p_im-index.js`、`p_bought-index.js`，
> 卖家工作台 seller-workbench 0.1.68，以及 Ydisks-Xianyu-Helper（Apache-2.0）的 `internal/xianyu/mtop/`。
> ✅ 是用个人卖家账号实测过的（只读）；🔍 是网页端代码里看到的调用，没在线上调过；📦 只在开源实现里见过。

网页版主站只有"我买到的"（`mtop.idle.web.trade.bought.list`），**没有卖出的订单列表**，本系统改从交易消息里记
（见下面"即时消息"里的[交易消息里的订单号](#交易消息里的订单号)）；卖出的订单在卖家工作台
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
  `seller`、`tradeStatusEnum`（见过 `trade_success`、`refund_success`）等。实测里 `seller` 全是 false，只有买到的订单。
  本系统用它同步买到的订单（`OrderApi.iter_bought`，2026-11-02 实测 62 笔）：
  - 每页 10 条；第二页起网页端带 `offsetRow`（上一页返回的 `lastEndRow`），`nextPage` 是字符串 `"true"`；
  - 订单号用 `commonData.orderIdStr`（`orderId` 超出 JS 的安全整数）；对方是 `commonData.peerUserId` 和
    `head.data.userInfo.userNick`（卖家）；状态文字在 `head.data.statusViewMsg`，下单时间 `head.data.createTime`
    （"2026-10-19 20:57:22"）；商品在 `content.data.detailInfo`（`auctionTitle`、`auctionPic`），
    实付款和数量在 `content.data.priceInfo`（`price`、`buyAmount`）；
  - 状态文字除了"交易成功"、"交易关闭，有退款"、"等待买家付款"，还见过"已刀成,待卖家发货"、"等待见面交易"；
    文字认不出来时按 `tradeStatusEnum` 归类（见过 `buyer_to_pay`、`seller_to_send`、`buyer_to_confirm`、
    `trade_success`、`refund_success`）；
  - 买到的订单详情（`order.detail`）里 `seller` 是 false，`peerUserId` 是卖家，订单信息里是"卖家昵称"；
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

## 搜索

> 来源：2026-11-02 读网页端 xy-site 0.0.179 的 `p_search-index.js`（goofish.com/search 页），
> 用个人卖家账号实测了 7 次（综合、翻第 2 页、价格区间 + 个人闲置、最新发布），没碰到风控。

`mtop.taobao.idlemtopsearch.pc.search` v1.0，`spm_cnt=a21ybx.search.0.0`。和别的接口一样直接签名调用就行，
不用开浏览器（ai-goofish-monitor、goofish-cli 是开浏览器让页面自己搜、截响应）。网页端搜之前会等 baxia
（`window.__baxiaReady`）准备好，纯 HTTP 调没有这一步，目前没影响。

请求体（不排序、不筛选时）：

```json
{"pageNumber": 1, "keyword": "机械键盘", "fromFilter": false, "rowsPerPage": 30, "sortValue": "", "sortField": "",
 "customDistance": "", "gps": "", "propValueStr": {}, "customGps": "", "searchReqFromPage": "pcSearch",
 "extraFilterValue": "{}", "userPositionJson": "{}"}
```

- 排序：`sortField` / `sortValue` 取 `modify/desc`（最近活跃）、`credit/credit_desc`（信用）、`reduce/desc`（新降价）、
  `create/desc`（最新发布）、`price/asc`、`price/desc`；综合是两个空串；
- 筛选拼在 `propValueStr.searchFilter` 里，按这个顺序：`publishDays:7;`（1、3、7、14）、`priceRange:30,60;`（元；
  没填的最低价是空串，没填的最高价是 JS 的 `undefined`，原样写成 `undefined`）、
  `quickFilter:filterPersonal,filterFreePostage;`（个人闲置、包邮；还有 `filterNew` 全新、`filterAppraise` 验货宝、
  `gameAccountInsurance` 验号担保、`filterHighLevelYxpSeller` 超赞鱼小铺、`inspectedPhone` 严选、`filterOneKeyResell` 转卖）；
- 有排序或筛选时 `fromFilter` 是 true，`propValueStr` 是 `{searchFilter: "..."}`（只排序时是空串）；
- 网页端排序和发布天数在同一个下拉里，只能选一个；本系统两个一起传，平台照样认；
- 地区（`extraFilterValue.divisionList`）、距离（`gps`、`customDistance`）没做。

返回：
- `resultList[]` 每条的 `data.item.main` 下：`exContent` 里有 `itemId`、`title`（有的卖家把描述也塞进来，很长）、
  `picUrl`（不少是 heic，要加 `_<n>x<n>.jpg` 转）、`price`（分几段：`sign` ¥、`integer`、`decimal`，可能有 `unit` 万）、
  `oriPrice`（"¥199"，没填就没有）、`area`（省或市）、`userNickName`；`clickParam.args` 里 `price`（"49.90"，最好用）、
  `publishTime`（毫秒）、`tag`（`/` 分隔，`freeship` 是包邮）。`args.wantNum` 总是 0，想要人数要看标签；
- 标签在 `exContent.fishTags`：`r2` 成色、品牌、属性和"22天内降价"，`r3` "1434人想要"、"累计降价13%"，
  `r4` "卖家信用极好"、"回复超快"，`r1`、`r5` 是图标（`type: img`，包邮图标之类）；
- `resultInfo.hasNextPage` 有没有下一页；`resultInfo.searchResControlFields.numFound` 找到多少件。
  不筛选时实测是 1500（= 50 页 × 30，网页端最多翻 50 页），加了筛选反而是 41245，所以 1500 应该是能翻到的上限，
  不是真实件数；
- 网页端代码里还有另一种结构：`resultInfo.trackParams` 是空对象时，每条直接是 `data.id`、`data.title`、`data.price`……
  实测没碰到过，本系统先不认（`SearchCard.parse` 跳过认不出的行）。

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

扫码登录后的做法：先查一次，`canOpenLongLogin` 为 true 且还没开时才提交 `status=0`，提交后再查一次确认。
网页端不显示开关的时候（`canOpenLongLogin` 为 false）不提交，免得发出网页端不会发的请求。

2026-10-29 实测：当天扫码登录的账号查出来是 `canOpenLongLogin: false`、`hasLongTokenLogin: false`，凭证里也没有长登录标记。
同一天用新代码重新扫码（落到 `/im` 后马上查）也一样是 `canOpenLongLogin: false`，所以没有提交 `setLoginSettings.do`，
新凭证里还是没有 `havana_lgc_exp` / `cookie3_bak_exp`，也没有 `sdkSilent`。
🔍 为什么不能开还不知道：可能是账号本身不支持，也可能和登录方式（纯 HTTP 扫码、`umidTag=SERVER`、没走登录框的
`keepLoginConfirm` 弹窗）有关。可以在浏览器里正常登录一次 goofish.com，看右上角个人信息弹窗里有没有"保存登录信息"开关来区分。

#### 扫码登录时的"保持登录状态"弹窗

> 来源：2026-10-30 读登录框 `x.alicdn.com/vip/havana-nlogin/0.10.38/index.js`（SHA-256 `1603531d…`）和
> `mini_login.htm` 里的 `window.viewConfig`。🔍 还没在线上见过真正的弹窗响应。

扫码视图的 `moduleCfg.blockCfg.keepLoginConfirm` 配了一个弹窗：标题"保持登录状态"，内容"保持登录，下次访问无需登录即可查看账号信息"，
两个按钮"不保持"（`dynamicUrlCode=dialogRefuseLoginUrl`，8 秒后自动选它）和"保持"（`dialogConfirmLoginUrl`）。密码、短信登录也有这个弹窗，
另外还有"保持登录"复选框（请求里的 `keepLogin`）；扫码登录没有复选框，只能靠弹窗。

- `query.do` 返回 `CONFIRMED` 后，登录框把 `content.data` 交给 `handleLoginResult`：`dialogAction` 是弹窗类型，
  `feedbackPayload.dynamicUrl` 是各按钮要提交的地址（平台给，可能是相对地址）。要弹窗时结果里不会同时有 `redirect`，
  登录要等用户选完才往下走；
- 点按钮：POST 那个地址，请求体只有登录框每个 POST 都带的 `deviceId`（浏览器 localStorage 的 `APLUS_CNA`，新浏览器是空的）
  和 `pageTraceId`（登录页 `<meta name="eagleeye-trace">`）；返回的 `content.data.resultCode === 100` 才算成功，
  然后照常处理登录结果（`redirect` 时整页跳到 `redirectUrl`）；
- 我们以前不看 `dialogAction`、不提交，等于一直选了"不保持"。这可能就是上面开不了保持登录的原因。

`QRLogin` 现在的做法：`dialogAction` 是 `keepLoginConfirm` 时替用户选"保持"，提交 `dialogConfirmLoginUrl`（只发给
`https://passport.goofish.com`，别的地址不发）；成功后有本站的 `redirectUrl` 就跟一下，再照常访问 `/im`、查一次保持登录。
弹窗提交失败只记日志，不影响登录。确认结果和弹窗结果的字段名用 INFO 级别记日志（`xianyu -v login` 能看到），不记值。

2026-10-30 实测（同一个账号再扫一次）：确认结果的字段是 `appEntrance`、`loginResult`、`loginScene`、`loginSucResultAction`、
`loginType`、`processFinished`、`qrCodeStatus`、`resultCode`、`smartlock`、`st`，**没有** `dialogAction`，平台这次没要弹窗；
查出来还是 `canOpenLongLogin: false`。所以上面这个账号开不了保持登录，不是因为没回弹窗。
- 登录框拿到这种结果只做一件事：有 `st` 就 postMessage 给外层页面；`smartlock`、`loginSucResultAction`、`loginScene` 登录框都不看；
- 外层页面（xy-site 0.0.179 `p_layout.js` 的登录弹层）收到 `loginResult` 且 `dialogAction` 不是 `keepLoginConfirm` 时直接
  `location.reload()`，不拿 `st` 换别的东西。登录 cookie 在 `query.do` 那一跳已经种好了，和我们的做法一样；
- 个人信息弹窗的"保存登录信息"开关只在 `canOpenLongLogin` 为 true 时显示（同一个文件），和我们查的是同一个值。
✅ 2026-10-30 同一个账号在用户自己的浏览器里登录 goofish.com，头像菜单里**有**"保存登录信息"开关，而且是开着的。
所以账号本身能开，差别在登录环境：浏览器登录框带的是 um.js 采集设备信息算出来的 `umidToken`，我们是登录页给的
`umidToken` 加 `umidTag=SERVER`。伪造设备指纹是在绕平台的风控，不做。

#### 导入浏览器的登录

替代办法：在浏览器里登录、打开"保存登录信息"，把那份登录的 cookie 整串交给管理后台（`POST /api/login/import`）。
平时用 Chrome 插件（`extension/`）自动推，登录弹窗里的「导入浏览器登录」是手动粘贴的备用办法。2026-10-30 手动粘贴实测成功，
导入的凭证带 `havana_lgc_exp`（到 30 天后），还带着浏览器那边的 `sdkSilent`。
- 从哪复制：开发者工具"网络"里 `www.goofish.com` 那条文档请求的 `Cookie` 请求头。续期插件在 `www.goofish.com` 上读
  `havana_lgc_exp` / `cookie3_bak_exp`，所以这两个 cookie 对 www 可见，会出现在这个请求头里；`cookie2` 是 HttpOnly，
  `document.cookie` 拿不到，请求头里有；
- 后台只要求有 `unb` 和 `cookie2`（`_m_h5_tk` 没有也行，第一次请求平台会发），先调 `loginuser.get` 确认登录态有效、
  `userId` 等于 `unb` 才保存，失效的 cookie 不会盖掉现有凭证；
- 确认时**不续期**：插件会反复推同一份 cookie，浏览器那份过期了还替它续，就是每推一次续一次，绕开了 `sdkSilent` 限频。
  过期的直接拒绝（401），后台留着自己那份，自己的会话过期时照常续；浏览器那份等网页端自己续上、cookie 变了再推；
- `unb`、`cookie2`、`havana_lgc_exp`、`cookie3_bak_exp` 都和已保存的一样就算同一份登录，不请求平台，直接返回 `changed: false`；
- 插件那边：这几个 cookie 变了（防抖 3 秒）、每 10 分钟、浏览器启动、点"立即同步"时推；登录指纹没变不推；
  4xx（过期、缺字段、不是这个账号）记下这份指纹不再重推，连不上后台、429、5xx 下次定时再试；
- 导入后浏览器和后台共用这次登录：浏览器里退出登录，后台也失效。两边各自免密续期会不会互相顶掉还没验证过。

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
- 网页端每打开一个页面就续一次；管理后台没人用的时候也要续，所以 `xym serve` 每 30 分钟调一次 `loginuser.get`（`app/keepalive.py`），
  会话过期就走上面的续期。失效的账号只在续期有希望时（保持登录没到期、`sdkSilent` 过了）再试，最快 2 小时一次；风控的不碰。
  续期成功和失效都用 WARNING 记日志，在 `xym serve` 的终端里能看到，可以从这里看出会话实际多久过期。

几个还不知道的数（等定时确认跑一阵再补）：
- 🔍 会话（`cookie2`）多久过期；
- 🔍 `sdkSilent` 一般设多久：2026-10-30 只见过一次，浏览器 07:02 登录后是到第二天 08:00；
- 🔍 续期时平台会不会顺延 `havana_lgc_exp`。不顺延的话，登录 30 天后要在浏览器里重新登录一次，页面会提前 3 天提醒。

## 即时消息

**收消息是服务端实时推送，不是轮询。**网页版 `/im` 用的是钉钉的 IMPaaS：一条 WebSocket 长连接，新消息由服务端推下来。
轮询只在断线重连后补漏时用到，而且也是走同一条 WebSocket 发请求，不是另开 HTTP 接口去轮询。
（2026-10-30 读 Ydisks 的 `internal/xianyu/ws` 和 goofish-cli 的 `core/ws.py` 整理，都是 Apache-2.0。
标了 ✅ 的当天用自己的账号实测过，测试程序只读：没发消息、没标已读（发消息那一条除外，见第 9 条）。
✅ 连着的时候在闲鱼 App 里发一条消息，0.3 秒后就推到了这条连接上。测的是自己在 App 里发出去的消息，
别人发来的还没测；开源项目说格式一样。）

1. ✅ 调 `mtop.taobao.idlemessage.pc.login.token`，data 是 `{"appKey": "444e9908a51d1cb236a27862abc769c9", "deviceId": <did>}`，
   `spm_cnt` 用 `a21ybx.im.0.0`。返回 `accessToken`、`accessTokenExpiredTime`、`refreshToken`，token 24 小时后过期。
   Ydisks 说网页版每次连接（包括重连）都重新取一个，底座也这样。
   Ydisks 还说这里的 `deviceId` 必须和后面 `/reg` 的 `did` 一样，否则注册报 "device id or appkey is not equal"（没试过不一样）；
2. ✅ 连 `wss://wss-goofish.dingtalk.com/`，握手带 `Origin: https://www.goofish.com`、cookie 和 UA，1 秒左右连上；
3. ✅ 发 `/reg` 注册：`app-key` 同上，`token` 是 accessToken（Ydisks 先 URL 解码；实测拿到的 token 原样用也行），
   `did` 是设备 ID，`sync` 填 `0,0;0;0;`，`ua` 是浏览器 UA 后面接一串 `DingTalk(2.2.0) … IMPaaS`。
   返回 `code: 200`，body 是 `{"unitName": "PNM", "timestamp": …, "isFromChina": …}`；
4. ✅ 注册后服务端先推一帧 `/s/sync`（没有消息条目，`syncExtraType.type` 是 2），再推 `/s/vulcan`，这时才算就绪。
   Ydisks 和 goofish-cli 都说就绪前发 `/r/` 请求会被 `code 400` 拒绝（没试）。
   `/s/vulcan` 里是最近会话的"激活"事件，实测 37 条，`operation.content.contentType` 是 8，
   `operation.sessionInfo.extensions` 里有 `itemId`、`itemTitle`、`ownerUserId`、`extUserId`；
5. ✅ 每 15 秒发一次心跳 `/!`，回 `code: 200`，一般 260 毫秒左右，慢的时候 1.4 秒。10 分钟 39 次心跳，连接没断过。
   开源项目的做法是 30 秒没回应就断开重连，按连续失败次数退避：5 秒、15 秒、30 秒、1 分钟、2 分钟；
6. ✅ **收消息**：服务端推带 `body.syncPushPackage.data[]` 的帧，一帧里可能有好几条，每条的 `data` 是 base64。
   `/s/vulcan` 里的会话激活事件 base64 解出来就是 JSON；聊天消息走 `/s/sync`，条目的 `objectType` 是 40000，
   base64 解出来是 MessagePack，见下面"聊天消息的推送"。
   每帧都原样带着它的 headers 回一次 ACK（`{"code": 200, "headers": …}`，一帧只回一次，不管里面有几条），服务端没有异议；
7. ✅ 推送带 `syncExtraType` 1 或 2 时，发 `/r/SyncStatus/getState`（body `[{"topic": "sync"}]`），
   再把它返回的 body 原样放进 `/r/SyncStatus/ackDiff`，两个都回 200；
8. 补漏和看历史（都是 WebSocket 上的请求）：
   - ✅ 会话列表 `/r/Conversation/listNewestPagination`，body `[9007199254740991, 20]`（游标取最大值表示从最新一页开始）。
     返回 `hasMore`、`nextCursor`、`userConvs[]`；每个会话在 `singleChatUserConversation` 下，有 `redPoint`（应该是未读数）、
     `modifyTime`，`singleChatConversation` 里有 `cid`、`pairFirst` / `pairSecond`（双方 ID）、`extension.ownerUserId` / `extUserId`；
     `extension` 里还有 `itemId`、`itemTitle`、`itemMainPic`、`ownerUserType` / `extUserType`（用户类型，普通用户 `0`，
     系统账号 `10`）、`squadId_<userId>` / `squadName_<userId>`。
     ✅ 2026-10-31 更正：**`squadName_<userId>` 不是昵称**，是商品的简称（294 个商品会话里 152 个和商品标题一样，
     别的是"取暖器"这类短名）。会话列表本身不带对方昵称，只有两种办法拿：
     `singleChatUserConversation.lastMessage`（和历史消息同一个格式）是对方发的文本时，它的 `reminderTitle` 就是昵称；
     否则调 mtop `mtop.taobao.idlemessage.pc.user.query`（见下面接口表），网页版也是每个会话查一次。
     ✅ 每页 50 个可以；按 `modifyTime` 从新到旧，`nextCursor` 是下一页第一个会话的 `modifyTime`（毫秒），下一页从它开始、包含它。
     自己的账号一共 313 个会话，7 页翻完；`bizType` 是 `"1"` 的商品会话 293 个，别的是交易通知、活动之类的系统会话，
     未读数大多在系统会话上（336 对 4）。`/s/vulcan` 里的 37 条只是最近会话的激活事件，不是会话总数；
     ✅ 2026-10-31 读网页版 JS：`bizType` 就是网页版的会话类型（`sessionType = parseInt(bizType)`，`visible` 是
     `singleChatUserConversation.visible === 1`）。网页版会话列表只显示 `visible` 而且类型在
     `[1, 19, 15, 32, 3, 44, 51, 52, 24]` 里的（灰度名单里的账号再加群聊 47），未读数、提示音也只算这些。
     ✅ 自己账号 294 个商品会话里 13 个 `visible` 是 0（在 App 里删掉了），网页版不显示、不算未读，本系统也一样；
     `muteNotification` 是免打扰，全都是 0。网页版的会话列表是分页加载的，滑到底才加载更早的会话，红点和未读数
     只算已经加载的（2026-10-31 在网页版上看到的）。所以网页版刚打开时未读比本系统少：自己账号有 4 个很久以前的会话
     `redPoint` 是 1，网页版要往下翻到它们才算上；本系统一次列出所有会话，都算。网页版代码里还调
     `mtop.taobao.idlemessage.pc.redpoint.query`（`{sessionTypes: "1,19,15,32,3,44,51,52,24", fetch: 50}`，只回 `total`），
     实测 fetch 50 和 500 都回 0，它算的是什么还不清楚，本系统不用。`session.sync`（3.0）只回通知类的推送会话
     （类型 23、25 之类），没有商品会话；
     其中 3 是"通知消息"（另走 mtop 的 `session.sync` 拉，内容是"本周闲鱼币优推抵扣"这类推广），24 是"闲小蜜"
     （客服机器人，"邀您填写售后问卷"，发送人 ID 1400）。自己账号的系统会话还有 6、10、14、23、25、37、62、63、66，
     对方 ID 大多是 `0`，网页版都不显示。消息提醒里的 `sessionType` 和会话的 `bizType` 是同一个值，推送也能按它分。
     本系统只存和人的聊天：类型在网页版名单里、去掉 3 和 24、对方不是 `0`（`xianyu_core/im.py` 的 `USER_SESSION_TYPES`）；
   - ✅ 某个会话的消息 `/r/MessageManager/listUserMessages`，body `["<cid>@goofish", false, 游标, 条数, false]`。
     返回 `hasMore`、`nextCursor`、`userMessageModels[]`，自己发的和对方发的都在里面。每条的 `message` 下有
     `messageId`、`cid`、`createAt`（毫秒）、`sender.uid`、`content.custom`（`type` 1 是文本，`summary` 是摘要，
     `data` 是 base64 的 JSON，解出来和推送里的 `1.6.3.5` 一样）、`extension`（和推送里的 `1.10` 一样，
     也带着发送端的设备信息）。
     ✅ 一页 20 条（网页版也是 20），从新到旧。`nextCursor` 是下一条更早消息的 `createAt`（毫秒），传回去从它开始、包含它，
     页与页不重叠；拿本页最早一条的 `createAt` 当游标会多出这一条。翻到头 `hasMore` 是 0。
     消息类型不止文本：14 是交易卡片（"[卖家已发货]"之类），25 是评价提醒，`summary` 都是给人看的摘要。
     ✅ 2 是图片，`summary` 是"[图片]"，内层是 `{"contentType": 2, "image": {"pics": [{"url", "width", "height", "type": 0}]}}`，
     url 是 `img.alicdn.com/imgextra/…` 的原图，后面接 `_400x400.jpg` 能拿缩略图；
     这些卡片的 `sender` 也是对方，但 `reminderTitle` 是"交易消息"、"快给ta一个评价吧～"这样的标题，不是昵称；
     ✅ 2026-10-31：文本消息也不一定，卖家设的自动回复 `reminderTitle` 是"你有一条新消息"。分法是看 `reminderNotice`：
     人发的是"发来一条新消息"（本地存的 67 条对方文本消息里 63 条，标题都是昵称），自动回复是"～"（4 条）；
     交易卡片（14）、评价（25、26）有时也是"发来一条新消息"，标题多半是昵称，但也见过"交易消息"。
     所以只认"发来一条新消息"而且内容类型是 1（文字）或 2（图片）的，它们的标题全是昵称；
9. ✅ 发消息走 `/r/MessageSend/sendByReceiverScope`。2026-10-31 照网页版 `/im` 的 JS 整理，和 Ydisks、goofish-cli 的写法一致。
   body 是两项：
   - 消息：`uuid`（`-`、毫秒时间戳、再接一个从 1 开始递增的序号）、`cid`（带 `@goofish`）、`conversationType: 1`、
     `content: {contentType: 101, custom: {type: 1, data}}`、`redPointPolicy: 0`、`extension: {extJson: "{}"}`、
     `ctx: {appVersion: "1.0", platform: "web"}`、`mtags: {}`、`msgReadStatusSetting: 1`。
     `data` 是 `{"contentType":1,"text":{"text":…}}` 的 base64，网页版用 `JSON.stringify` 转，没有空格；
   - 接收方：`{actualReceivers: [对方@goofish, 自己@goofish]}`（网页版取会话的 `pairFirst`、`pairSecond`）。

   网页版要 `code` 是 200、而且 body 里有 `messageId` 才算发出去。
   ✅ 2026-10-31 用自己的账号在一个会话里发了一条「好的，谢谢」（征得同意，只发了这一条）：0.6 秒回 200，
   body 有 `messageId`（`<13 位数字>.PNM`）、`uuid`、`createAt`、`content`、`extension`、`receiverCount`、`unreadCount`、
   `msgReadStatusSetting`、`msgReadStatusDowngrade`；`extension` 和推送里的 `1.10` 一样（`reminderTitle` 是自己的昵称，
   `reminderUrl` 里有商品 ID，`_platform` 是 `web`），也带着发送端的设备信息，要去掉。对方的自动回复同一秒推了过来，说明送到了。
   **自己发的这条不会推回发它的这条连接**（等了 40 秒没有），所以发完直接用响应存库；
   在 App 里发的会推过来（见上面），两种都按消息 ID 去重。
   失败时 body 里有 `reason`、`moreInfo`：`moreInfo` 含 `USER_NEED_ALIPYA_BIND`（要绑支付宝、实名认证）或
   `USER_NEED_VERIFY` / `USER_NEED_REALNAME_VERIFY`（要验证身份）时，网页版弹窗让人去 App 里处理。
   没等到回应时消息可能已经发出去了，底座抛 `ImSendUncertainError`，不自动重发。
   标已读分两件事（2026-10-31 读网页版 JS）：
   - 清会话红点（自己这边的未读数）：`/r/Conversation/clearRedPoint`，body `[[{"messageId": 会话最新一条的 ID,
     "cid": "<cid>@goofish"}]]`。网页版点开 `redPoint` 大于 0 的会话时调一次，用的是会话列表里 `lastMessage` 的 ID。
     本系统在页面上点开有未读的会话、页面在前台时调。✅ 2026-10-31 在页面上点开 3 个有红点的会话，都回 200；
     之后重连、重新拉会话列表，`redPoint` 都是 0，平台上确实清掉了；
   - 已读回执（对方看到"已读"）：`/r/MessageStatus/read`，body `[[messageId]]`（Ydisks 说一次一条，只认 `.PNM` 结尾的 ID）。
     网页版是消息滚到眼前时发，一部分走这个，一部分走一个 mtop 接口（带 `sessionId`、`msgId`、`peerUid`），还没做；

   **对方读没读自己发的消息**（2026-11-04 读网页版 JS `idle-pc/xy-site/0.0.179/js/p_im-index.js`）：
   - 每条消息带 `receiverCount`（接收方个数）和 `unreadCount`（还有几个没读）。历史消息、会话列表的 `lastMessage`
     在 `message` 下，推送在 `1.7`、`1.8`，发消息的响应 body 里也有。网页版在自己发的、发送成功的消息旁边
     显示 `unreadCount === 0 ? "已读" : "未读"`，只有群聊（会话类型 47）不显示；
   - 对方读了以后，平台给发消息的人推一个条目，`objectType` 是 **40103**（网页版的 `MsgReadToSenderEventModel`），
     MessagePack 解出来是 `{1: messageId, 2: receiverCount, 3: unreadCount, 4: "<cid>@goofish", 5: 会话类型,
     6: 时间（毫秒）}`。网页版收到后把那条消息的 `unreadCount` 换成它。Ydisks 也是按 40103 认已读回执，
     但把第 2 项当成了"状态（2 是已读）"，其实是接收方个数，单聊里一直是 2；
   - 40102（`MsgReadToReaderEventModel`，`{1: [messageId…], 2: 2, 3: cid}`）是推给读的人自己的：
     在别的设备上读了对方的消息。本系统没用；
   - 历史消息外层的 `readStatus`（2 已读、0 未读）说的是**当前账号**读没读这条，对方发来还没看的是 0；
   - ✅ 本地存的 97 条自己发的消息里 79 条 `unreadCount` 是 0、14 条是 1，另外 4 条是以前自己发的时候没存这个数。
     还没实测过 40103 推下来的样子，格式照网页版 JS 和 Ydisks；
10. 被踢下线：服务端推 `/push/kickout`（别处登录了，按登录失效处理）或 `/s/session/remove`（连接数超限）。

`did` 在 Ydisks 里是随机 UUID（大写）加 `-<unb>`，goofish-cli 也按账号存下来一直用。按[设备指纹的边界](todo.md#1-设备指纹降低风控)，
不能每次连接都新生成一个：要么沿用浏览器里网页版已有的那个，要么每个账号只生成一次、存下来一直用。
实测用的是后一种，存在凭证旁边的 `data/accounts/<unb>.im.json`；同一个 did 前后连了好几次，都没被踢。
✅ 2026-10-31：同一个账号、同一个 did **同时**开两条连接（两个 `xym serve` 进程）会互相挤：新的一条连上，旧的那条就被断开，
服务端不推 `/push/kickout`，直接断（websockets 报 "no close frame received or sent"）。两边都当成断网重连，
3 分钟里各断了 4 次，停掉一边以后另一边马上稳定。所以一个账号同一时间只能有一个 `xym serve` 在连，
`MessageHub` 连之前先抢 `data/accounts/<unb>.im.lock` 上的文件锁（flock），抢不到就不连，等那边退出。
网页版用的是它自己的 did，会不会被挤还没测。

### 聊天消息的推送

✅ 2026-10-30 实测一条文本消息。MessagePack 解出来是嵌套的字典，键都是数字字符串，下面用点号写路径：

| 路径 | 内容 |
|---|---|
| `1.1.1` | `<userId>@goofish`。这次是自己发的，它和 `senderUserId` 都是自己，分不出它是发送人还是当前账号，以 `1.10.senderUserId` 为准（历史消息里对应的 `sender.uid` 是发送人） |
| `1.2` | 会话 ID，`<cid>@goofish`，和会话列表里的 `cid` 对得上 |
| `1.3` | 消息 ID，形如 `<13 位数字>.PNM` |
| `1.5` | 发送时间，毫秒时间戳 |
| `1.6.1` | 外层内容类型，聊天消息是 101（自定义消息） |
| `1.6.3.4` | 内层内容类型，1 是文本 |
| `1.6.3.5` | 内层内容，是 JSON 字符串，不是 base64：`{"contentType": 1, "text": {"text": …}, "atUsers": []}` |
| `1.6.3.2` | 摘要，文本消息就是正文 |
| `1.7`、`1.8` | 接收方个数、还有几个接收方没读（`receiverCount`、`unreadCount`）。自己发的消息上 `1.8` 是 0 就是对方读了，见上面第 9 条 |
| `1.10` | 提醒信息：`senderUserId`、`reminderTitle`（人发的消息是发送人昵称，交易卡片、自动回复是"交易消息"、"你有一条新消息"这样的标题，用 `reminderNotice` 分，见上面第 8 条）、`reminderContent`（正文）、`sessionType`、`extJson`、`bizTag`（都有 `messageId`）。`reminderUrl` 形如 `fleamarket://message_chat?itemId=…&peerUserId=…&sid=…&messageId=…`，商品 ID 从这里取。✅ `peerUserId` 是**发送人**，不是会话里的对方：这个链接是给收消息的人点的，自己发的消息里它就是自己。对方是谁看会话列表 |
| `3.needPush` | 要不要给手机推通知，这次是 `"false"` |

`1.10` 里还带着发送端的 `clientIp`、`umid`、`umidToken`、`utdid`，是发消息那台设备的信息，不要存，也不要写进日志。

### 交易消息里的订单号

✅ 2026-11-01 读本地存的消息（自己账号 8 笔订单的卡片，1 笔卖出、7 笔买到）整理。订单的每一步平台都往会话里发一条消息，
`sender` 多半是对方（自己操作的那几步是自己），提醒里的 `receiver` 是当前账号，卡片是按收的人给的：
同一步买卖双方看到的文字和链接不一样。内容类型有这几种：

| 内容类型 | 例子 | 订单号在哪 |
|---|---|---|
| 26 交易卡片 | "[我已拍下，待付款]"、"[我已付款，等待你发货]"、"[我发起了退款申请]"、"[我完成了评价]" | `dxCard.item.main.targetUrl` 是 `fleamarket://order_detail?id=<订单号>&role=<角色>`；退款卡片是 `…/refundDetail?…&orderId=<订单号>`；按钮的 `exContent.button.targetUrl` 也常带（"修改价格"是 `fleamarket://adjust_price?…&bizOrderId=<订单号>`） |
| 25 评价 | "快给ta一个评价吧～" | 按钮里的评价页链接 `…?orderId=<订单号>` |
| 6 物流 | "[物流已签收]" | `textCard.content` 是一段 HTML，里面的 `order_detail?id=…&role=…` 链接 |
| 14 交易提示 | "[你已发货]"、"[买家确认收货，交易成功]"、"[卖家已发货]" | **大多没有**，只有 `tip.tip` 一句话；个别（"有蚂蚁森林能量可领"）在 `tip.argInfo.args.orderId` |

- `role` 是**当前账号**在这笔订单里的角色：卖出的是 `seller`，买到的是 `Buyer` 或 `buyer`（大小写不定）。
  8 笔订单的卡片角色都和实际一致，同一笔订单的几张卡片也一致；
- 提醒里 `bizTag` 的 `taskName` 以"_卖家"或"_买家"结尾（"已拍下_未付款_卖家"、"付款完成待发货_无需发货_买家"、
  "已发货_卖家"），说的也是当前账号的角色。链接里没写 `role` 时用它；活动类的提示（"神奇鱼塘-蚂蚁森林能量"）没有；
- `extJson.updateKey` 形如 `<cid>:<订单号>:1_not_pay_seller` 或 `<cid>:<订单号>:64:TRADE_PAID_DONE_BUYER:26`，
  也带订单号，本系统没用；
- 会话列表里会话的 `extension.orderId` 是这个会话最近一笔订单，但看不出自己是买家还是卖家。

本系统的做法（`xianyu_core/im.py` 的 `_order_ref`、`app/orders.py` 的 `OrderFeed`）：内容类型不是 1、2（人发的文字、图片，
里面贴的链接不算）的消息，先找 `order_detail?id=` 链接，再找 `orderId=` / `bizOrderId=`，角色取链接里的 `role`，没有就看
`taskName`。新存进本地库的消息里有订单号、角色不是买家的，拉一次订单详情（`seller` 为假的不记）记进本地库。
开源项目的做法差不多：Ydisks 从卡片 `targetUrl` 里取订单号和 `role`（`internal/automation/event_order_identity.go`），
别的项目按"[我已付款，等待你发货]"这类文字认付款。

## 开源项目里常见的 mtop 接口

| 接口 | 用途 |
|---|---|
| `mtop.taobao.idlemessage.pc.login.token` | 即时消息的 accessToken。✅ 见上面"即时消息" |
| `mtop.taobao.idlemessage.pc.loginuser.get` | 当前登录用户。✅ 只返回 `userId`（等于 cookie 里的 `unb`），没有昵称 |
| `mtop.idle.web.user.page.head` | 个人主页头部，`{"self": true}` 查自己。✅ 昵称在 `module.base.displayName`，见下面"账号资料" |
| `mtop.taobao.idlemessage.pc.user.query` | 会话里对方的昵称。✅ v4.0，data `{type: 对方的用户类型, sessionType: 会话的 bizType, sessionId: 会话 ID, isOwner: false}`（不传对方 ID；对方类型看 extUserId 是不是自己，是的话取 ownerUserType，否则取 extUserType）。返回 `userInfo` 里的 `fishNick`（闲鱼昵称）、`nick`（打了码的账号名，"x***0"）、`logo`；网页版显示 `fishNick`，没有才用 `nick` |
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
| `mtop.taobao.idlemtopsearch.pc.search` | 搜索。✅ 见上面"搜索" |
| `mtop.idle.web.user.page.nav` | 个人主页导航 |

## 账号资料

账号矩阵（`app/matrix.py`）显示每个账号的昵称、头像这些，用的是个人主页头部 `mtop.idle.web.user.page.head`，
请求体 `{"self": true}`（查自己，不用传 ID），只读，网页端打开"我的主页"时也调它。解析在 `xianyu_core/profile.py`。

✅ 2026-11-04 用自己的账号实测，`data` 里是 `baseInfo`、`module`、`needDecryptKeys`：

- `module.base`：`displayName` 昵称；`avatar.avatar` 头像地址（`http://img.alicdn.com/bao/uploaded/...jpg`，
  页面上换成 https，加 `_80x80.jpg` 取小图）；`ipLocation`（"广东省"）；`introduction` 简介；
  `ylzTags` 信用标签，`code` 是 `cs_seller_level` / `cs_buyer_level`，`text` 是"卖家信用极好"，`attributes.level` 是等级（5）；
- `module.social`：`followers`、`following`，数字字符串（"3"）；
- `module.tabs`：`item.number` 主页上的宝贝数（数字），`rate.number` 评价数（字符串）；
- `baseInfo.kcUserId` 等于 `unb`，`baseInfo.tags` 是一堆认证标记（实名、芝麻）。

本系统一天拉一次（登录有效时），手动"重新检查登录态"时顺带重拉；拉失败了半小时内不再试。
