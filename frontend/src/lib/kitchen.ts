import { useEffect, useMemo, useRef } from "react";
import { useHomeBases, useLocation as useVendorLocation, type HomeBase } from "../api/geo";
import { useHealth } from "../api/queries";
import { useSetCurrentKitchen, useSettings, type Settings } from "../api/settings";
import { readLastLocation } from "../components/purchases/LocationSelect";

/** Whether Plan is built: `plan` on `/health` (migration 0050; 09, Phase gating). */
export function usePlanBuilt(): boolean {
  const health = useHealth();
  return health.data?.features?.includes("plan") ?? false;
}

/** Kitchens as the switcher lists them: by name. */
export function sortKitchens(kitchens: readonly HomeBase[]): HomeBase[] {
  return [...kitchens].sort((a, b) => a.name.localeCompare(b.name));
}

/**
 * The kitchen the client sends on scoped calls (17, 4A: The current kitchen), in
 * order: the server setting; the kitchen of the device's remembered location;
 * the server's suggestion (the only kitchen); the first by name. A kitchen that
 * no longer exists counts for nothing.
 */
export function resolveKitchen(
  settings: Settings | undefined,
  kitchens: readonly HomeBase[],
  rememberedKitchenId: string | null,
): string | null {
  const known = (id: string | null | undefined) =>
    id && kitchens.some((k) => k.id === id) ? id : null;
  return (
    known(settings?.current_home_base_id) ??
    known(rememberedKitchenId) ??
    known(settings?.suggested_home_base_id) ??
    sortKitchens(kitchens)[0]?.id ??
    null
  );
}

export interface CurrentKitchen {
  /** `plan` is a feature: the switcher, the tag and the scoped panels exist. */
  built: boolean;
  /** Every kitchen, by name. Empty until the list answers or when there is none. */
  kitchens: HomeBase[];
  current: HomeBase | null;
  currentId: string | null;
  /** Whether the setting and the kitchens have both answered. */
  ready: boolean;
  switchTo: (id: string) => void;
  switching: boolean;
}

/**
 * The current kitchen and the means to change it. Nothing is fetched until Plan
 * is built. With `writeBack`, a kitchen resolved by the fallback chain is written
 * to the server once, so a second device reads the same answer (UI-2.5); one
 * mounted caller passes it, the sidebar's switcher.
 */
export function useCurrentKitchen({
  writeBack = false,
}: { writeBack?: boolean } = {}): CurrentKitchen {
  const built = usePlanBuilt();
  const settings = useSettings(built);
  const homeBases = useHomeBases(built);
  const kitchens = useMemo(() => sortKitchens(homeBases.data ?? []), [homeBases.data]);

  // The device's remembered location, looked up only while the setting is empty.
  const unset = settings.data !== undefined && settings.data.current_home_base_id === null;
  const remembered = useVendorLocation(unset ? (readLastLocation() ?? undefined) : undefined);
  const rememberedKitchenId = unset ? (remembered.data?.home_base_id ?? null) : null;
  // Until that lookup answers, the chain stops at the setting, so a later
  // kitchen does not flash before the remembered one.
  const waiting = unset && remembered.isPending && remembered.fetchStatus !== "idle";

  const currentId = waiting ? null : resolveKitchen(settings.data, kitchens, rememberedKitchenId);
  const current = kitchens.find((k) => k.id === currentId) ?? null;
  const set = useSetCurrentKitchen();

  // The fallback is written back once per answer (17, 4A).
  const written = useRef<string | null>(null);
  useEffect(() => {
    if (
      !writeBack ||
      !unset ||
      waiting ||
      !currentId ||
      set.isPending ||
      written.current === currentId
    )
      return;
    written.current = currentId;
    set.mutate(currentId);
  }, [writeBack, unset, waiting, currentId, set]);

  return {
    built,
    kitchens,
    current,
    currentId,
    ready: built && settings.data !== undefined && homeBases.data !== undefined && !waiting,
    switchTo: (id) => {
      if (id !== currentId) set.mutate(id);
    },
    switching: set.isPending,
  };
}
