import type { SearchResult } from "../api/search";

const KEY = "kerp.searchRecents";
export const RECENT_LIMIT = 5;
const KINDS = new Set(["ingredient", "product", "vendor"]);

/**
 * One remembered result, checked field by field. Local storage is this device's,
 * but anything could have written it, so a malformed entry is dropped rather
 * than trusted, and a route must stay inside this app.
 */
function valid(x: unknown): x is SearchResult {
  if (typeof x !== "object" || x === null) return false;
  const r = x as Record<string, unknown>;
  return (
    typeof r.kind === "string" &&
    KINDS.has(r.kind) &&
    typeof r.id === "string" &&
    typeof r.label === "string" &&
    (r.detail === null || typeof r.detail === "string") &&
    (r.category_key === null || typeof r.category_key === "string") &&
    typeof r.route === "string" &&
    r.route.startsWith("/") &&
    !r.route.startsWith("//")
  );
}

/** The last results opened from search on this device, newest first (G15). Empty when storage fails. */
export function readRecents(): SearchResult[] {
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(KEY) ?? "[]");
    if (!Array.isArray(parsed)) return [];
    // Each result once, even if storage holds it twice.
    const seen = new Set<string>();
    return parsed
      .filter(valid)
      .filter((r) => {
        const key = `${r.kind}:${r.id}`;
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .slice(0, RECENT_LIMIT);
  } catch {
    return [];
  }
}

/** Put a result first, once, keeping the five most recent. */
export function rememberRecent(result: SearchResult): void {
  try {
    const { kind, id, label, detail, route, category_key } = result;
    const next = [{ kind, id, label, detail, route, category_key }, ...readRecents().filter((r) => !(r.kind === kind && r.id === id))];
    localStorage.setItem(KEY, JSON.stringify(next.slice(0, RECENT_LIMIT)));
  } catch {
    // Private mode or storage disabled: search simply has no recents.
  }
}
