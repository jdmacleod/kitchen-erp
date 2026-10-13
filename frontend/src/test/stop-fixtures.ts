// Synthetic stop screens (17, 4C). Stores, products and figures are invented;
// the stops are Balanced's in trip-fixtures; ids are fixed v7-shaped UUIDs.
import type { ProductBrief } from "../api/shoppingList";
import type { CheaperElsewhereItem, StopDone, StopScreen, StopScreenItem } from "../api/trips";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { kitchen, kitchenId, openListId, other } from "./plan-fixtures";
import { tripId } from "./trip-fixtures";

export const milkItemId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fa001";
export const lentilItemId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fa002";
export const oatsItemId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fa003";
export const purchaseId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fa101";

function product(id: string, name: string, over: Partial<ProductBrief> = {}): ProductBrief {
  return {
    id,
    name,
    ingredient_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fa2" + id.slice(-2),
    pack_qty: "946",
    pack_unit: "ml",
    pack_count: null,
    piece_name: null,
    quality_rating: null,
    active: true,
    ...over,
  };
}

export function stopItem(
  over: Partial<StopScreenItem> & { id: string; label: string },
): StopScreenItem {
  return {
    list_item_id: over.id.replace(/a0/, "b0"),
    amounts: [],
    note: null,
    product: product("0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fa301", `${over.label}, house pack`),
    packs: "1",
    pack_price: "4.50",
    line_cost: "4.50",
    observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fa401",
    planned_observed_at: "2026-10-10T18:00:00Z",
    price_stale: false,
    forced: false,
    got_here: false,
    actual_price: null,
    actual_qty: null,
    actual_unit: null,
    edited: false,
    effective_cost: over.line_cost ?? "4.50",
    claimed_by: null,
    checked_at: null,
    checked_by: null,
    ...over,
  };
}

export const milk = stopItem({
  id: milkItemId,
  label: "Oat milk",
  amounts: [{ seq: 1, amount: "2", unit: "l" }],
  product: product("0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fa302", "Moonfield oat milk"),
  packs: "2",
  pack_price: "4.25",
  line_cost: "8.50",
  effective_cost: "8.50",
});

export const lentils = stopItem({
  id: lentilItemId,
  label: "Red lentils",
  amounts: [{ seq: 1, amount: "1", unit: "kg" }],
  product: product("0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fa303", "Cedar row lentils", {
    pack_qty: "1",
    pack_unit: "kg",
  }),
  pack_price: "8.70",
  line_cost: "8.70",
  effective_cost: "8.70",
  price_stale: true,
  claimed_by: other,
});

export const oatsElsewhere: CheaperElsewhereItem = {
  id: oatsItemId,
  list_item_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fb003",
  label: "Rolled oats",
  amounts: [{ seq: 1, amount: "500", unit: "g" }],
  at_seq: 2,
  at_location_name: marketLocation.name,
  product_there: product("0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fa304", "Larkspur rolled oats"),
  line_cost_there: "10.50",
  product_here: product("0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fa305", "Harbour rolled oats"),
  packs_here: "1",
  pack_price_here: "11.30",
  line_cost_here: "11.30",
  price_stale_here: false,
  difference: "0.80",
};

export function stopScreen(over: Partial<StopScreen> = {}): StopScreen {
  return {
    plan_id: tripId,
    plan_status: "planned",
    list_id: openListId,
    home_base_id: kitchenId,
    home_base_name: kitchen.name,
    stop_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fa501",
    seq: 1,
    stop_count: 2,
    location: {
      id: chainLocation.id,
      name: chainLocation.name,
      vendor_name: chainLocation.vendor.name,
      address: "Ground floor, Harbour arcade",
    },
    arrive_at: "2026-10-17T16:12:00Z",
    predicted_cost: "17.20",
    estimated_here: "17.20",
    done_at: null,
    purchase_id: null,
    lines_recorded: null,
    actual_cost: null,
    next_seq: 2,
    items: [milk, lentils],
    cheaper_elsewhere: [oatsElsewhere],
    ...over,
  };
}

export function doneResult(over: Partial<StopDone> = {}): StopDone {
  return {
    plan_id: tripId,
    stop_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fa501",
    seq: 1,
    status: "underway",
    done_at: "2026-10-17T16:30:00Z",
    purchase_id: purchaseId,
    lines_recorded: 1,
    observation_ids: ["0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fa601"],
    shelf_observation_ids: [],
    checked: 1,
    next_seq: 2,
    ...over,
  };
}
