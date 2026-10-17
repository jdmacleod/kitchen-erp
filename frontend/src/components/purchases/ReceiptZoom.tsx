import { useEffect, useRef, useState, type PointerEvent, type RefObject } from "react";
import type { Purchase, PurchaseLine } from "../../api/purchases";
import { useRowBoxes, type RowBoxes } from "../../api/rowBoxes";
import { Button } from "../ui";
import { ReceiptImage } from "./ReceiptImage";

const STEPS = [1, 1.5, 2, 3] as const;
const small = "min-h-11 lg:min-h-8 px-2 text-xs";
// How far a pointer may move between press and release and still be a click.
const CLICK_SLOP = 5;

/**
 * The receipt photo in review (2S, U4): zoom in on the prices, drag to pan,
 * and widen the image column while checking lines against it. The frame
 * scrolls with the keyboard too, and the link still opens the original.
 *
 * Where the reading found each line's printed row (issue 380), the current
 * line's row is outlined and the frame scrolls to it; clicking a row on the
 * photo makes its line the current one. A row that wasn't found has no outline.
 */
export function ReceiptZoom({
  purchase,
  wide,
  onWide,
  currentLine,
  onSelectLine,
}: {
  purchase: Purchase;
  wide: boolean;
  onWide: (wide: boolean) => void;
  currentLine?: PurchaseLine | null;
  onSelectLine?: (lineId: string) => void;
}) {
  const documentId = purchase.receipt_document_id ?? "";
  const [step, setStep] = useState(0);
  const zoom = STEPS[step];
  const view = photoView(zoom);
  // The outlines wait for the image they sit on: none over the sentence shown
  // when it fails. The whole-receipt and zoomed images are different files.
  const shown = `${documentId}:${view.file}`;
  const [loaded, setLoaded] = useState<string | null>(null);
  const { data: boxes } = useRowBoxes(purchase.id, purchase.updated_at);
  const outlined = loaded === shown && boxes?.width && boxes.height ? boxes : null;
  const currentLineId = currentLine?.id ?? null;
  // What moves the current row: the line, the boxes arriving, the zoom.
  const place = `${currentLineId ?? ""}:${outlined ? "on" : "off"}:${zoom}`;
  const { frame, currentBox, handlers } = useFrame(zoom, place, onSelectLine);

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
          variant={wide ? "primary" : "secondary"}
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
        {...handlers}
        className={`max-h-[80dvh] overflow-auto rounded-md border border-neutral-200 dark:border-neutral-800 ${zoom > 1 ? "cursor-grab active:cursor-grabbing" : ""}`}
      >
        {/* Sized to the image, so the outlines can be placed in its proportions. */}
        <div data-testid="receipt-photo" className={view.wrapper} style={view.style}>
          <ReceiptImage
            documentId={documentId}
            alt="The receipt as photographed"
            width={view.width}
            link={view.link}
            draggable={view.draggable}
            onLoad={() => setLoaded(shown)}
            className={view.image}
          />
          {outlined ? (
            <RowOutlines found={outlined} currentLineId={currentLineId} currentBox={currentBox} />
          ) : null}
        </div>
      </div>
    </div>
  );
}

/** How the photo is shown at a zoom level: whole in its frame, or wider than it. */
function photoView(zoom: number) {
  if (zoom === 1)
    return {
      file: "fit",
      // A phone photo or a PDF page is several megabytes at full size; the
      // review needs a screen's width, and the link opens the original.
      width: 1024,
      link: { label: "Open the receipt photo at full size" },
      draggable: true,
      // Sized to the image, so the outlines can be placed in its proportions.
      wrapper: "relative mx-auto w-fit",
      style: undefined,
      image: "block max-h-[80dvh] max-w-full",
    };
  return {
    file: "full",
    width: undefined,
    link: undefined,
    draggable: false,
    wrapper: "relative",
    style: { width: `${zoom * 100}%` },
    image: "block w-full select-none",
  };
}

/**
 * The frame: drag a zoomed photo to pan it, click a row (press and release in
 * nearly the same place) to pick its line, and, whenever ``place`` changes,
 * bring the current line's row into view if it is out of view.
 */
function useFrame(zoom: number, place: string, onSelectLine?: (lineId: string) => void) {
  const frame = useRef<HTMLDivElement>(null);
  const currentBox = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = frame.current;
    const row = currentBox.current;
    if (!el || !row) return;
    const f = el.getBoundingClientRect();
    const b = row.getBoundingClientRect();
    if (b.top < f.top || b.bottom > f.bottom) el.scrollTop += b.top - f.top - el.clientHeight / 3;
    if (b.left < f.left || b.right > f.right) el.scrollLeft += b.left - f.left - CLICK_SLOP * 4;
  }, [place]);
  const drag = useRef<{ x: number; y: number; left: number; top: number } | null>(null);
  const press = useRef<{ x: number; y: number; lineId: string | null } | null>(null);
  const onPointerDown = (e: PointerEvent<HTMLDivElement>) => {
    const row = (e.target as HTMLElement).closest<HTMLElement>("[data-line-id]");
    press.current = { x: e.clientX, y: e.clientY, lineId: row?.dataset.lineId ?? null };
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
  const onPointerUp = (e: PointerEvent<HTMLDivElement>) => {
    const start = press.current;
    drag.current = null;
    press.current = null;
    if (!start?.lineId || !onSelectLine) return;
    const moved = Math.abs(e.clientX - start.x) + Math.abs(e.clientY - start.y);
    if (moved <= CLICK_SLOP) onSelectLine(start.lineId);
  };
  const onPointerCancel = () => {
    drag.current = null;
    press.current = null;
  };
  return {
    frame,
    currentBox,
    handlers: { onPointerDown, onPointerMove, onPointerUp, onPointerCancel },
  };
}

/**
 * The image beside the lines. From lg the lines are a table that needs the
 * width, so the image stacks above it until xl; side by side, a person has
 * chosen half the page for the image while checking lines against it.
 */
export function imageColumns(sideBySide: boolean): string {
  return sideBySide
    ? "md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]"
    : "md:grid-cols-[minmax(0,2fr)_minmax(0,3fr)] lg:grid-cols-1 xl:grid-cols-[minmax(0,1fr)_minmax(0,2fr)]";
}

const pct = (n: number, of: number) => `${(100 * n) / of}%`;

/**
 * A hit area over each found row and an outline around the current line's.
 * Hidden from assistive technology: the lines themselves are the way to move
 * between lines, and this only shows where the current one is printed.
 */
function RowOutlines({
  found,
  currentLineId,
  currentBox,
}: {
  found: RowBoxes;
  currentLineId: string | null;
  currentBox: RefObject<HTMLDivElement | null>;
}) {
  const width = found.width ?? 1;
  const height = found.height ?? 1;
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-0">
      {found.lines.map((b) => {
        const current = b.line_id === currentLineId;
        return (
          <div
            key={b.line_id}
            ref={current ? currentBox : undefined}
            data-line-id={b.line_id}
            data-current={current ? "true" : undefined}
            // Drawn on the paper, which is light in either theme: so dark in both.
            className={`pointer-events-auto absolute cursor-pointer rounded-sm outline-offset-2 ${
              current ? "bg-neutral-900/10 outline-2 outline-neutral-900" : "hover:bg-neutral-900/5"
            }`}
            style={{
              left: pct(b.left, width),
              top: pct(b.top, height),
              width: pct(b.width, width),
              height: pct(b.height, height),
            }}
          />
        );
      })}
    </div>
  );
}
