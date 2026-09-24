import { render, screen } from "@testing-library/react";
import { QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";
import { App } from "../App";
import { createQueryClient } from "../lib/queryClient";
import type { User } from "../api/types";

export const adminUser: User = {
  id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5a01",
  email: "admin@example.com",
  display_name: "Admin Example",
  role: "admin",
  active: true,
  created_at: "2026-01-01T00:00:00Z",
};

export const memberUser: User = {
  ...adminUser,
  id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5a02",
  email: "member@example.com",
  display_name: "Member Example",
  role: "member",
};

export function jsonResponse(status: number, body?: unknown): Response {
  if (body === undefined) return new Response(null, { status });
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

export function errorResponse(status: number, code: string, message: string): Response {
  return jsonResponse(status, { error: { code, message } });
}

export interface RecordedCall {
  method: string;
  /** Path relative to /api/v1, including any query string. */
  path: string;
  /** The query string parsed, for routes matched by pathname. */
  query: URLSearchParams;
  headers: Headers;
  body: unknown;
}

export type RouteHandler = (call: RecordedCall) => Response | Promise<Response>;

/**
 * Replace global fetch with a router keyed by "METHOD /path" (path relative to
 * /api/v1). A key without a query string also matches calls that carry one, so
 * "GET /ingredients" serves "GET /ingredients?q=flour&limit=50". Unmatched
 * calls fail loudly. Returns the recorded calls.
 */
export function mockApi(routes: Record<string, RouteHandler>): RecordedCall[] {
  const calls: RecordedCall[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
    const path = url.replace(/^\/api\/v1/, "");
    const method = (init?.method ?? "GET").toUpperCase();
    const headers = new Headers(init?.headers);
    const body = typeof init?.body === "string" ? JSON.parse(init.body) : undefined;
    const [pathname, search = ""] = path.split("?", 2);
    const call: RecordedCall = { method, path, query: new URLSearchParams(search), headers, body };
    calls.push(call);
    const handler = routes[`${method} ${path}`] ?? routes[`${method} ${pathname}`];
    if (!handler) {
      throw new Error(`Unexpected API call: ${method} ${path}`);
    }
    return handler(call);
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

/**
 * Mount the app at a path. Pass an object instead of a string to arrive with
 * router state, the way a `<Link state={…}>` or a `navigate(path, { state })`
 * hands one page a fact the next one needs.
 */
export function renderApp(initial: string | { pathname: string; state?: unknown }) {
  const client = createQueryClient({ retry: false });
  const result = render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[initial]}>
        <App />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return { ...result, client };
}

/**
 * The page's own content, excluding the shell.
 *
 * The sidebar's health line is a live region, so an unscoped `getByRole("status")`
 * now matches it as well as whatever the page under test is showing. Scope to the
 * main landmark and the query means what it used to mean.
 */
export function mainRegion(): HTMLElement {
  return screen.getByRole("main");
}
