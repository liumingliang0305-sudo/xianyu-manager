// 拼多多商品页（mobile.yangkeduo.com/goods*.html）上读商品数据。
// readPddGoods 由弹窗用 chrome.scripting.executeScript 注入到页面里跑（world: "MAIN"，才读得到页面的 window），
// 所以函数里不能用外面的变量，要用的都写在函数里面。
//
// 数据在商品页服务端渲染时写进页面的 window.rawData.store.initDataObj（2026-11 实测）。
// 只挑管理后台用得到的字段发过去：页面里还有登录用户、分享参数这些，不带走。

/**
 * 返回 { goods, mall }；页面上没有商品数据时返回 { error, message }：
 * login 没登录（跳到了登录页），mismatch 页面数据和网址里的商品对不上（在页面里点到了别的商品），
 * missing 页面上找不到商品数据（还没加载完，或者拼多多改版了）
 */
export function readPddGoods() {
  const GOODS_KEYS = [
    "goodsID",
    "goodsName",
    "topGallery",
    "detailGallery",
    "videoGallery",
    "descVideoGallery",
    "skus",
    "goodsProperty",
    "minGroupPrice",
    "maxGroupPrice",
    "minNormalPrice",
    "maxNormalPrice",
    "sideSalesTip",
  ]
  const pick = (obj, keys) =>
    Object.fromEntries(keys.filter((key) => obj[key] !== undefined).map((key) => [key, obj[key]]))

  if (location.pathname.startsWith("/login")) {
    return { error: "login", message: "拼多多要先登录：在这个页面登录以后，回到商品页再导入" }
  }
  const init = window.rawData?.store?.initDataObj
  const goods = init?.goods
  if (!goods?.goodsID) {
    return {
      error: "missing",
      message: "这个页面上没找到商品数据：等页面加载完再试；还不行就是拼多多改版了",
    }
  }
  const wanted = new URLSearchParams(location.search).get("goods_id")
  if (wanted && wanted !== String(goods.goodsID)) {
    return { error: "mismatch", message: "页面数据和网址里的商品对不上，刷新一下页面再导入" }
  }
  return {
    goods: JSON.parse(JSON.stringify(pick(goods, GOODS_KEYS))),
    mall: init.mall ? pick(init.mall, ["mallId", "mallName"]) : null,
  }
}
