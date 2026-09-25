import { Link } from "react-router";
import { Alert, focusRing } from "../ui";

/**
 * What is waiting on the household, if anything.
 *
 * Three rules, each from a decision rather than taste:
 *
 *   - Nothing waiting is not news, so with every queue resolved, empty and
 *     healthy the section does not render at all.
 *   - A broken queue must never look like an empty one. An errored queue keeps
 *     the section on screen and says which one failed, because silently dropping
 *     it would leave receipt lines unreviewed behind a page that reads as calm.
 *   - One alert for the section, not one per queue. `Alert tone="error"` renders
 *     role="alert", and two failures would otherwise mount two assertive live
 *     regions on one page and talk over each other.
 *
 * The queues arrive as props in a fixed order decided by the caller, so rows do
 * not reshuffle between visits and nothing becomes findable only by position.
 * Fetching lives with the caller too, so all of the home page's queries start
 * together rather than waiting on each other.
 */
export interface QueueSummary {
  /** Stable key and the name used when reporting a failure. */
  id: string;
  /** Row text, given the count. */
  label: (count: number) => string;
  to: string;
  linkLabel: string;
  count: number;
  isPending: boolean;
  isError: boolean;
}

const sectionLabel = "text-xs font-semibold text-neutral-600 dark:text-neutral-400";

export function NeedsYou({ queues }: { queues: QueueSummary[] }) {
  const failed = queues.filter((q) => q.isError);
  const pending = queues.some((q) => q.isPending);
  const waiting = queues.filter((q) => !q.isError && !q.isPending && q.count > 0);

  if (failed.length === 0 && !pending && waiting.length === 0) return null;

  return (
    <section className="mt-8" aria-label="Needs you">
      <h2 className={sectionLabel}>
        Needs you
      </h2>

      {pending ? (
        <p role="status" className="mt-2 text-sm text-neutral-600 dark:text-neutral-400">
          Loading…
        </p>
      ) : null}

      {failed.length > 0 ? (
        <Alert tone="error" className="mt-2">
          Could not load {failed.map((q) => q.id).join(" or ")}. Anything waiting there is not
          shown below.
        </Alert>
      ) : null}

      {waiting.length > 0 ? (
        <ul className="mt-2">
          {waiting.map((q) => (
            <li
              key={q.id}
              className="flex items-baseline justify-between gap-3 border-t border-neutral-200 py-2 text-sm first:border-t-0 dark:border-neutral-800"
            >
              <span>{q.label(q.count)}</span>
              <Link
                to={q.to}
                className={`shrink-0 rounded underline underline-offset-2 hover:no-underline ${focusRing}`}
              >
                {q.linkLabel}
              </Link>
            </li>
          ))}
        </ul>
      ) : null}
    </section>
  );
}
