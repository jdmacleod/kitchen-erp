import { QueryClient } from "@tanstack/react-query";

export function createQueryClient(overrides: { retry?: number | boolean } = {}): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: overrides.retry ?? 1,
        refetchOnWindowFocus: true,
      },
    },
  });
}
