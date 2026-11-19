// 拼多多商品页（mobile.yangkeduo.com/goods*.html）上读商品数据。
// readPddGoods 由弹窗用 chrome.scripting.executeScript 注入到页面里跑（world: "MAIN"，才读得到页面的 window），
// 所以函数里不能用外面的变量，要用的都写在函数里面。
//
// 商品数据是一个 store 里的 initDataObj（goods、mall），页面有两种渲染法，放的地方不一样：
// - 服务端渲染（2026-11-05 实测）：写进网页的 window.rawData.store；
// - 浏览器里渲染（2026-11-19 实测，拼多多在分组实验）：网页里只有 window.rawData=null，页面脚本自己向接口取数据，
//   交给 React 根组件的 props.store，结构和上面一样。插件只读页面已经取到的，不自己请求拼多多。
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
  // 浏览器里渲染时 store 在 React 根组件的 props 上（#main 下面第二层）：从根往下找，限个数免得卡住页面
  const fromReact = () => {
    for (const el of document.body?.children ?? []) {
      const key = Object.keys(el).find((name) => name.startsWith("__reactContainer$"))
      const queue = key ? [el[key]] : []
      for (let seen = 0; queue.length && seen < 500; seen++) {
        const fiber = queue.shift()
        const found = fiber?.memoizedProps?.store?.initDataObj
        if (found?.goods) return found
        if (fiber?.child) queue.push(fiber.child)
        if (fiber?.sibling) queue.push(fiber.sibling)
      }
    }
    return null
  }
  const rendered = window.rawData?.store?.initDataObj
  const init = rendered?.goods ? rendered : fromReact()
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
