// Helpers for OpenStreetMap opening_hours strings: the three builder presets
// write the syntax, and describe() renders a stored string readably. The
// server is the only validator; nothing here decides whether a string is legal.

export const DAY_CODES = ["Mo", "Tu", "We", "Th", "Fr", "Sa", "Su"] as const;
export type DayCode = (typeof DAY_CODES)[number];

export const MONTH_CODES = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"] as const;
export type MonthCode = (typeof MONTH_CODES)[number];

const dayName: Record<DayCode, string> = {
  Mo: "Monday",
  Tu: "Tuesday",
  We: "Wednesday",
  Th: "Thursday",
  Fr: "Friday",
  Sa: "Saturday",
  Su: "Sunday",
};

const monthName: Record<MonthCode, string> = {
  Jan: "January",
  Feb: "February",
  Mar: "March",
  Apr: "April",
  May: "May",
  Jun: "June",
  Jul: "July",
  Aug: "August",
  Sep: "September",
  Oct: "October",
  Nov: "November",
  Dec: "December",
};

/** The day spans the builder offers for the daily preset. */
export const DAY_SPANS = [
  { value: "Mo-Su", label: "Every day" },
  { value: "Mo-Fr", label: "Monday to Friday" },
  { value: "Mo-Sa", label: "Monday to Saturday" },
  { value: "Sa,Su", label: "Weekends" },
] as const;

export type DaySpan = (typeof DAY_SPANS)[number]["value"];

export interface DailyPreset {
  kind: "daily";
  days: DaySpan;
  open: string; // "08:00"
  close: string; // "20:00"
}

export interface WeeklyPreset {
  kind: "weekly";
  day: DayCode;
  open: string;
  close: string;
}

export interface SeasonalPreset {
  kind: "seasonal";
  from: MonthCode;
  to: MonthCode;
  day: DayCode;
  open: string;
  close: string;
}

export type Preset = DailyPreset | WeeklyPreset | SeasonalPreset;

/** Write the OSM opening_hours string for a preset. Times are "HH:MM". */
export function presetToString(preset: Preset): string {
  switch (preset.kind) {
    case "daily":
      return `${preset.days} ${preset.open}-${preset.close}`;
    case "weekly":
      return `${preset.day} ${preset.open}-${preset.close}`;
    case "seasonal":
      return `${preset.from}-${preset.to} ${preset.day} ${preset.open}-${preset.close}`;
  }
}

const TIME = /^\d{2}:\d{2}$/;

/** True when both times look like HH:MM. The server checks the rest. */
export function presetTimesComplete(preset: Preset): boolean {
  return TIME.test(preset.open) && TIME.test(preset.close);
}

/**
 * A readable rendering of an opening_hours string. Covers the common shapes
 * (day and month ranges, lists, time ranges, "off", "24/7", "PH") and leaves
 * anything else as written, so an exotic rule is never misdescribed.
 */
export function describeOpeningHours(text: string | null | undefined): string {
  if (!text) return "Hours unknown";
  const trimmed = text.trim();
  if (trimmed === "24/7") return "Open 24 hours, every day";
  return trimmed
    .split(";")
    .map((rule) => describeRule(rule.trim()))
    .filter(Boolean)
    .join("; ");
}

function describeRule(rule: string): string {
  if (!rule) return "";
  return rule
    .split(/\s+/)
    .map((token) => describeToken(token))
    .join(" ")
    .replace(/\s+,/g, ",");
}

function describeToken(token: string): string {
  if (token === "off" || token === "closed") return "closed";
  if (token === "PH") return "public holidays";
  if (token === "SH") return "school holidays";
  // 08:00-13:00 or 08:00-13:00,14:00-18:00
  if (/^\d{2}:\d{2}(-\d{2}:\d{2})?(,\d{2}:\d{2}(-\d{2}:\d{2})?)*$/.test(token)) {
    return token.replace(/-/g, "–").replace(/,/g, ", ");
  }
  // Mo-Fr, Sa,Su, Mo
  const dayList = token.split(",");
  if (dayList.every((part) => /^[A-Z][a-z]$/.test(part.split("-")[0]) && isDayRange(part))) {
    return dayList.map(describeDayRange).join(", ");
  }
  const monthList = token.split(",");
  if (monthList.every(isMonthRange)) {
    return monthList.map(describeMonthRange).join(", ");
  }
  return token;
}

function isDay(code: string): code is DayCode {
  return (DAY_CODES as readonly string[]).includes(code);
}

function isMonth(code: string): code is MonthCode {
  return (MONTH_CODES as readonly string[]).includes(code);
}

function isDayRange(part: string): boolean {
  const [a, b] = part.split("-");
  return isDay(a) && (b === undefined || isDay(b));
}

function isMonthRange(part: string): boolean {
  const [a, b] = part.split("-");
  return isMonth(a) && (b === undefined || isMonth(b));
}

function describeDayRange(part: string): string {
  const [a, b] = part.split("-");
  if (!isDay(a)) return part;
  if (b === undefined) return dayName[a];
  if (!isDay(b)) return part;
  if (a === "Mo" && b === "Su") return "every day";
  return `${dayName[a]}–${dayName[b]}`;
}

function describeMonthRange(part: string): string {
  const [a, b] = part.split("-");
  if (!isMonth(a)) return part;
  if (b === undefined) return monthName[a];
  if (!isMonth(b)) return part;
  return `${monthName[a]}–${monthName[b]}`;
}

/** "2026-07-04T09:30" for a datetime-local input, in the viewer's zone. */
export function toDateTimeLocal(date: Date): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}T${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** The UTC ISO instant for a datetime-local value, or null when it does not parse. */
export function fromDateTimeLocal(value: string): string | null {
  if (!value) return null;
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? null : d.toISOString();
}
