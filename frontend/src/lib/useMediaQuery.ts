import { useSyncExternalStore } from "react";

/** The lg breakpoint (1024px), where the phone shell gives way to the desktop one (G19). */
export const LG_QUERY = "(min-width: 1024px)";

/**
 * Whether a media query matches, kept current as the window changes. Where
 * matchMedia is missing (jsdom, very old browsers) it answers `fallback`.
 */
export function useMediaQuery(query: string, fallback = true): boolean {
  return useSyncExternalStore(
    (onChange) => {
      if (typeof window === "undefined" || typeof window.matchMedia !== "function") return () => {};
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    () => (typeof window !== "undefined" && typeof window.matchMedia === "function" ? window.matchMedia(query).matches : fallback),
    () => fallback,
  );
}
