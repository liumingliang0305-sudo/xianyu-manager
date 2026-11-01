import { useSearchParams } from "react-router"

/**
 * 页面上的筛选放在网址里（?status=sold）：刷新、复制链接、前进后退都还是这个筛选。
 * 默认值不写进网址；网址里是不认识的值时当默认值。切换筛选用 replace，不给浏览器历史添一条。
 */
export function useUrlParam<T extends string>(
  key: string,
  fallback: T,
  allowed: readonly T[],
): [T, (value: T) => void] {
  const [params, setParams] = useSearchParams()
  const raw = params.get(key)
  const value = allowed.find((v) => v === raw) ?? fallback
  const setValue = (next: T) =>
    setParams(
      (prev) => {
        const p = new URLSearchParams(prev)
        if (next === fallback) p.delete(key)
        else p.set(key, next)
        return p
      },
      { replace: true },
    )
  return [value, setValue]
}
