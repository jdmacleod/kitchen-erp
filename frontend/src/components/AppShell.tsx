import { useEffect, useRef, useState } from "react";
import { Link, Outlet, useLocation } from "react-router";
import { Nav } from "./Nav";
import { focusRing } from "./ui";

const MENU_ID = "phone-menu";

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
        <Link to="/" className={`rounded-md py-1 text-base font-semibold ${focusRing}`}>
          Kitchen ERP
        </Link>
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
        <Link to="/" className={`mb-6 rounded-md px-3 py-2 text-base font-semibold ${focusRing}`}>
          Kitchen ERP
        </Link>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <Nav id="desktop-nav" />
        </div>
      </aside>

      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-4xl flex-1 px-4 py-6 md:px-8 md:py-8">
        <Outlet />
      </main>
    </div>
  );
}
