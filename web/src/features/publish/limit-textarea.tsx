import { useCallback, useLayoutEffect, useRef, type ComponentProps } from "react"

import { Textarea } from "@/components/ui/textarea"
import { countChars } from "@/lib/format"
import { cn } from "@/lib/utils"

/** 影子层照输入框抄的样式：字体、行高、内边距、边框宽度、换行方式，换行位置才对得上 */
const MIRRORED = [
  "font-family",
  "font-size",
  "font-weight",
  "font-style",
  "font-stretch",
  "font-variant",
  "font-feature-settings",
  "font-variation-settings",
  "font-kerning",
  "line-height",
  "letter-spacing",
  "word-spacing",
  "text-indent",
  "text-transform",
  "text-align",
  "text-rendering",
  "tab-size",
  "direction",
  "white-space",
  "word-break",
  "overflow-wrap",
  "line-break",
  "hyphens",
  "padding-top",
  "padding-bottom",
  "padding-left",
  "border-top-width",
  "border-right-width",
  "border-bottom-width",
  "border-left-width",
  "border-radius",
] as const

/**
 * 有字数上限、但不截断的多行输入框：超过 `limit` 个字的部分标红，让人自己挑着删（像发推时超了字数）。
 *
 * textarea 里没法给一段字上色，所以在输入框上面盖一层对齐的影子：字是透明的，只画超出那段的红底和红下划线，
 * 不挡点击。影子的字体、内边距、换行方式照输入框算出来的样式抄，输入框有滚动条时右边让出同样的宽度，
 * 滚动、改大小（窗口变宽变窄、字体加载完）时跟着对齐。输入法正在拼的字也算在里面。
 */
export function LimitTextarea({
  value,
  limit,
  className,
  onScroll,
  ...props
}: Omit<ComponentProps<"textarea">, "value"> & { value: string; limit: number }) {
  const input = useRef<HTMLTextAreaElement>(null)
  const mirror = useRef<HTMLDivElement>(null)
  const { cut } = countChars(value, limit)
  const over = cut < value.length

  const align = useCallback(() => {
    const textarea = input.current
    const shadow = mirror.current
    if (!textarea || !shadow) return
    const style = getComputedStyle(textarea)
    for (const name of MIRRORED) shadow.style.setProperty(name, style.getPropertyValue(name))
    // 竖滚动条占的是内容区的宽度，影子没有滚动条，右内边距补上
    const scrollbar =
      textarea.offsetWidth -
      textarea.clientWidth -
      parseFloat(style.borderLeftWidth) -
      parseFloat(style.borderRightWidth)
    shadow.style.paddingRight = `${parseFloat(style.paddingRight) + Math.max(scrollbar, 0)}px`
    shadow.scrollTop = textarea.scrollTop
    shadow.scrollLeft = textarea.scrollLeft
  }, [])

  useLayoutEffect(() => {
    const textarea = input.current
    if (!textarea) return
    const observer = new ResizeObserver(align)
    observer.observe(textarea)
    return () => observer.disconnect()
  }, [align])

  // 每次改字都对一遍：多出一行可能出滚动条，光标移到底下时输入框会自己滚
  useLayoutEffect(align, [align, value, over])

  return (
    <div className="relative">
      <Textarea
        ref={input}
        value={value}
        className={cn("peer", className)}
        onScroll={(e) => {
          align()
          onScroll?.(e)
        }}
        {...props}
      />
      {over && (
        <div
          ref={mirror}
          aria-hidden
          className="pointer-events-none absolute inset-0 overflow-hidden border-solid border-transparent text-transparent select-none peer-disabled:opacity-50"
        >
          {value.slice(0, cut)}
          <mark className="rounded-[2px] bg-destructive/15 text-transparent underline decoration-destructive decoration-[1.5px] underline-offset-[3px] [box-decoration-break:clone] dark:bg-destructive/25">
            {value.slice(cut)}
          </mark>
          {/* 最后一个是换行时 div 不画空的最后一行，补一个空格，滚到底时才对得齐 */}
          {value.endsWith("\n") && " "}
        </div>
      )}
    </div>
  )
}
