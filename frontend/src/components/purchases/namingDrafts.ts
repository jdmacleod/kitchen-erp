import type { RowEdit } from "./namingRows";

// Unsent edits on the naming pass, kept per browser so a reload loses nothing (issue 389).
const KEY = "kerp.naming-drafts";

export function loadDrafts(): Record<string, RowEdit> {
  try {
    const raw = window.localStorage.getItem(KEY);
    const found: unknown = raw ? JSON.parse(raw) : null;
    return found && typeof found === "object" ? (found as Record<string, RowEdit>) : {};
  } catch {
    return {};
  }
}

export function saveDrafts(edits: Record<string, RowEdit>): void {
  try {
    if (Object.keys(edits).length === 0) window.localStorage.removeItem(KEY);
    else window.localStorage.setItem(KEY, JSON.stringify(edits));
  } catch {
    // Private windows and blocked storage: drafts just aren't kept.
  }
}
