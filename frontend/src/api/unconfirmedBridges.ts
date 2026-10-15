import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { catalogKeys, type CanonicalUnit } from "./catalog";
import { api } from "./client";

/** Every density and measure nobody has checked yet (issue 356). */

export type UnconfirmedKind = "density" | "measure" | "product_density";

export interface UnconfirmedValue {
  kind: UnconfirmedKind;
  /** The ingredient for a density, the measure for a measure, the product for an override. */
  id: string;
  ingredient_id: string;
  ingredient_name: string;
  canonical_unit: CanonicalUnit;
  product_name: string | null;
  label: string | null;
  /** g/ml for a density; a measure's quantity in the ingredient's unit. */
  value: string;
  source: string | null;
  /** The density a volume measure implies, to three significant figures. */
  g_per_ml: string | null;
  /** Why no food could have this value; such values come first. */
  problem: string | null;
}

export const unconfirmedKey = ["bridges", "unconfirmed"] as const;

export function useUnconfirmed() {
  return useQuery({
    queryKey: unconfirmedKey,
    queryFn: () => api<{ items: UnconfirmedValue[] }>("/bridges/unconfirmed"),
  });
}

const enc = encodeURIComponent;

/** The confirm route for each kind of value. */
function confirmPath(v: UnconfirmedValue): string {
  if (v.kind === "density") return `/ingredients/${enc(v.id)}/density/confirm`;
  if (v.kind === "measure") return `/measures/${enc(v.id)}/confirm`;
  return `/products/${enc(v.id)}/density-override/confirm`;
}

/** The write that sets a new value (from a label) or, with `null`, removes it. */
function write(v: UnconfirmedValue, value: string | null): Promise<unknown> {
  if (v.kind === "measure") {
    return value === null
      ? api(`/measures/${enc(v.id)}`, { method: "DELETE" })
      : api(`/measures/${enc(v.id)}`, {
          method: "PATCH",
          body: { canonical_qty: value, source: "label" },
        });
  }
  if (v.kind === "density") {
    return api(`/ingredients/${enc(v.id)}`, {
      method: "PATCH",
      body:
        value === null
          ? { clear_density: true }
          : { density_g_per_ml: value, density_source: "label" },
    });
  }
  return api(`/products/${enc(v.id)}`, {
    method: "PATCH",
    body:
      value === null
        ? { clear_density_override: true }
        : { density_override: value, density_override_source: "label" },
  });
}

export type UnconfirmedAction =
  { action: "confirm" } | { action: "edit"; value: string } | { action: "delete" };

/**
 * Confirm, edit or delete one value through the catalog's own routes. An edit
 * is saved from a label and confirmed in the same step, since the person has
 * just checked it; a saved edit alone would be unconfirmed again.
 */
export function useUnconfirmedAction() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async ({ target, ...change }: UnconfirmedAction & { target: UnconfirmedValue }) => {
      if (change.action === "delete") return write(target, null);
      if (change.action === "edit") await write(target, change.value);
      return api(confirmPath(target), { method: "POST" });
    },
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: unconfirmedKey });
      void client.invalidateQueries({ queryKey: catalogKeys.ingredients });
      void client.invalidateQueries({ queryKey: catalogKeys.products });
      void client.invalidateQueries({ queryKey: ["price-book"] });
      void client.invalidateQueries({ queryKey: ["inbox"] });
    },
  });
}
