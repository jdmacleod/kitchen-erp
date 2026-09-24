import { Navigate, Outlet, useLocation } from "react-router";
import { errorMessage } from "../api/client";
import { useMe } from "../api/queries";
import { Alert, Button, Centered } from "../components/ui";
import { useElapsed } from "../lib/useElapsed";
import { CurrentUserContext } from "./context";

/**
 * How long a wait may look like an ordinary one.
 *
 * The auth probe is the first request of every session, so this spinner is also
 * the first impression when a deployment is half up. Against a stopped API it
 * resolves in about three seconds (nginx's connect fails with EHOSTUNREACH),
 * and until it does, a slow server and an absent one look identical. Two
 * seconds is under that and well over a healthy response, so the extra line
 * appears when something really is wrong and not on an ordinary load.
 */
const SLOW_MS = 2_000;

export function RequireAuth() {
  const location = useLocation();
  const me = useMe();
  const slow = useElapsed(SLOW_MS);

  if (me.isPending) {
    return (
      <Centered>
        {/* One live region, so the extra line is announced as news about the
            same wait rather than as a second, competing status. */}
        <p role="status" className="text-center text-sm text-neutral-600 dark:text-neutral-400">
          Loading…
          {slow ? (
            <span className="mt-2 block">
              Still waiting for the server. If this deployment was just started, it may still be coming up.
            </span>
          ) : null}
        </p>
      </Centered>
    );
  }

  if (me.isError) {
    return (
      <Centered>
        <Alert tone="error">{errorMessage(me.error)}</Alert>
        <Button variant="secondary" className="mt-3 w-full" onClick={() => me.refetch()}>
          Try again
        </Button>
      </Centered>
    );
  }

  if (!me.data) {
    return <Navigate to="/login" replace state={{ from: location.pathname }} />;
  }

  return (
    <CurrentUserContext.Provider value={me.data}>
      <Outlet />
    </CurrentUserContext.Provider>
  );
}
