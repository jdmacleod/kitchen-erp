import { renderHook, waitFor } from "@testing-library/react";
import { QueryClientProvider, useMutation } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { describe, expect, it } from "vitest";
import { useInbox, type Inbox, type InboxItem } from "../api/inbox";
import { createQueryClient } from "../lib/queryClient";
import { jsonResponse, mockApi } from "./helpers";

const reading = { count: 0, oldest_at: null, stalled: false };
const item: InboxItem = {
  kind: "identify",
  title: "1 receipt line to identify",
  detail: "",
  action_label: "Review lines",
  action_route: "/shop/receipts/identify",
  created_at: "2026-09-24T18:00:00Z",
};

/**
 * UI-2.12: after a resolving mutation completes, the next render does not show the
 * resolved item, even when a request started before the mutation answers after it.
 */
describe("inbox freshness", () => {
  it("refetches after a mutation, and a stale response cannot bring the item back", async () => {
    let releaseStale: (r: Response) => void = () => {};
    let reads = 0;
    mockApi({
      "GET /inbox": () => {
        reads += 1;
        if (reads === 1) return new Promise<Response>((resolve) => (releaseStale = resolve));
        return jsonResponse(200, { items: [], reading });
      },
      "POST /to-identify/apply": () => jsonResponse(200, { applied: 1 }),
    });
    const client = createQueryClient({ retry: false });
    const wrapper = ({ children }: { children: ReactNode }) => <QueryClientProvider client={client}>{children}</QueryClientProvider>;

    const { result } = renderHook(
      () => ({
        inbox: useInbox(),
        apply: useMutation({ mutationFn: () => fetch("/api/v1/to-identify/apply", { method: "POST" }) }),
      }),
      { wrapper },
    );
    await waitFor(() => expect(reads).toBe(1));

    // The item is resolved while the first read is still in flight.
    await result.current.apply.mutateAsync();
    await waitFor(() => expect(result.current.inbox.data?.items).toEqual([]));

    // The stale read, which still had the item, arrives last and is ignored.
    releaseStale(jsonResponse(200, { items: [item], reading } satisfies Inbox));
    await new Promise((r) => setTimeout(r, 20));
    expect(result.current.inbox.data?.items).toEqual([]);
    expect(reads).toBe(2);
  });
});
