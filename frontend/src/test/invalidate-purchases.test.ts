import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it, vi } from "vitest";
import { invalidatePurchases, purchaseKeys } from "../api/purchases";

/**
 * The helper is the single place that decides what a purchase mutation clears
 * (issue #19). Two call sites used to invalidate by hand and would have missed
 * anything added to it, so its contract is pinned here: what each scope covers,
 * and that both scopes go through this one function.
 */
describe("invalidatePurchases", () => {
  function spyOn() {
    const client = new QueryClient();
    return { client, spy: vi.spyOn(client, "invalidateQueries").mockReturnValue(Promise.resolve()) };
  }

  it("clears only the lists by default", () => {
    // Enough for a mutation that already wrote its own detail back into the
    // cache, which usePurchaseMutation does.
    const { client, spy } = spyOn();
    invalidatePurchases(client);
    expect(spy).toHaveBeenCalledWith({ queryKey: [...purchaseKeys.purchases, "list"] });
  });

  it("clears every cached purchase when asked for all", () => {
    // For a mutation that changes purchases it never loaded: applying an
    // identification reaches the detail of each one it touched.
    const { client, spy } = spyOn();
    invalidatePurchases(client, "all");
    expect(spy).toHaveBeenCalledWith({ queryKey: purchaseKeys.purchases });
  });

  it("covers a detail query under the all scope but not under lists", () => {
    // The prefixes, stated as the cache sees them rather than as the literals.
    const detail = purchaseKeys.purchase("0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5a01");
    const listPrefix = [...purchaseKeys.purchases, "list"];
    expect(detail.slice(0, purchaseKeys.purchases.length)).toEqual([...purchaseKeys.purchases]);
    expect(detail.slice(0, listPrefix.length)).not.toEqual(listPrefix);
  });
});
