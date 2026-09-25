import { NavLink } from "react-router";
import { useLogout, useLogoutEverywhere } from "../api/queries";
import { useCurrentUser } from "../auth/context";
import { HealthStatus } from "./HealthStatus";
import { Button, focusRing } from "./ui";

export const catalogLinks = [
  { to: "/ingredients", label: "Ingredients" },
  { to: "/products", label: "Products" },
  { to: "/vendors", label: "Vendors" },
  { to: "/map", label: "Map" },
] as const;

export const purchaseLinks = [
  { to: "/purchases", label: "Purchases", end: true },
  { to: "/purchases/new", label: "New purchase" },
  { to: "/receipts", label: "Receipts" },
  { to: "/to-identify", label: "To identify" },
  { to: "/prices/new", label: "Shelf price" },
] as const;

export const priceBookLinks = [
  { to: "/compare", label: "Compare" },
  { to: "/price-book/needs-bridge", label: "Needs a bridge" },
] as const;

function linkClass({ isActive }: { isActive: boolean }): string {
  return `block rounded-md px-3 py-2 text-sm font-medium ${focusRing} ${
    isActive
      ? "bg-blue-100 text-blue-800 dark:bg-blue-900 dark:text-blue-200"
      : "text-neutral-700 hover:bg-neutral-100 dark:text-neutral-300 dark:hover:bg-neutral-800/60"
  }`;
}

/** The full navigation list. Rendered in the desktop sidebar and the phone menu. */
export function Nav({ id }: { id?: string }) {
  const user = useCurrentUser();
  const logout = useLogout();
  const logoutEverywhere = useLogoutEverywhere();
  const busy = logout.isPending || logoutEverywhere.isPending;

  const settingsLinks = [
    ...(user.role === "admin" ? [{ to: "/settings/users", label: "Users" }] : []),
    { to: "/settings/home-bases", label: "Home bases" },
    { to: "/settings/tokens", label: "API tokens" },
  ];

  return (
    <nav id={id} aria-label="Main" className="flex h-full flex-col gap-6">
      {/* Ungrouped and above the first group label: Home belongs to no section,
          and appending it to catalogLinks would file it under "Catalog". `end`
          keeps it from matching every route, since every path starts with "/". */}
      <ul className="flex flex-col gap-0.5">
        <li>
          <NavLink to="/" end className={linkClass}>
            Home
          </NavLink>
        </li>
      </ul>
      <div>
        <p className="mb-1 px-3 text-xs font-semibold text-neutral-600 dark:text-neutral-400">Catalog</p>
        <ul className="flex flex-col gap-0.5">
          {catalogLinks.map((link) => (
            <li key={link.to}>
              <NavLink to={link.to} className={linkClass}>
                {link.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </div>
      <div>
        <p className="mb-1 px-3 text-xs font-semibold text-neutral-600 dark:text-neutral-400">Purchases</p>
        <ul className="flex flex-col gap-0.5">
          {purchaseLinks.map((link) => (
            <li key={link.to}>
              <NavLink to={link.to} end={"end" in link} className={linkClass}>
                {link.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </div>
      <div>
        <p className="mb-1 px-3 text-xs font-semibold text-neutral-600 dark:text-neutral-400">Price book</p>
        <ul className="flex flex-col gap-0.5">
          {priceBookLinks.map((link) => (
            <li key={link.to}>
              <NavLink to={link.to} className={linkClass}>
                {link.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </div>
      <div>
        <p className="mb-1 px-3 text-xs font-semibold text-neutral-600 dark:text-neutral-400">Settings</p>
        <ul className="flex flex-col gap-0.5">
          {settingsLinks.map((link) => (
            <li key={link.to}>
              <NavLink to={link.to} className={linkClass}>
                {link.label}
              </NavLink>
            </li>
          ))}
        </ul>
      </div>
      <div className="mt-auto flex flex-col gap-3 border-t border-neutral-200 pt-4 dark:border-neutral-800">
        <p className="px-3 text-sm">
          <span className="block truncate font-medium">{user.display_name}</span>
          <span className="block truncate text-xs text-neutral-600 dark:text-neutral-400">
            {user.email} · {user.role}
          </span>
        </p>
        <div className="flex flex-col gap-1 px-1">
          <Button variant="ghost" className="justify-start" disabled={busy} onClick={() => logout.mutate()}>
            Log out
          </Button>
          <Button
            variant="ghost"
            className="justify-start"
            disabled={busy}
            onClick={() => logoutEverywhere.mutate()}
          >
            Log out everywhere
          </Button>
        </div>
        <div className="px-3">
          <HealthStatus />
        </div>
      </div>
    </nav>
  );
}
