import { ImageOffIcon, ImagePlusIcon, Loader2Icon, RotateCwIcon, XIcon } from "lucide-react"
import { useRef } from "react"

import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

import { ACCEPT, MAX_SCREENSHOTS, type Screenshots } from "./use-screenshots"

export function ScreenshotPicker({
  screenshots,
  dragging,
  disabled,
  error,
}: {
  screenshots: Screenshots
  dragging: boolean
  disabled: boolean
  error: string
}) {
  const fileInput = useRef<HTMLInputElement>(null)
  const { shots, add, remove, retry } = screenshots
  return (
    <div className="grid gap-2">
      <div className="flex items-center justify-between gap-4">
        <span id="feedback-shots-label" className="text-sm leading-none font-medium">
          截图 <span className="font-normal text-muted-foreground">（选填）</span>
        </span>
        <span className="text-xs text-muted-foreground tabular-nums">
          {shots.length} / {MAX_SCREENSHOTS} 张
        </span>
      </div>
      <ul
        aria-labelledby="feedback-shots-label"
        aria-describedby="feedback-shots-hint"
        className={cn(
          "flex flex-wrap gap-2 rounded-lg transition-colors",
          dragging && "bg-muted outline-2 outline-offset-4 outline-border outline-dashed",
        )}
      >
        {shots.map((shot, i) => (
          <li key={shot.key} className="group relative">
            <img
              src={shot.preview}
              alt={`截图 ${i + 1}`}
              title={shot.name}
              className={cn(
                "size-20 rounded-md border bg-muted object-cover",
                shot.status !== "done" && "opacity-40",
                shot.status === "failed" && "border-destructive",
              )}
            />
            {shot.status === "uploading" && (
              <span className="absolute inset-0 flex items-center justify-center" aria-label="上传中">
                <Loader2Icon className="size-5 animate-spin text-muted-foreground" aria-hidden />
              </span>
            )}
            {shot.status === "failed" && (
              <button
                type="button"
                disabled={disabled}
                onClick={() => retry(shot.key)}
                title={shot.error}
                aria-label={`第 ${i + 1} 张没传上：${shot.error}。点一下重传`}
                className="absolute inset-0 flex flex-col items-center justify-center gap-0.5 rounded-md text-[11px] font-medium text-destructive outline-none focus-visible:ring-3 focus-visible:ring-ring/50"
              >
                <ImageOffIcon className="size-4 group-hover:hidden" aria-hidden />
                <RotateCwIcon className="hidden size-4 group-hover:block" aria-hidden />
                没传上
              </button>
            )}
            <Button
              type="button"
              size="icon-xs"
              variant="secondary"
              disabled={disabled}
              onClick={() => remove(shot.key)}
              aria-label={`删掉第 ${i + 1} 张截图`}
              className="absolute top-1 right-1 opacity-0 transition-opacity group-focus-within:opacity-100 group-hover:opacity-100"
            >
              <XIcon />
            </Button>
          </li>
        ))}
        {shots.length < MAX_SCREENSHOTS && (
          <li>
            <button
              type="button"
              disabled={disabled}
              onClick={() => fileInput.current?.click()}
              className="flex size-20 flex-col items-center justify-center gap-1 rounded-md border border-dashed border-input text-xs text-muted-foreground transition-colors outline-none hover:bg-muted hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50 disabled:opacity-50"
            >
              <ImagePlusIcon className="size-5" aria-hidden />
              加截图
            </button>
            <input
              ref={fileInput}
              type="file"
              accept={ACCEPT}
              multiple
              hidden
              onChange={(event) => {
                const files = [...(event.target.files ?? [])]
                event.target.value = ""
                add(files)
              }}
            />
          </li>
        )}
      </ul>
      <p
        id="feedback-shots-hint"
        aria-live="polite"
        className={error ? "text-xs text-destructive" : "text-xs text-muted-foreground"}
      >
        {error ||
          `截好图可以直接在这个窗口里粘贴，也可以拖进来。最多 ${MAX_SCREENSHOTS} 张，每张不超过 20 MB。`}
      </p>
    </div>
  )
}
