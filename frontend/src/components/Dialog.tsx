import { useEffect, useRef, type ReactNode, type RefObject } from "react";

export const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

interface DialogProps {
  open: boolean;
  onClose: () => void;
  /** The id of the element that names the dialog. */
  labelledBy: string;
  /**
   * "center" is a dialog in the middle of the screen. "sheet" rises from the bottom
   * below lg (1024px, G19) and becomes a small centred dialog at lg and wider (G14).
   */
  placement?: "center" | "sheet";
  /** Where focus goes on open; the first focusable element otherwise. */
  initialFocus?: RefObject<HTMLElement | null>;
  className?: string;
  children: ReactNode;
}

/**
 * The keyboard contract every modal shares (spec 08: Dialog, Drawer): focus moves
 * in on open, Tab stays inside, Escape asks to close, and focus returns to whatever
 * opened it.
 */
export function useModal(
  panel: RefObject<HTMLElement | null>,
  open: boolean,
  onEscape: () => void,
  initialFocus?: RefObject<HTMLElement | null>,
) {
  const onEscapeRef = useRef(onEscape);
  useEffect(() => {
    onEscapeRef.current = onEscape;
  });

  useEffect(() => {
    if (!open) return;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    const first = initialFocus?.current ?? panel.current?.querySelector<HTMLElement>(FOCUSABLE) ?? panel.current;
    first?.focus();

    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        event.preventDefault();
        onEscapeRef.current();
        return;
      }
      if (event.key !== "Tab" || !panel.current) return;
      const items = [...panel.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
      if (items.length === 0) return;
      const [head, tail] = [items[0], items[items.length - 1]];
      if (event.shiftKey && document.activeElement === head) {
        event.preventDefault();
        tail.focus();
      } else if (!event.shiftKey && document.activeElement === tail) {
        event.preventDefault();
        head.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      // Back to the opener, unless closing moved focus somewhere on purpose (a
      // result that navigated, for instance, leaves the opener behind).
      if (opener?.isConnected) opener.focus();
    };
  }, [open, initialFocus, panel]);
}

/**
 * A modal over a scrim. Focus moves in on open, Tab stays inside, Escape or a click
 * on the scrim closes it, and focus returns to whatever opened it.
 */
export function Dialog({ open, onClose, labelledBy, placement = "center", initialFocus, className = "", children }: DialogProps) {
  const panel = useRef<HTMLDivElement>(null);
  useModal(panel, open, onClose, initialFocus);

  if (!open) return null;

  const position =
    placement === "sheet"
      ? "items-end lg:items-center lg:justify-center"
      : "items-start justify-center pt-[12vh] px-4";
  const shape =
    placement === "sheet"
      ? "w-full rounded-t-2xl lg:w-[28rem] lg:rounded-xl"
      : "w-full max-w-[40rem] rounded-xl";

  return (
    <div className={`fixed inset-0 z-50 flex ${position}`}>
      <div aria-hidden="true" className="absolute inset-0 bg-neutral-950/40" onClick={onClose} />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        tabIndex={-1}
        className={`relative bg-white shadow-xl outline-none dark:bg-neutral-900 ${shape} ${className}`}
      >
        {children}
      </div>
    </div>
  );
}
