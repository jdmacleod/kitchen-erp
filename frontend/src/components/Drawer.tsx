import { useId, useRef, useState, type ReactNode } from "react";
import { useModal } from "./Dialog";
import { Button } from "./ui";

interface DrawerProps {
  title: string;
  /** What the drawer creates, for the unsaved-input bar: "Discard this product?". */
  thing: string;
  /** The form holds typed input, so closing must ask first (D5). */
  dirty: boolean;
  onClose: () => void;
  /** The id of the form inside, which the footer's primary button submits. */
  formId: string;
  primaryLabel: string;
  busy?: boolean;
  busyLabel?: string;
  /** Another action beside the primary, e.g. "Save and scan another". */
  secondaryAction?: ReactNode;
  children: ReactNode;
}

/**
 * A right-side drawer for creating things (spec 08: Drawer). Mounted only while
 * open. 460px on desktop, the full width on a phone, with Cancel and the primary
 * action in a sticky footer.
 *
 * With typed input, Escape, a backdrop click and Cancel do not close it: they show
 * an inline squash bar asking to discard, and focus goes to Keep editing (D5).
 */
export function Drawer({ title, thing, dirty, onClose, formId, primaryLabel, busy = false, busyLabel, secondaryAction, children }: DrawerProps) {
  const titleId = useId();
  const panel = useRef<HTMLDivElement>(null);
  const keepEditing = useRef<HTMLButtonElement>(null);
  const [asking, setAsking] = useState(false);

  const requestClose = () => {
    if (!dirty) return onClose();
    setAsking(true);
    // After the bar renders.
    queueMicrotask(() => keepEditing.current?.focus());
  };
  useModal(panel, true, requestClose);

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div aria-hidden="true" data-testid="drawer-backdrop" className="absolute inset-0 bg-neutral-950/40" onClick={requestClose} />
      <div
        ref={panel}
        role="dialog"
        aria-modal="true"
        aria-labelledby={titleId}
        tabIndex={-1}
        className="relative flex h-full w-full flex-col bg-white shadow-xl outline-none sm:m-2 sm:h-[calc(100%-1rem)] sm:w-[460px] sm:rounded-xl dark:bg-neutral-900"
      >
        <header className="border-b border-neutral-200 px-5 py-4 dark:border-neutral-800">
          <h2 id={titleId} className="font-display text-xl">
            {title}
          </h2>
        </header>
        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-4">{children}</div>
        <footer className="border-t border-neutral-200 px-5 py-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] dark:border-neutral-800">
          {asking ? (
            <div role="alert" className="mb-3 rounded-md border border-amber-300 bg-amber-50 px-3 py-2 text-sm text-amber-900 dark:border-amber-800 dark:bg-amber-950 dark:text-amber-100">
              <p>Discard this {thing}? What you typed will be lost.</p>
              <div className="mt-2 flex flex-wrap gap-2">
                <Button ref={keepEditing} variant="secondary" onClick={() => setAsking(false)}>
                  Keep editing
                </Button>
                <Button variant="danger" onClick={onClose}>
                  Discard
                </Button>
              </div>
            </div>
          ) : null}
          <div className="flex flex-wrap justify-end gap-2">
            <Button variant="secondary" onClick={requestClose}>
              Cancel
            </Button>
            {secondaryAction}
            <Button type="submit" form={formId} disabled={busy}>
              {busy ? (busyLabel ?? primaryLabel) : primaryLabel}
            </Button>
          </div>
        </footer>
      </div>
    </div>
  );
}
