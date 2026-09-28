import { createContext, useContext, useEffect } from "react";

/** What the app shell lets a page open: the Capture sheet and the search palette. */
export interface Chrome {
  openCapture: () => void;
  openSearch: () => void;
  /** Ask for the wide content column; see useWidePage. */
  setWide: (wide: boolean) => void;
}

export const ChromeContext = createContext<Chrome>({ openCapture: () => {}, openSearch: () => {}, setWide: () => {} });

export function useChrome(): Chrome {
  return useContext(ChromeContext);
}

/**
 * While the calling page is mounted, the shell's content column is wide instead
 * of reading width. For work surfaces that put two things side by side: the
 * receipt review shows the receipt beside a five-column table, and at reading
 * width the table scrolled sideways under the product picker (#61).
 */
export function useWidePage(active = true): void {
  const { setWide } = useChrome();
  useEffect(() => {
    if (!active) return;
    setWide(true);
    return () => setWide(false);
  }, [active, setWide]);
}
