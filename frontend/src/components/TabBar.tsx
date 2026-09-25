import { useId, useRef } from "react";
import { Link, NavLink, useLocation } from "react-router";
import { useHealth, useLogout } from "../api/queries";
import { useCurrentUser } from "../auth/context";
import { Dialog } from "./Dialog";
import { HealthStatus } from "./HealthStatus";
import { FOOTER_SECTIONS, InboxBadge, MAIN_SECTIONS, visibleSections } from "./Nav";
import { Button, focusRing } from "./ui";

/** Stroke icons for the tabs, drawn on a 24px grid. */
const ICONS = {
  home: "M4 11 12 4l8 7v9h-5v-6H9v6H4v-9Z",
  purchases: "M6 3h12v18l-3-2-3 2-3-2-3 2V3Zm3 5h6m-6 4h6",
  capture: "M12 5v14M5 12h14",
  search: "M11 18a7 7 0 1 0 0-14 7 7 0 0 0 0 14Zm9 2-4.35-4.35",
  more: "M5 12h.01M12 12h.01M19 12h.01",
} as const;

function Icon({ d, className = "size-6", weight = 1.75 }: { d: string; className?: string; weight?: number }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth={weight} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={d} />
    </svg>
  );
}

// The current tab is herb, as the sidebar's active item is (08: active nav).
const tabClass = (current: boolean) =>
  `flex min-h-11 min-w-11 flex-1 flex-col items-center justify-center gap-0.5 rounded-md text-xs ${focusRing} ${
    current ? "font-semibold text-blue-700 dark:text-blue-300" : "font-medium text-neutral-600 dark:text-neutral-400"
  }`;

interface TabBarProps {
  onCapture: () => void;
  onSearch: () => void;
  onMore: () => void;
  /** Whether the More sheet is open, for its tab's expanded state. */
  moreOpen: boolean;
}

/**
 * The phone and tablet navigation below lg (docs/spec/09, Phone and tablet; 10, Tab
 * bar): Home, Purchases, a raised Capture, Search and More. 84px with the home
 * indicator's safe area.
 */
export function TabBar({ onCapture, onSearch, onMore, moreOpen }: TabBarProps) {
  const { pathname } = useLocation();
  const onPurchases = pathname === "/shop/purchases" || pathname.startsWith("/shop/purchases/");
  // Every page that is neither Home nor Purchases is reached through More.
  const underMore = moreOpen || (pathname !== "/" && !onPurchases);
  return (
    <nav
      aria-label="Tabs"
      className="fixed inset-x-0 bottom-0 z-30 border-t border-neutral-200 bg-neutral-50/95 px-2 pb-[max(0.5rem,env(safe-area-inset-bottom))] backdrop-blur lg:hidden dark:border-neutral-800 dark:bg-neutral-950/95"
    >
      <ul className="mx-auto flex h-[3.75rem] max-w-xl items-stretch gap-1 pt-1">
        <li className="flex flex-1">
          <NavLink to="/" end className={({ isActive }) => tabClass(isActive)}>
            <span className="relative">
              <Icon d={ICONS.home} />
              <span className="absolute -top-1.5 left-4">
                <InboxBadge />
              </span>
            </span>
            Home
          </NavLink>
        </li>
        <li className="flex flex-1">
          <Link to="/shop/purchases" aria-current={onPurchases ? "page" : undefined} className={tabClass(onPurchases)}>
            <Icon d={ICONS.purchases} />
            Purchases
          </Link>
        </li>
        <li className="flex flex-1 justify-center">
          {/* Raised above the bar: the in-store action is one thumb away. */}
          <button
            type="button"
            onClick={onCapture}
            className={`-mt-6 flex flex-col items-center gap-0.5 rounded-full text-xs font-medium text-neutral-800 dark:text-neutral-200 ${focusRing}`}
          >
            <span className="flex size-14 items-center justify-center rounded-full bg-blue-600 text-white shadow-lg ring-4 ring-neutral-50 dark:ring-neutral-950">
              <Icon d={ICONS.capture} className="size-7" />
            </span>
            Capture
          </button>
        </li>
        <li className="flex flex-1">
          <button type="button" onClick={onSearch} className={tabClass(false)}>
            <Icon d={ICONS.search} />
            Search
          </button>
        </li>
        <li className="flex flex-1">
          <button type="button" onClick={onMore} aria-haspopup="dialog" aria-expanded={moreOpen} className={tabClass(underMore)}>
            <Icon d={ICONS.more} weight={3} />
            More
          </button>
        </li>
      </ul>
    </nav>
  );
}

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `flex min-h-11 items-center rounded-md px-3 text-sm ${focusRing} ${
    isActive
      ? "bg-neutral-100 font-semibold text-neutral-900 dark:bg-neutral-800 dark:text-neutral-100"
      : "text-neutral-800 hover:bg-neutral-100 dark:text-neutral-200 dark:hover:bg-neutral-800"
  }`;

/**
 * The More tab (UI-4.9): Shop's other pages, Catalog and Settings, then the
 * account and the status line. A sheet, not a route; 09 has no `/more`.
 */
export function MoreSheet({ onClose }: { onClose: () => void }) {
  const titleId = useId();
  const user = useCurrentUser();
  const logout = useLogout();
  const features = useHealth().data?.features;
  const admin = user.role === "admin";
  // Choosing a page navigates, so focus goes to the new page, not back to More.
  const returnFocus = useRef(true);
  const choose = () => {
    returnFocus.current = false;
    onClose();
  };

  // Purchases has its own tab, so Shop lists only its other pages.
  const groups = visibleSections([...MAIN_SECTIONS, ...FOOTER_SECTIONS], features)
    .map((s) => ({
      ...s,
      items: s.items.filter((i) => (admin || !i.adminOnly) && i.to !== "/shop/purchases"),
    }))
    .filter((s) => s.items.length > 0);

  return (
    <Dialog
      open
      onClose={onClose}
      labelledBy={titleId}
      placement="sheet"
      returnFocus={returnFocus}
      className="flex max-h-[85dvh] flex-col p-4 pb-[max(1rem,env(safe-area-inset-bottom))]"
    >
      <div aria-hidden="true" className="mx-auto mb-3 h-1 w-10 shrink-0 rounded-full bg-neutral-300 dark:bg-neutral-700" />
      <h2 id={titleId} className="font-display mb-2 text-xl">
        More
      </h2>
      <nav aria-label="More" className="min-h-0 flex-1 overflow-y-auto">
        {groups.map((g) => (
          <section key={g.key} aria-labelledby={`${titleId}-${g.key}`} className="mb-3">
            <h3 id={`${titleId}-${g.key}`} className="px-3 py-1 text-xs font-semibold text-neutral-600 dark:text-neutral-400">
              {g.label}
            </h3>
            <ul className="flex flex-col">
              {g.items.map((item) => (
                <li key={item.to}>
                  <NavLink to={item.to} onClick={choose} className={linkClass}>
                    {item.label}
                  </NavLink>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </nav>
      <div className="flex shrink-0 flex-col gap-3 border-t border-neutral-200 pt-3 dark:border-neutral-800">
        <div className="flex items-center justify-between gap-2 px-3 text-sm">
          <span className="min-w-0">
            <span className="block truncate font-medium">{user.display_name}</span>
            <span className="block truncate text-xs text-neutral-600 dark:text-neutral-400">{user.email}</span>
          </span>
          <Button variant="ghost" className="shrink-0" disabled={logout.isPending} onClick={() => logout.mutate()}>
            Log out
          </Button>
        </div>
        <div className="px-3">
          <HealthStatus />
        </div>
      </div>
    </Dialog>
  );
}
