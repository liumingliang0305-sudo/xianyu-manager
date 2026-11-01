// 在页面绘制前按上次的选择（next-themes 存在 localStorage 的 theme）设好深浅色，避免闪一下。
// 单独一个文件、不写在 index.html 里：内容安全策略（CSP）只让页面跑自己站点上的脚本文件，不认内联脚本
try {
  var t = localStorage.getItem("theme") || "system"
  if (t === "dark" || (t === "system" && matchMedia("(prefers-color-scheme: dark)").matches))
    document.documentElement.classList.add("dark")
} catch {}
