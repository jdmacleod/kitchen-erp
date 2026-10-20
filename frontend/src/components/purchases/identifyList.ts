import type { ToIdentifyGroup } from "../../api/toIdentify";

/** How the to-identify list is ordered: newest receipt first (the server's order), most lines, or A–Z. */
export type IdentifySort = "newest" | "lines" | "name";

export const IDENTIFY_SORTS: { value: IdentifySort; label: string }[] = [
  { value: "newest", label: "Newest first" },
  { value: "lines", label: "Most lines first" },
  { value: "name", label: "A–Z" },
];

export function readSort(value: string | null): IdentifySort {
  return IDENTIFY_SORTS.some((s) => s.value === value) ? (value as IdentifySort) : "newest";
}

export interface IdentifyFilter {
  /** A vendor id, or "" for every store. */
  store: string;
  q: string;
  sort: IdentifySort;
}

function words(text: string): string[] {
  return text
    .toLowerCase()
    .split(/[^\p{L}\p{N}]+/u)
    .filter(Boolean);
}

/**
 * Whether every typed word starts a word of the group's wording, or of one of
 * its lines as printed: "gr on" finds GREEN ONIONS, "nions" does not.
 */
export function matchesText(group: ToIdentifyGroup, q: string): boolean {
  const wanted = words(q);
  if (wanted.length === 0) return true;
  const texts = [group.raw_text_norm ?? "", ...group.lines.map((l) => l.raw_text ?? "")];
  return texts.some((t) => {
    const have = words(t);
    return wanted.every((w) => have.some((h) => h.startsWith(w)));
  });
}

/** The groups the filter keeps, in the order it asks for. */
export function filterGroups(groups: ToIdentifyGroup[], f: IdentifyFilter): ToIdentifyGroup[] {
  const kept = groups.filter(
    (g) => (f.store === "" || g.vendor.id === f.store) && matchesText(g, f.q),
  );
  if (f.sort === "lines") return [...kept].sort((a, b) => b.line_count - a.line_count);
  if (f.sort === "name")
    return [...kept].sort((a, b) => (a.raw_text_norm ?? "").localeCompare(b.raw_text_norm ?? ""));
  return kept;
}

/** Each store with groups waiting, with how many, most first. */
export function storeCounts(
  groups: ToIdentifyGroup[],
): { id: string; name: string; groups: number }[] {
  const out = new Map<string, { id: string; name: string; groups: number }>();
  for (const g of groups) {
    const s = out.get(g.vendor.id) ?? { id: g.vendor.id, name: g.vendor.name, groups: 0 };
    s.groups += 1;
    out.set(g.vendor.id, s);
  }
  return [...out.values()].sort((a, b) => b.groups - a.groups || a.name.localeCompare(b.name));
}

/** The words a store's site is searched for: the suggested brand and name, else the wording. */
export function searchWords(group: ToIdentifyGroup): string {
  const named = [group.suggested_brand, group.suggested_name].filter(Boolean).join(" ");
  return named || (group.raw_text_norm ?? "").toLowerCase();
}

/**
 * The store's search page for these words, or null without a page or words. The
 * address is the household's own setting and only ever opened by a person.
 */
export function searchHref(template: string | null | undefined, words: string): string | null {
  if (!template || !words.trim() || !/^https?:\/\//.test(template)) return null;
  return template.replace("{q}", encodeURIComponent(words.trim()));
}
