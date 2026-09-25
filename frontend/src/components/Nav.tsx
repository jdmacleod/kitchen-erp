import { Link, NavLink, useLocation } from "react-router";
import { useInbox } from "../api/inbox";
import { useHealth, useLogout } from "../api/queries";
import { useCurrentUser } from "../auth/context";
import { HealthStatus } from "./HealthStatus";
import { Button, focusRing } from "./ui";

interface NavItem {
  to: string;
  label: string;
  adminOnly?: boolean;
}

export interface NavSection {
  key: string;
  label: string;
  /** The section's own link: its first page. */
  to: string;
  /** Paths under this prefix belong to the section and expand it. */
  prefix?: string;
  /** The `/health` feature that builds it; no feature means always built. */
  feature?: string;
  items: NavItem[];
}

/** The body of the sidebar, in the order of docs/spec/09 (Sections). */
export const MAIN_SECTIONS: NavSection[] = [
  { key: "home", label: "Home", to: "/", items: [] },
  {
    key: "shop",
    label: "Shop",
    to: "/shop/purchases",
    prefix: "/shop",
    feature: "shop",
    items: [
      { to: "/shop/purchases", label: "Purchases" },
      { to: "/shop/receipts", label: "Receipts" },
      { to: "/shop/shelf-prices", label: "Shelf prices" },
      { to: "/shop/compare", label: "Compare prices" },
    ],
  },
];

/** The footer: Catalog, then Settings. */
export const FOOTER_SECTIONS: NavSection[] = [
  {
    key: "catalog",
    label: "Catalog",
    to: "/catalog/ingredients",
    prefix: "/catalog",
    feature: "catalog",
    items: [
      { to: "/catalog/ingredients", label: "Ingredients" },
      { to: "/catalog/products", label: "Products" },
      { to: "/catalog/vendors", label: "Vendors" },
    ],
  },
  {
    key: "settings",
    label: "Settings",
    to: "/settings/kitchens",
    prefix: "/settings",
    items: [
      { to: "/settings/kitchens", label: "Kitchens" },
      { to: "/settings/users", label: "Users", adminOnly: true },
      { to: "/settings/tokens", label: "API tokens" },
      { to: "/settings/system", label: "System" },
    ],
  },
];

/** Phases 1 and 2, shown before `/health` answers and while it fails (D11). */
export const DEFAULT_FEATURES = ["catalog", "shop"] as const;

/**
 * The sections that are built. Features only add to the defaults, never remove,
 * and a section keeps its fixed place however late its feature arrives (G16).
 */
export function visibleSections(sections: NavSection[], features: readonly string[] | undefined): NavSection[] {
  const built = new Set<string>([...DEFAULT_FEATURES, ...(features ?? [])]);
  return sections.filter((s) => !s.feature || built.has(s.feature));
}

function sectionClass(active: boolean): string {
  return `flex min-h-10 items-center justify-between gap-2 rounded-md px-3 text-sm ${focusRing} ${
    active
      ? "bg-blue-100 font-semibold text-blue-800 dark:bg-blue-900 dark:text-blue-100"
      : "font-medium text-neutral-700 hover:bg-neutral-200/70 dark:text-neutral-300 dark:hover:bg-neutral-800"
  }`;
}

function itemClass({ isActive }: { isActive: boolean }): string {
  return `flex min-h-9 items-center rounded-md px-3 text-sm ${focusRing} ${
    isActive
      ? "bg-white font-medium text-neutral-900 shadow-sm dark:bg-neutral-800 dark:text-neutral-100"
      : "text-neutral-700 hover:bg-neutral-200/70 dark:text-neutral-300 dark:hover:bg-neutral-800"
  }`;
}

/**
 * The inbox count on Home (G6, G16, D6): hidden until the first answer, "!" when
 * the inbox could not be checked, and nothing when nothing needs you.
 */
export function InboxBadge() {
  const inbox = useInbox();
  const pill = "inline-flex min-w-6 items-center justify-center rounded-full px-1.5 text-xs font-semibold";
  if (inbox.isError && !inbox.data) {
    return (
      <span role="img" aria-label="Couldn't check what needs you" className={`${pill} bg-neutral-200 text-neutral-800 dark:bg-neutral-700 dark:text-neutral-100`}>
        !
      </span>
    );
  }
  const count = inbox.data?.items.length ?? 0;
  if (count === 0) return null;
  return (
    <span aria-label={`${count} ${count === 1 ? "thing needs" : "things need"} you`} className={`${pill} bg-amber-200 text-amber-900 dark:bg-amber-900 dark:text-amber-100`}>
      {count}
    </span>
  );
}

function Section({ section, admin }: { section: NavSection; admin: boolean }) {
  const { pathname } = useLocation();
  const active = section.prefix ? pathname === section.prefix || pathname.startsWith(`${section.prefix}/`) : pathname === section.to;
  const items = section.items.filter((i) => admin || !i.adminOnly);
  return (
    <li>
      {/* A plain link: the section is "current" as a place, and the sub-page link
          below is the one that is the current page. */}
      <Link to={section.to} className={sectionClass(active)} aria-current={active ? (section.prefix ? "true" : "page") : undefined}>
        <span>{section.label}</span>
        {section.key === "home" ? <InboxBadge /> : null}
      </Link>
      {/* Only the active section expands (UI-2.1). */}
      {active && items.length > 0 ? (
        <ul className="mt-0.5 mb-1 ml-3 flex flex-col gap-0.5 border-l border-neutral-200 pl-2 dark:border-neutral-800">
          {items.map((item) => (
            <li key={item.to}>
              <NavLink to={item.to} className={itemClass}>
                {item.label}
              </NavLink>
            </li>
          ))}
        </ul>
      ) : null}
    </li>
  );
}

/** The navigation. Rendered in the desktop sidebar and the phone menu. */
export function Nav({ id }: { id?: string }) {
  const user = useCurrentUser();
  const logout = useLogout();
  const health = useHealth();
  const features = health.data?.features;
  const admin = user.role === "admin";

  return (
    <nav id={id} aria-label="Main" className="flex h-full flex-col gap-4">
      <ul className="flex flex-col gap-0.5">
        {visibleSections(MAIN_SECTIONS, features).map((s) => (
          <Section key={s.key} section={s} admin={admin} />
        ))}
      </ul>
      <div className="mt-auto flex flex-col gap-3 border-t border-neutral-200 pt-3 dark:border-neutral-800">
        <ul className="flex flex-col gap-0.5">
          {visibleSections(FOOTER_SECTIONS, features).map((s) => (
            <Section key={s.key} section={s} admin={admin} />
          ))}
        </ul>
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
    </nav>
  );
}
