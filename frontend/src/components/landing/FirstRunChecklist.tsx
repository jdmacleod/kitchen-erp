import type { ReactNode } from "react";
import { Link } from "react-router";
import { primaryLinkClass, secondaryLinkClass } from "../ui";

/**
 * The two-step first-run checklist.
 *
 * Two steps, not the five the data model implies, because the entry forms create
 * their own dependencies: dropping a pin creates the vendor and the location
 * together (MapPage's LocationDraftForm renders VendorPicker with inline create),
 * and the purchase form creates a product and its ingredient as you type them.
 * Sending someone to Vendors first would teach a two-page detour around a
 * one-page path.
 *
 *   step 1  add somewhere you shop     -> /map?place=location
 *   step 2  record your first purchase -> /purchases/new  (state: firstPurchase)
 *
 * An <ol> rather than styled divs: a screen reader then announces "list, 2 items"
 * and numbers each one without any ARIA. Completion is never carried by colour
 * alone — an sr-only word, the tick's shape, and a muted title all say it.
 */

const primaryAction =
  `${primaryLinkClass} w-full sm:w-auto`;

const secondaryAction = `${secondaryLinkClass} w-full sm:w-auto`;

interface StepProps {
  done: boolean;
  /** The filled button marks the one thing to do next; every other action is outlined. */
  next: boolean;
  title: string;
  to: string;
  state?: Record<string, unknown>;
  action: string;
  children: ReactNode;
}

function Step({ done, next, title, to, state, action, children }: StepProps) {
  return (
    <li className="flex flex-col gap-3 border-t border-neutral-200 py-4 first:border-t-0 sm:flex-row sm:items-start sm:gap-4 dark:border-neutral-800">
      {/* The indicator stays beside the title at every width. Letting the row's
          flex-col stack it put a lone circle on its own line above the heading,
          where it read as a stray dot rather than a checkbox. */}
      <div className="flex min-w-0 flex-1 gap-3">
        {/* Decoration: the sr-only word below carries this state for a screen reader.
            green-600, not green-500 — 4.83:1 against the white card, over the 3:1
            floor that WCAG 1.4.11 sets for non-text UI; green-500 measures 3.47:1.
            Dark keeps green-500: 4.59:1 on neutral-900. Market palette, 2026-09-25. */}
        <span
          aria-hidden="true"
          className={`mt-0.5 flex size-5 shrink-0 items-center justify-center rounded-full border-2 ${
            done ? "border-green-600 bg-green-600 dark:border-green-500 dark:bg-green-500" : "border-neutral-300 dark:border-neutral-600"
          }`}
        >
          {done ? (
            <svg viewBox="0 0 16 16" className="size-3 text-white" fill="none" stroke="currentColor" strokeWidth="3">
              <path d="M3.5 8.5 6.5 11.5 12.5 4.5" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          ) : null}
        </span>

        <div className="min-w-0">
          <p className={`text-base font-medium ${done ? "text-neutral-600 dark:text-neutral-400" : ""}`}>
            <span className="sr-only">{done ? "Done" : "To do"} — </span>
            {title}
          </p>
          <p className="mt-1 text-sm text-neutral-600 dark:text-neutral-400">{children}</p>
        </div>
      </div>

      {/* Below 640px the action drops beneath the description and becomes a
          full-width 40px target rather than a cramped inline link. */}
      <Link to={to} state={state} className={next ? primaryAction : secondaryAction}>
        {action}
      </Link>
    </li>
  );
}

export function FirstRunChecklist({ hasLocation }: { hasLocation: boolean }) {
  return (
    <ol className="mt-6">
      <Step
        done={hasLocation}
        next={!hasLocation}
        title="Add somewhere you shop"
        to="/map?place=location"
        action="Open the map"
      >
        Dropping a pin names the shop and its location together, so this is one step rather
        than two. The map opens ready to place, and stays a plain background until map tiles
        are installed — pins work either way.
      </Step>
      <Step
        done={false}
        next={hasLocation}
        title="Record your first purchase"
        to="/purchases/new"
        // Tells the entry form this is the first one, so the confirmation can say so
        // where the work finishes. Free, and true by construction: this link only
        // renders while no committed purchase exists.
        state={{ firstPurchase: true }}
        action="New purchase"
      >
        Products, and the ingredients behind them, are created as you type them. There is
        nothing to set up first.
      </Step>
    </ol>
  );
}
