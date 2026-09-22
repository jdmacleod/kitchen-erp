// Synthetic Phase 2 records for tests: an invented store, invented products,
// fixed v7-shaped ids, and prices that exercise the decimal arithmetic.
import type { Observation, Purchase } from "../api/purchases";
import { flourId, flourProductId, hits } from "./catalog-fixtures";
import { chainLocation, chainLocationId, marketLocation, marketLocationId } from "./geo-fixtures";

export const observationId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7001";
export const purchaseId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8001";

export const observationOk: Observation = {
  id: observationId,
  product: {
    id: flourProductId,
    name: "All-Purpose Flour",
    brand: "Millstone",
    pack_qty: "5",
    pack_unit: "lb",
    ingredient: { id: flourId, name: "all-purpose flour", canonical_unit: "g" },
  },
  vendor_location: { id: chainLocationId, name: chainLocation.name, vendor: chainLocation.vendor },
  purchase_line_id: null,
  observed_at: "2026-09-21T15:00:00Z",
  price: "4.99",
  qty: "1",
  unit: "each",
  is_promo: false,
  source: "shelf",
  voided: false,
  norm: {
    status: "ok",
    canonical_qty: "2267.96185",
    norm_unit: "g",
    norm_unit_price: "0.002200",
    bridge_kind: "pack",
    bridge_source: "label",
    bridge_confirmed: true,
  },
  created_at: "2026-09-21T15:00:00Z",
};

export const observationNoDensity: Observation = {
  ...observationOk,
  id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7002",
  unit: "cup",
  is_promo: true,
  norm: {
    status: "no_density",
    canonical_qty: null,
    norm_unit: null,
    norm_unit_price: null,
    bridge_kind: null,
    bridge_source: null,
    bridge_confirmed: null,
  },
};

export const manualPurchase: Purchase = {
  id: purchaseId,
  vendor_location: {
    id: marketLocationId,
    name: marketLocation.name,
    vendor: { id: marketLocation.vendor.id, name: marketLocation.vendor.name, kind: "market" },
  },
  receipt_document_id: null,
  purchased_at: "2026-09-19T16:30:00Z",
  subtotal: null,
  tax: null,
  total: "14.21",
  computed_total: "14.2169",
  status: "committed",
  source: "manual",
  flags: [],
  lines: [
    {
      id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8101",
      seq: 1,
      raw_text: null,
      line_kind: "item",
      product: { id: flourProductId, name: "All-Purpose Flour", brand: "Millstone", pack_qty: "5", pack_unit: "lb" },
      parent_line_id: null,
      qty: "2.31",
      unit: "lb",
      unit_price: "3.9900",
      line_total: "9.2169",
      resolution: "manual",
      flags: [],
      observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7101",
    },
    {
      id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8102",
      seq: 2,
      raw_text: null,
      line_kind: "item",
      product: { id: hits[1].id, name: hits[1].name, brand: hits[1].brand, pack_qty: hits[1].pack_qty, pack_unit: hits[1].pack_unit },
      parent_line_id: null,
      qty: "1",
      unit: "each",
      unit_price: "5.0000",
      line_total: "5.0000",
      resolution: "manual",
      flags: [],
      observation_id: null,
    },
  ],
  created_at: "2026-09-19T16:31:00Z",
  updated_at: "2026-09-19T16:31:00Z",
};
