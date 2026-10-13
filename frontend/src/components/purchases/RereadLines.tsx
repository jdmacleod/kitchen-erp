import { useId, useRef, useState } from "react";
import { Link, useNavigate } from "react-router";
import { errorMessage } from "../../api/client";
import type { Purchase } from "../../api/purchases";
import { useRequestReread, type RereadMode } from "../../api/rereads";
import { Alert, Button, focusRing, secondaryLinkClass } from "../ui";

const hintClass = "text-sm text-neutral-600 dark:text-neutral-400";

/**
 * "Re-read lines" beside a receipt purchase's line count (spec 10; 04, 2S, issue 272).
 *
 * Opens a small panel with the two ways to re-read: "Re-check the lines", shown
 * only when the reading kept the reader's answer, and "Read again". Either goes
 * to the re-read page. While a re-read is open, the button goes there instead.
 */
export function RereadLines({ purchase, disabled = false }: { purchase: Purchase; disabled?: boolean }) {
  const state = purchase.reread;
  const navigate = useNavigate();
  const request = useRequestReread(purchase.id);
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const headingId = useId();
  if (!state) return null;
  const page = `/shop/purchases/${purchase.id}/reread`;

  if (state.open) {
    return (
      <Link to={page} className={secondaryLinkClass}>
        {state.open.status === "ready" ? "Re-read ready" : "Re-read in progress"}
      </Link>
    );
  }

  const choose = (mode: RereadMode) => request.mutate(mode, { onSuccess: () => navigate(page) });
  const close = () => {
    setOpen(false);
    request.reset();
    trigger.current?.focus();
  };

  return (
    <>
      <Button ref={trigger} variant="secondary" disabled={disabled || request.isPending} aria-expanded={open} onClick={() => setOpen((v) => !v)}>
        Re-read lines
      </Button>
      {open ? (
        <div
          role="group"
          aria-labelledby={headingId}
          // A row of its own under the buttons, in the flex row that holds them.
          className="flex basis-full flex-col gap-3 rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-900"
          onKeyDown={(e) => {
            if (e.key === "Escape") close();
          }}
        >
          <p id={headingId} className="text-sm font-medium">
            Re-read this receipt's lines
          </p>
          {state.can_recheck ? (
            <div className="flex flex-col gap-1">
              <Button variant="secondary" disabled={request.isPending} onClick={() => choose("check")}>
                Re-check the lines
              </Button>
              <span className={hintClass}>Runs today's line rules again. Instant, no model.</span>
            </div>
          ) : null}
          <div className="flex flex-col gap-1">
            <Button variant="secondary" disabled={request.isPending} onClick={() => choose("read")}>
              Read again
            </Button>
            <span className={hintClass}>Asks the model to read the receipt text again. Takes a few minutes.</span>
          </div>
          <span className={hintClass}>Nothing changes until you've seen what would change and applied it.</span>
          {request.isError ? <Alert tone="error">{errorMessage(request.error)}</Alert> : null}
          <Button variant="secondary" className={`self-start ${focusRing}`} onClick={close}>
            Cancel
          </Button>
        </div>
      ) : null}
    </>
  );
}
