import { useEffect, useState } from "react";

/**
 * True once `ms` have passed since this mounted.
 *
 * For telling a slow wait from a broken one. A spinner that says only
 * "Loading…" looks the same whether the server is thinking or is not there at
 * all, which is how a backend outage read as the app being hung (issue #25).
 */
export function useElapsed(ms: number): boolean {
  const [elapsed, setElapsed] = useState(false);
  useEffect(() => {
    const timer = setTimeout(() => setElapsed(true), ms);
    return () => clearTimeout(timer);
  }, [ms]);
  return elapsed;
}
