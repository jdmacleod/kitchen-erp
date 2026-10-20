import { useSyncExternalStore } from "react";

// The To identify group the next clipped page is for (04, 2D). The clip window
// opens on this app's origin, so it reads what the card wrote here. Kept in this
// browser only, and for two hours: a convenience, never a record.

export interface ClipTarget {
  vendor_id: string;
  vendor_name: string;
  raw_text_norm: string;
  line_count: number;
  /** When it was set, in milliseconds. */
  at: number;
}

const KEY = "kerp.clipFor";
const CHANNEL = "kerp-clip";
const MAX_AGE_MS = 2 * 60 * 60 * 1000;

// Cards in this tab re-render when the target changes (useClipTarget).
const listeners = new Set<() => void>();
let version = 0;
function changed() {
  version += 1;
  for (const l of listeners) l();
}

export function setClipTarget(target: Omit<ClipTarget, "at">): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...target, at: Date.now() }));
  } catch {
    // Storage may be off (a private window): the clip is simply for no lines.
  }
  changed();
}

export function clearClipTarget(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    // Nothing to clear.
  }
  changed();
}

/** The key of the group the next clip is for in this browser, re-read when it changes. */
export function useClipTargetKey(): string | null {
  useSyncExternalStore(
    (l) => {
      listeners.add(l);
      return () => listeners.delete(l);
    },
    () => version,
  );
  const t = readClipTarget();
  return t ? clipKey(t.vendor_id, t.raw_text_norm) : null;
}

export function clipKey(vendorId: string, rawTextNorm: string): string {
  return `${vendorId}:${rawTextNorm}`;
}

/** The group the next clip is for, unless it is missing, malformed or stale. */
export function readClipTarget(now = Date.now()): ClipTarget | null {
  try {
    const raw: unknown = JSON.parse(localStorage.getItem(KEY) ?? "null");
    if (!raw || typeof raw !== "object") return null;
    const t = raw as Partial<ClipTarget>;
    if (
      typeof t.vendor_id !== "string" ||
      typeof t.vendor_name !== "string" ||
      typeof t.raw_text_norm !== "string" ||
      typeof t.line_count !== "number" ||
      typeof t.at !== "number" ||
      now - t.at > MAX_AGE_MS
    )
      return null;
    return t as ClipTarget;
  } catch {
    return null;
  }
}

/** Tell this app's other tabs a clip was saved, so their lists catch up. */
export function announceClip(): void {
  try {
    const channel = new BroadcastChannel(CHANNEL);
    channel.postMessage({ type: "saved" });
    channel.close();
  } catch {
    // An older browser: the list catches up when its tab is focused again.
  }
}

/** Run `onSaved` whenever another tab saves a clip; returns the unsubscribe. */
export function onClipSaved(onSaved: () => void): () => void {
  try {
    const channel = new BroadcastChannel(CHANNEL);
    channel.onmessage = () => onSaved();
    return () => channel.close();
  } catch {
    return () => undefined;
  }
}
