import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { setUnauthenticatedHandler } from "../api/client";
import { resetSession } from "../api/queries";

/**
 * Connects the API client to the query cache: when any request comes back
 * 401 (a revoked session, for instance), the cached user is dropped and the
 * route guards send the browser to /login.
 */
export function AuthBridge() {
  const client = useQueryClient();
  useEffect(() => {
    setUnauthenticatedHandler(() => resetSession(client, null));
    return () => setUnauthenticatedHandler(null);
  }, [client]);
  return null;
}
