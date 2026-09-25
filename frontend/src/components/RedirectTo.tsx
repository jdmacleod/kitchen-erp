import { Navigate, generatePath, useLocation, useParams } from "react-router";

/**
 * A client-side redirect from a retired path to its new route (docs/spec/09, Routes).
 *
 * Route parameters, the query string, the hash and router state all carry over, so
 * an old bookmark or a link in a message lands exactly where it pointed. `query`
 * adds parameters the new route needs, such as `view=map`; they come first, and a
 * value the old URL already carried wins over nothing.
 */
export function RedirectTo({ to, query = {} }: { to: string; query?: Record<string, string> }) {
  const params = useParams();
  const location = useLocation();
  const search = new URLSearchParams(query);
  for (const [key, value] of new URLSearchParams(location.search)) {
    if (!(key in query)) search.append(key, value);
  }
  const qs = search.toString();
  return (
    <Navigate
      replace
      to={{ pathname: generatePath(to, params), search: qs ? `?${qs}` : "", hash: location.hash }}
      state={location.state}
    />
  );
}
