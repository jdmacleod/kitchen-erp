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

const timeOnly = new Intl.DateTimeFormat(undefined, { timeStyle: "short" });

/** Render a UTC ISO-8601 timestamp as a time of day, e.g. "2:00 PM". */
export function formatTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return timeOnly.format(d);
}
