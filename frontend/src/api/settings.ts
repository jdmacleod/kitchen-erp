// The signed-in person's settings (17, 4A: the current kitchen). One typed row
// per user on the server, so a choice on one device is the choice on another.

import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api } from "./client";

export interface Settings {
  /** The kitchen this person sends on kitchen-scoped calls; null until chosen. */
  current_home_base_id: string | null;
  /** The only kitchen when there is exactly one, for the fallback chain. */
  suggested_home_base_id: string | null;
  updated_at?: string;
}

export const settingsKeys = {
  me: ["me", "settings"] as const,
};

/** `GET /me/settings`, only once Plan is built: older APIs have no such route. */
export function useSettings(enabled = true) {
  return useQuery({
    queryKey: settingsKeys.me,
    queryFn: () => api<Settings>("/me/settings"),
    staleTime: 60_000,
    enabled,
  });
}

export function setSettings(client: QueryClient, settings: Settings): void {
  client.setQueryData<Settings>(settingsKeys.me, settings);
}

/** `PUT /me/settings`: persist the current kitchen, then refetch to be sure. */
export function useSetCurrentKitchen() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (homeBaseId: string | null) => api<Settings>("/me/settings", { method: "PUT", body: { current_home_base_id: homeBaseId } }),
    onSuccess: (settings) => {
      setSettings(client, settings);
      void client.invalidateQueries({ queryKey: settingsKeys.me });
    },
  });
}
