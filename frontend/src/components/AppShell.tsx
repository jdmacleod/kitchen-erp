import { useEffect, useMemo, useState } from "react";
import { Link, Outlet } from "react-router";
import { useCurrentUser } from "../auth/context";
import { CaptureSheet } from "./CaptureSheet";
import { ChromeContext, type Chrome } from "./chrome";
import { Nav } from "./Nav";
import { NoticeProvider } from "./Notice";
import { SearchPalette } from "./SearchPalette";
import { MoreSheet, TabBar } from "./TabBar";
import { focusRing } from "./ui";

const chromeButton =
  "inline-flex min-h-10 items-center gap-2 rounded-md px-3 text-sm font-medium text-neutral-800 hover:bg-neutral-200 dark:text-neutral-200 dark:hover:bg-neutral-800";
const captureButton =
  "inline-flex min-h-10 items-center rounded-md bg-blue-600 px-3 text-sm font-medium text-white hover:bg-blue-700";
// The shortcut as this platform writes it.
const shortcut = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘K" : "Ctrl K";

/** The signed-in person's initials, for the phone header's avatar. */
function initials(name: string): string {
  const words = name.trim().split(/\s+/).filter(Boolean);
  return words
    .slice(0, 2)
    .map((w) => w[0].toUpperCase())
    .join("");
}

type Overlay = "search" | "capture" | "more" | null;

/**
 * Authenticated layout. At lg (1024px) and wider: a fixed sidebar. Below lg
 * (G19): a slim header with the wordmark and avatar, and the bottom tab bar.
 * Only the shell changes at lg; pages keep their own breakpoints (09).
 */
export function AppShell() {
  const user = useCurrentUser();
  // One overlay at a time: opening one replaces whichever is up.
  const [overlay, setOverlay] = useState<Overlay>(null);
  const close = () => setOverlay(null);
  const chrome = useMemo<Chrome>(
    () => ({ openCapture: () => setOverlay("capture"), openSearch: () => setOverlay("search") }),
    [],
  );

  // ⌘K / Ctrl+K opens search from any page (UI-2.8).
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOverlay("search");
      }
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  return (
    <div className="min-h-dvh lg:flex">
      <a
        href="#main"
        className={`sr-only rounded-md bg-white px-3 py-2 text-sm focus:not-sr-only focus:fixed focus:top-2 focus:left-2 focus:z-50 dark:bg-neutral-900 ${focusRing}`}
      >
        Skip to content
      </a>

      {/* Phone and tablet header (10, Phone: home) */}
      <header className="sticky top-0 z-30 flex items-center justify-between border-b border-neutral-200 bg-neutral-50/95 px-4 pt-[env(safe-area-inset-top)] backdrop-blur lg:hidden dark:border-neutral-800 dark:bg-neutral-950/95">
        <Link to="/" className={`font-display flex min-h-11 items-center rounded-md text-lg font-semibold ${focusRing}`}>
          Kitchen ERP
        </Link>
        <span
          role="img"
          aria-label={`Signed in as ${user.display_name}`}
          title={user.display_name}
          className="flex size-9 items-center justify-center rounded-full bg-neutral-200 text-sm font-semibold text-neutral-800 dark:bg-neutral-800 dark:text-neutral-200"
        >
          {initials(user.display_name)}
        </span>
      </header>

      {/* Desktop sidebar */}
      <aside className="hidden w-60 shrink-0 border-r border-neutral-200 bg-neutral-100 p-3 lg:sticky lg:top-0 lg:flex lg:h-dvh lg:flex-col dark:border-neutral-800 dark:bg-neutral-900/60">
        <Link to="/" className={`font-display mb-3 rounded-md px-3 py-2 text-xl font-semibold ${focusRing}`}>
          Kitchen ERP
        </Link>
        <div className="mb-4 flex gap-2 px-1">
          <button type="button" onClick={() => setOverlay("search")} className={`${chromeButton} flex-1 justify-between border border-neutral-300 bg-white dark:border-neutral-700 dark:bg-neutral-900 ${focusRing}`}>
            <span>Search</span>
            <kbd className="text-xs text-neutral-600 dark:text-neutral-400">{shortcut}</kbd>
          </button>
          <button type="button" onClick={() => setOverlay("capture")} className={`${captureButton} ${focusRing}`}>
            Capture
          </button>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto">
          <Nav id="desktop-nav" />
        </div>
      </aside>

      {/* Below lg the tab bar covers the bottom 84px, so the page ends above it. */}
      <main
        id="main"
        tabIndex={-1}
        className="mx-auto w-full max-w-4xl flex-1 px-4 pt-6 pb-[calc(6.5rem+env(safe-area-inset-bottom))] md:px-8 lg:py-8"
      >
        <ChromeContext.Provider value={chrome}>
          <NoticeProvider>
            <Outlet />
          </NoticeProvider>
        </ChromeContext.Provider>
      </main>

      <TabBar
        onCapture={() => setOverlay("capture")}
        onSearch={() => setOverlay("search")}
        onMore={() => setOverlay(overlay === "more" ? null : "more")}
        moreOpen={overlay === "more"}
      />

      {overlay === "search" ? <SearchPalette onClose={close} /> : null}
      {overlay === "capture" ? <CaptureSheet onClose={close} /> : null}
      {overlay === "more" ? <MoreSheet onClose={close} /> : null}
    </div>
  );
}
