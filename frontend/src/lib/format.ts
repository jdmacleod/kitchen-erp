const dateTime = new Intl.DateTimeFormat(undefined, {
  dateStyle: "medium",
  timeStyle: "short",
});

/** Render a UTC ISO-8601 timestamp in the viewer's locale and zone. */
export function formatDateTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return dateTime.format(d);
}

const dateOnly = new Intl.DateTimeFormat(undefined, { dateStyle: "medium" });

/** Render a UTC ISO-8601 timestamp as a date in the viewer's locale and zone. */
export function formatDate(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return dateOnly.format(d);
}

/**
 * Render a calendar date ("2026-06-04") as written, e.g. "Jun 4, 2026". It is a
 * date in the household's calendar, not a moment, so it is never shifted by zone.
 */
export function formatCalendarDate(ymd: string | null | undefined): string {
  if (!ymd) return "";
  const m = /^(\d{4})-(\d{2})-(\d{2})$/.exec(ymd);
  if (!m) return ymd;
  return dateOnly.format(new Date(Number(m[1]), Number(m[2]) - 1, Number(m[3])));
}

const timeOnly = new Intl.DateTimeFormat(undefined, { timeStyle: "short" });

/** Render a UTC ISO-8601 timestamp as a time of day, e.g. "2:00 PM". */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return timeOnly.format(d);
}

/**
 * A moment as a distance from now, for "indexed 3 hours ago": minutes under an
 * hour, hours under a day, days under a month, then the date. Display only.
 */
export function formatRelative(iso: string | null | undefined, now: number = Date.now()): string {
  if (!iso) return "";
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return iso;
  const seconds = Math.max(0, Math.floor((now - then) / 1000));
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes} ${minutes === 1 ? "minute" : "minutes"} ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours} ${hours === 1 ? "hour" : "hours"} ago`;
  const days = Math.floor(hours / 24);
  if (days < 31) return `${days} ${days === 1 ? "day" : "days"} ago`;
  return dateOnly.format(new Date(then));
}
