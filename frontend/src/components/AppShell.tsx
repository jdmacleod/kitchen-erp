import { useEffect, useMemo, useRef, useState } from "react";
import { Link, Outlet, useLocation } from "react-router";
import { CaptureSheet } from "./CaptureSheet";
import { ChromeContext, type Chrome } from "./chrome";
import { Nav } from "./Nav";
import { SearchPalette } from "./SearchPalette";
import { focusRing } from "./ui";

const MENU_ID = "phone-menu";

const chromeButton =
  "inline-flex min-h-10 items-center gap-2 rounded-md px-3 text-sm font-medium text-neutral-800 hover:bg-neutral-200 dark:text-neutral-200 dark:hover:bg-neutral-800";
const captureButton =
  "inline-flex min-h-10 items-center rounded-md bg-blue-600 px-3 text-sm font-medium text-white hover:bg-blue-700";
// The shortcut as this platform writes it.
const shortcut = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘K" : "Ctrl K";

/**
 * Authenticated layout. Desktop: a fixed sidebar. Phone (below md): a header
 * with a disclosure button that opens the same navigation as a panel.
 */
export function AppShell() {
  const location = useLocation();
  const buttonRef = useRef<HTMLButtonElement>(null);
  // The menu remembers the path it was opened on, so navigating anywhere
  // closes it without an effect.
  const [openedOn, setOpenedOn] = useState<string | null>(null);
  const open = openedOn === location.pathname;
  const setOpen = (next: boolean) => setOpenedOn(next ? location.pathname : null);

  const [searching, setSearching] = useState(false);
  const [capturing, setCapturing] = useState(false);
  const chrome = useMemo<Chrome>(
    () => ({ openCapture: () => setCapturing(true), openSearch: () => setSearching(true) }),
    [],
  );

  // ⌘K / Ctrl+K opens search from any page (UI-2.8).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setCapturing(false);
        setSearching(true);
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  // Escape closes the menu and returns focus to the button that opened it.
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setOpenedOn(null);
        buttonRef.current?.focus();
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open]);

  return (
    <div className="min-h-dvh md:flex">
      <a
        href="#main"
        className={`sr-only rounded-md bg-white px-3 py-2 text-sm focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 dark:bg-neutral-900 ${focusRing}`}
      >
        Skip to content
      </a>

      {/* Phone header */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-neutral-200 bg-neutral-50/95 px-4 py-2 backdrop-blur md:hidden dark:border-neutral-800 dark:bg-neutral-950/95">
        <Link to="/" className={`font-display rounded-md py-1 text-lg font-semibold ${focusRing}`}>
          Kitchen ERP
        </Link>
        <span className="flex items-center gap-1">
          <button type="button" onClick={() => setSearching(true)} className={`${chromeButton} ${focusRing}`}>
            Search
          </button>
          <button type="button" onClick={() => setCapturing(true)} className={`${captureButton} ${focusRing}`}>
            Capture
          </button>
        <button
          ref={buttonRef}
          type="button"
          aria-expanded={open}
          aria-controls={MENU_ID}
          onClick={() => setOpen(!open)}
          className={`min-h-10 rounded-md px-3 text-sm font-medium hover:bg-neutral-200 dark:hover:bg-neutral-800 ${focusRing}`}
        >
          {open ? "Close" : "Menu"}
        </button>
        </span>
      </header>

      {/* Phone menu panel */}
      {open ? (
        <div
          id={MENU_ID}
          className="border-b border-neutral-200 bg-neutral-50 px-3 py-4 md:hidden dark:border-neutral-800 dark:bg-neutral-950"
        >
          <Nav />
        </div>
      ) : null}

      {/* Desktop sidebar */}
      <aside className="hidden w-60 shrink-0 border-r border-neutral-200 bg-neutral-100 p-3 md:sticky md:top-0 md:flex md:h-dvh md:flex-col dark:border-neutral-800 dark:bg-neutral-900/60">
        <Link to="/" className={`font-display mb-3 rounded-md px-3 py-2 text-xl font-semibold ${focusRing}`}>
          Kitchen ERP
        </Link>
        <div className="mb-4 flex gap-2 px-1">
          <button type="button" onClick={() => setSearching(true)} className={`${chromeButton} flex-1 justify-between border border-neutral-300 bg-white dark:border-neutral-700 dark:bg-neutral-900 ${focusRing}`}>
            <span>Search</span>
            <kbd className="text-xs text-neutral-600 dark:text-neutral-400">{shortcut}</kbd>
          </button>
          <button type="button" onClick={() => setCapturing(true)} className={`${captureButton} ${focusRing}`}>
            Capture
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <Nav id="desktop-nav" />
        </div>
      </aside>

      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-4xl flex-1 px-4 py-6 md:px-8 md:py-8">
        <ChromeContext.Provider value={chrome}>
          <Outlet />
        </ChromeContext.Provider>
      </main>

      {searching ? <SearchPalette onClose={() => setSearching(false)} /> : null}
      {capturing ? <CaptureSheet onClose={() => setCapturing(false)} /> : null}
    </div>
  );
}
