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
  { to: "/purchases", label: "Purchases" },
] as const;

function linkClass({ isActive }: { isActive: boolean }): string {
  return `block rounded-md px-3 py-2 text-sm font-medium ${focusRing} ${
    isActive
      ? "bg-neutral-200 text-neutral-900 dark:bg-neutral-800 dark:text-neutral-50"
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
      <div>
        <p className="mb-1 px-3 text-xs font-semibold uppercase tracking-wide text-neutral-500">Catalog</p>
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
        <p className="mb-1 px-3 text-xs font-semibold uppercase tracking-wide text-neutral-500">Settings</p>
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
