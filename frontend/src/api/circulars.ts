import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./client";

/** A vendor location's weekly circular (18, 2U-4; UI-9.5). */

export type Mechanism = "hosted_circular" | "storefront" | "itemized" | "image" | "region";

export interface CircularSource {
  mechanism: Mechanism;
  adapter: string;
  store_ref: Record<string, string>;
  /** ISO weekday the ad week starts: 1 is Monday. */
  publish_weekday: number;
  enabled: boolean;
  require_confirmation: boolean;
  failures: number;
  paused_until: string | null;
}

export interface FlyerRun {
  id: string;
  status: "ok" | "empty" | "failed" | "not_allowed";
  reason: string | null;
  ad_week_from: string | null;
  ad_week_to: string | null;
  fetched_at: string;
  offered_count: number;
  relevant_count: number;
}

export interface Circular {
  source: CircularSource | null;
  /** The newest first. */
  runs: FlyerRun[];
  /** A read is waiting for the helper. */
  checking: boolean;
}

export interface CircularInput {
  mechanism: Mechanism;
  adapter: string;
  store_ref: Record<string, string>;
  publish_weekday: number;
  enabled: boolean;
  /** Left out: image and region sources ask first. */
  require_confirmation?: boolean;
}

export type CircularPatch = Partial<
  Pick<CircularSource, "enabled" | "require_confirmation" | "publish_weekday">
>;

const CHECKING_POLL_MS = 15_000;

const circularKey = (id: string) => ["circular", id] as const;
const path = (id: string) => `/vendor-locations/${encodeURIComponent(id)}/circular`;

export function useCircular(locationId: string) {
  return useQuery({
    queryKey: circularKey(locationId),
    queryFn: () => api<Circular>(path(locationId)),
    // While a read waits for the helper, look again until it has answered.
    refetchInterval: (query) => (query.state.data?.checking ? CHECKING_POLL_MS : false),
  });
}

function useCircularMutation<T>(locationId: string, send: (input: T) => Promise<Circular>) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: send,
    onSuccess: (circular) => client.setQueryData(circularKey(locationId), circular),
  });
}

export function useSaveCircular(locationId: string) {
  return useCircularMutation(locationId, (input: CircularInput) =>
    api<Circular>(path(locationId), { method: "PUT", body: input }),
  );
}

export function usePatchCircular(locationId: string) {
  return useCircularMutation(locationId, (input: CircularPatch) =>
    api<Circular>(path(locationId), { method: "PATCH", body: input }),
  );
}

export function useCheckCircular(locationId: string) {
  return useCircularMutation(locationId, () =>
    api<Circular>(`${path(locationId)}/check`, { method: "POST" }),
  );
}
