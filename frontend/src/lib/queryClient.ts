import { MutationCache, QueryClient } from "@tanstack/react-query";
import { inboxKey } from "../api/inbox";

export function createQueryClient(overrides: { retry?: number | boolean } = {}): QueryClient {
  const client: QueryClient = new QueryClient({
    defaultOptions: {
      queries: {
        retry: overrides.retry ?? 1,
        refetchOnWindowFocus: true,
      },
    },
    // Any change can settle an inbox item: a commit, an identified line, a new
    // density, a retried read. Refreshing after every successful mutation keeps
    // the list honest without each mutation having to know about the inbox.
    //
    // A read still in flight is cancelled first, so a response that started
    // before the change cannot bring a resolved item back (UI-2.12). Invalidation
    // alone would not do it: with no data yet, TanStack Query reuses the pending
    // request instead of starting a new one. Not awaited, so no mutation waits on
    // the inbox.
    mutationCache: new MutationCache({
      onSuccess: () => {
        void client
          .cancelQueries({ queryKey: inboxKey })
          .then(() => client.invalidateQueries({ queryKey: inboxKey }));
      },
    }),
  });
  return client;
}
