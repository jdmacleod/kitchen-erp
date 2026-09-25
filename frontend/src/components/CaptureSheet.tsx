import { useId, useRef } from "react";
import { Link } from "react-router";
import { Dialog } from "./Dialog";
import { Button, focusRing } from "./ui";

const MODES = [
  {
    to: "/shop/shelf-prices",
    title: "Log a shelf price",
    description: "Spotted a price without buying",
    icon: "M4 4h7l9 9-7 7-9-9V4Zm4 4h.01",
  },
  {
    to: "/shop/receipts",
    title: "Scan a receipt",
    description: "A store purchase with a receipt",
    icon: "M6 3h12v18l-3-2-3 2-3-2-3 2V3Zm3 5h6m-6 4h6",
  },
  {
    to: "/shop/purchases/new",
    title: "Enter a purchase",
    description: "A market or stand without a receipt",
    icon: "M4 20h4L19 9l-4-4L4 16v4Zm9-13 4 4",
  },
] as const;

/**
 * Capture: the one entry point for getting data in (docs/spec/09, Capture). A sheet
 * from the bottom on phone and tablet, a small centred dialog on desktop (G14).
 * Mounted only while open.
 */
export function CaptureSheet({ onClose }: { onClose: () => void }) {
  const titleId = useId();
  // Choosing a mode navigates, so focus goes to the new page, not back to Capture.
  const returnFocus = useRef(true);
  const choose = () => {
    returnFocus.current = false;
    onClose();
  };
  return (
    <Dialog open onClose={onClose} labelledBy={titleId} placement="sheet" returnFocus={returnFocus} className="p-4 pb-[max(1rem,env(safe-area-inset-bottom))]">
      <div aria-hidden="true" className="mx-auto mb-3 h-1 w-10 rounded-full bg-neutral-300 lg:hidden dark:bg-neutral-700" />
      <h2 id={titleId} className="font-display mb-3 text-xl">
        Capture
      </h2>
      <ul className="flex flex-col gap-2">
        {MODES.map((m) => (
          <li key={m.to}>
            <Link
              to={m.to}
              onClick={choose}
              className={`flex min-h-[4.75rem] items-center gap-3 rounded-lg border border-neutral-200 px-3 text-neutral-900 hover:bg-neutral-50 dark:border-neutral-800 dark:text-neutral-100 dark:hover:bg-neutral-800 ${focusRing}`}
            >
              <span className="flex size-11 shrink-0 items-center justify-center rounded-lg bg-neutral-100 text-neutral-700 dark:bg-neutral-800 dark:text-neutral-300">
                <svg viewBox="0 0 24 24" className="size-5" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <path d={m.icon} />
                </svg>
              </span>
              <span className="min-w-0">
                <span className="block font-medium">{m.title}</span>
                <span className="block text-sm text-neutral-600 dark:text-neutral-400">{m.description}</span>
              </span>
            </Link>
          </li>
        ))}
      </ul>
      <Button variant="secondary" className="mt-3 min-h-11 w-full" onClick={onClose}>
        Cancel
      </Button>
    </Dialog>
  );
}
