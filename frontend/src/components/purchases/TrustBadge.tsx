import type { Trust } from "../../api/purchases";
import { formatMoney } from "../../lib/decimal";

const tones = {
  // Olive: the lines add up to the printed total, nothing to check.
  good: "bg-green-100 text-green-900 dark:bg-green-950 dark:text-green-200",
  // Squash: the reading is a guess that needs a look, not an error.
  warn: "bg-amber-100 text-amber-900 dark:bg-amber-950 dark:text-amber-200",
  // Tomato: nothing could be read, the one error state.
  danger: "bg-red-100 text-red-800 dark:bg-red-950 dark:text-red-200",
} as const;

/** Small inline marks so the badge never depends on colour alone (08, issue 122). */
function Mark({ shape }: { shape: "check" | "alert" | "cross" }) {
  return (
    <svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" focusable="false" className="shrink-0">
      {shape === "check" ? <path d="M3 8.5l3 3 7-7" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /> : null}
      {shape === "alert" ? (
        <>
          <path d="M8 3v6" stroke="currentColor" strokeWidth="2" strokeLinecap="round" />
          <circle cx="8" cy="12.5" r="1.25" fill="currentColor" />
        </>
      ) : null}
      {shape === "cross" ? <path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" strokeWidth="2" strokeLinecap="round" /> : null}
    </svg>
  );
}

interface TrustBadgeProps {
  trust: Trust | null | undefined;
  /** How far the lines are from the printed total, with check_lines. */
  gap?: string | null;
  /** A draft far off its total: reads "Careful look", as in Needs you (issue 121). */
  held?: boolean;
}

/**
 * Whether a receipt's reading can be trusted, before it is opened: "Adds up",
 * "Check the lines · off by 0.30", "Careful look · off by 5.60" or "Couldn't
 * read". Nothing for a purchase entered by hand, or one still being read.
 */
export function TrustBadge({ trust, gap, held = false }: TrustBadgeProps) {
  if (!trust) return null;
  const off = gap ? ` · off by ${formatMoney(gap)}` : "";
  const [tone, shape, label] =
    trust === "adds_up"
      ? (["good", "check", "Adds up"] as const)
      : trust === "couldnt_read"
        ? (["danger", "cross", "Couldn't read"] as const)
        : (["warn", "alert", held ? "Careful look" : "Check the lines"] as const);
  return (
    <span data-testid="trust-badge" data-trust={trust} className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-xs font-medium whitespace-nowrap ${tones[tone]}`}>
      <Mark shape={shape} />
      {label}
      {trust === "check_lines" ? off : ""}
    </span>
  );
}
