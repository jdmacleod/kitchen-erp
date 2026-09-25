import type { ReactNode } from "react";
import { Link } from "react-router";
import { useLocations } from "../../api/geo";
import { EmptyState, primaryLinkClass } from "../ui";

/**
 * Blocks an entry form when the household has nowhere to shop yet.
 *
 * Only a *successful* empty answer blocks. Pending and errored both fall
 * through to the form on purpose:
 *
 *   - While the list loads, `LocationSelect` already shows "Loading…" in its own
 *     option and every other field stays usable. Hiding the form would make the
 *     weekly hot path wait on this query forever to suppress a one-render flash.
 *   - On a failed fetch, `LocationSelect` already says "Locations could not be
 *     loaded." An empty array is what the query hands back when it could not ask,
 *     so treating that as "you have none" would tell people their data is missing
 *     during an outage and send them to re-create it.
 *
 * Hence the check is `isSuccess && length === 0`, never `data ?? []`.
 */
export function LocationGuard({ children }: { children: ReactNode }) {
  const locations = useLocations({});

  if (locations.isSuccess && locations.data.length === 0) {
    return (
      <EmptyState
        title="No locations yet"
        action={
          <Link
            to="/map?place=location"
            className={primaryLinkClass}
          >
            Open the map
          </Link>
        }
      >
        A purchase happens somewhere. Dropping a pin on the map names the shop and its
        location in one step.
      </EmptyState>
    );
  }

  return <>{children}</>;
}
