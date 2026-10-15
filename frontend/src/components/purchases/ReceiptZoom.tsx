import { useRef, useState, type PointerEvent } from "react";
import { Button } from "../ui";
import { ReceiptImage } from "./ReceiptImage";

const STEPS = [1, 1.5, 2, 3] as const;
const small = "min-h-11 lg:min-h-8 px-2 text-xs";

/**
 * The receipt photo in review (2S, U4): zoom in on the prices, drag to pan,
 * and widen the image column while checking lines against it. The frame
 * scrolls with the keyboard too, and the link still opens the original.
 */
export function ReceiptZoom({
  documentId,
  wide,
  onWide,
}: {
  documentId: string;
  wide: boolean;
  onWide: (wide: boolean) => void;
}) {
  const [step, setStep] = useState(0);
  const frame = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const zoom = STEPS[step];

  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    const el = frame.current;
    if (!el || zoom === 1) return;
    drag.current = { x: e.clientX, y: e.clientY, left: el.scrollLeft, top: el.scrollTop };
    el.setPointerCapture(e.pointerId);
  };
  const onPointerMove = (e: PointerEvent<HTMLDivElement>) => {
    const el = frame.current;
    const start = drag.current;
    if (!el || !start) return;
    el.scrollLeft = start.left - (e.clientX - start.x);
    el.scrollTop = start.top - (e.clientY - start.y);
  };
  const onPointerUp = () => {
    drag.current = null;
  };

  return (
    <div className="flex flex-col gap-2">
      <div
        className="flex flex-wrap items-center gap-1"
        role="group"
        aria-label="Receipt image size"
      >
        <Button
          variant="secondary"
          className={small}
          disabled={step === 0}
          onClick={() => setStep((s) => Math.max(0, s - 1))}
        >
          Zoom out
        </Button>
        <Button
          variant="secondary"
          className={small}
          disabled={step === STEPS.length - 1}
          onClick={() => setStep((s) => Math.min(STEPS.length - 1, s + 1))}
        >
          Zoom in
        </Button>
        <span
          className="text-xs tabular-nums text-neutral-600 dark:text-neutral-400"
          aria-live="polite"
        >
          {zoom === 1 ? "Whole receipt" : `${zoom * 100}%`}
        </span>
        <Button
          variant="secondary"
          className={`${small} hidden lg:inline-flex`}
          aria-pressed={wide}
          onClick={() => onWide(!wide)}
        >
          Side by side
        </Button>
      </div>
      {/* A scrollable region: arrow keys pan it, as dragging does. */}
      <div
        ref={frame}
        role="region"
        aria-label="Receipt image"
        // A scrollable frame must take focus to scroll by keyboard.
        // eslint-disable-next-line jsx-a11y/no-noninteractive-tabindex
        tabIndex={0}
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        className={`max-h-[80dvh] overflow-auto rounded-md border border-neutral-200 dark:border-neutral-800 ${zoom > 1 ? "cursor-grab active:cursor-grabbing" : ""}`}
      >
        <ReceiptImage
          documentId={documentId}
          alt="The receipt as photographed"
          // A phone photo or a PDF page is several megabytes at full size; the
          // review needs a screen's width, and the link opens the original.
          width={zoom === 1 ? 1024 : undefined}
          link={zoom === 1 ? { label: "Open the receipt photo at full size" } : undefined}
          draggable={zoom === 1}
          style={zoom === 1 ? undefined : { width: `${zoom * 100}%`, maxWidth: "none" }}
          className={zoom === 1 ? "max-h-[80dvh] w-full object-contain" : "select-none"}
        />
      </div>
    </div>
  );
}
