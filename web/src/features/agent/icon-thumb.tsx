import type { LucideIcon } from "lucide-react"

/** 没有图的东西（直接对话、模板）在列表里占缩略图的位置：灰底方块里一个图标，和商品缩略图一样大 */
export function IconThumb({ icon: Icon, size = 40 }: { icon: LucideIcon; size?: number }) {
  return (
    <div
      className="flex shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground"
      style={{ width: size, height: size }}
      aria-hidden
    >
      <Icon className="size-4" />
    </div>
  )
}
