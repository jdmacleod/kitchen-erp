import { createContext, useContext } from "react";

/** What the app shell lets a page open: the Capture sheet and the search palette. */
export interface Chrome {
  openCapture: () => void;
  openSearch: () => void;
}

export const ChromeContext = createContext<Chrome>({ openCapture: () => {}, openSearch: () => {} });

export function useChrome(): Chrome {
  return useContext(ChromeContext);
}
