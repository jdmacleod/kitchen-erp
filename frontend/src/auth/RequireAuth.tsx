import { Navigate, Outlet, useLocation } from "react-router";
import { errorMessage } from "../api/client";
import { useMe } from "../api/queries";
import { Alert, Button, Centered } from "../components/ui";
import { CurrentUserContext } from "./context";

export function RequireAuth() {
  const location = useLocation();
  const me = useMe();

  if (me.isPending) {
    return (
      <Centered>
        <p role="status" className="text-center text-sm text-neutral-600 dark:text-neutral-400">
          Loading…
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
