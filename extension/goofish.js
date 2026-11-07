// 闲鱼商品页（www.goofish.com/item?id=…）上拿商品详情。
// fetchGoofishItemDetail 由插件后台用 chrome.scripting.executeScript 注入到页面里跑（world: "MAIN"），
// 所以函数里不能用外面的变量，要用的都写在函数里面。
//
// 为什么在页面里拿：管理后台的服务端直接调商品详情接口（mtop.taobao.idle.pc.detail）看别人的商品，
// 第一次就要滑块（2026-11-07 实测，见 docs/protocol-notes.md#看别人的商品详情）；同一个接口，页面自己调没事。
// 所以用页面自己的请求库（window.lib.mtop，lib-mtop 2.7.3）调，参数和页面打开时自己发的那次一样，
// 签名、cookie、安全脚本都是页面自己的，插件不造任何东西。
// 只挑管理后台用得到的字段发过去（描述、图片、价格、计数、状态），别的不带走。

/**
 * 返回 { item }（pc.detail 返回的 itemDO 里挑出来的字段）；没拿到时返回 { error, message }：
 * validate 闲鱼要验证（滑块），gone 商品没了，missing 页面上没有请求库，failed 别的错误
 */
export async function fetchGoofishItemDetail(itemId) {
  const ITEM_KEYS = [
    "itemId",
    "title",
    "desc",
    "soldPrice",
    "originalPrice",
    "wantCnt",
    "browseCnt",
    "collectCnt",
    "itemStatus",
    "itemStatusStr",
    "gmtCreate",
  ]
  const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

  // 请求库和安全脚本是页面加载完以后才挂上的，等一会儿
  for (let i = 0; i < 60 && !window.lib?.mtop?.request; i++) await sleep(250)
  const mtop = window.lib?.mtop
  if (!mtop?.request) {
    return {
      error: "missing",
      message: "闲鱼页面上没找到它自己的请求库：等一会儿再试；还不行就是闲鱼改版了",
    }
  }
  // 网页端搜索前会等安全脚本（baxia）准备好，这里也等一下，最多 3 秒
  await Promise.race([Promise.resolve(window.__baxiaReady), sleep(3000)]).catch(() => {})

  try {
    const resp = await mtop.request({
      api: "mtop.taobao.idle.pc.detail",
      v: "1.0",
      appKey: "34839810",
      data: { itemId: String(itemId) },
      type: "POST",
      dataType: "json",
      accountSite: "xianyu",
      sessionOption: "AutoLoginOnly",
      spm_cnt: "a21ybx.item.0.0",
      timeout: 20000,
    })
    const item = resp?.data?.itemDO
    if (!item) return { error: "failed", message: "闲鱼返回的数据里没有商品详情" }
    const picked = Object.fromEntries(
      ITEM_KEYS.filter((key) => item[key] !== undefined).map((key) => [key, item[key]]),
    )
    picked.imageInfos = (Array.isArray(item.imageInfos) ? item.imageInfos : []).map((img) => ({
      url: img?.url,
      widthSize: img?.widthSize,
      heightSize: img?.heightSize,
    }))
    return { item: JSON.parse(JSON.stringify(picked)) }
  } catch (error) {
    const ret = Array.isArray(error?.ret) ? error.ret.join(" | ") : ""
    if (/USER_VALIDATE|RGV587|哎哟喂/.test(ret)) {
      return {
        error: "validate",
        message: "闲鱼要验证：Chrome 里已经切到那件商品的闲鱼页面，在上面过一下滑块，再回来点「拉详情」",
      }
    }
    if (/NOT_FOUND|NOT_EXIST/.test(ret)) {
      return { error: "gone", message: "闲鱼上这件商品已经删掉或者下架了" }
    }
    return { error: "failed", message: `闲鱼返回：${ret || error?.message || "未知错误"}` }
  }
}
