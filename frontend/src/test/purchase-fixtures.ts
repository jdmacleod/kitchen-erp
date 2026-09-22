// Synthetic Phase 2 records for tests: an invented store, invented products,
// fixed v7-shaped ids, and prices that exercise the decimal arithmetic.
import type { Observation, Purchase, PurchaseLine } from "../api/purchases";
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

// --- a receipt purchase under review (2D) -----------------------------------

export const receiptPurchaseId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8003";
export const receiptDocumentId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9001";
export const ingestJobId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9101";

export const milkProduct = { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5d03", name: "Whole Milk", brand: "Coastline", pack_qty: "1", pack_unit: "gal" };

const receiptLineBase = {
  parent_line_id: null,
  resolved_by: null,
  flags: [] as string[],
  suggestions: [],
  observation_id: null,
};

/** Quiet: resolved by a confirmed alias, nothing flagged. */
export const aliasLine: PurchaseLine = {
  ...receiptLineBase,
  id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8201",
  seq: 1,
  raw_text: "MLSTN AP FLOUR 5LB",
  raw_text_norm: "MLSTN AP FLOUR",
  line_kind: "item",
  product: { id: flourProductId, name: "All-Purpose Flour", brand: "Millstone", pack_qty: "5", pack_unit: "lb" },
  qty: "1",
  unit: "each",
  unit_price: "4.9900",
  line_total: "4.99",
  resolution: "alias",
};

/** Unmatched, with a fuzzy suggestion on top and a model suggestion under it. */
export const unmatchedLine: PurchaseLine = {
  ...receiptLineBase,
  id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8202",
  seq: 2,
  raw_text: "RVRBND BREAD FLR 2KG",
  raw_text_norm: "RVRBND BREAD FLR",
  line_kind: "item",
  product: null,
  qty: "1",
  unit: "each",
  unit_price: "6.5000",
  line_total: "6.50",
  resolution: "unmatched",
  suggestions: [
    { kind: "fuzzy", product_id: hits[1].id, ignore: false, label: "Riverbend Bread Flour", score: "0.710" },
    { kind: "llm", product_id: flourProductId, ignore: false, label: "Millstone All-Purpose Flour", score: "0.400" },
  ],
};

/** A coupon not yet attached to anything. */
export const discountLine: PurchaseLine = {
  ...receiptLineBase,
  id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8203",
  seq: 3,
  raw_text: "COUPON",
  raw_text_norm: "COUPON",
  line_kind: "discount",
  product: null,
  qty: null,
  unit: null,
  unit_price: null,
  line_total: "-1.00",
  resolution: "unmatched",
};

/** Resolved by alias but flagged: the price is far from the product's history. */
export const outlierLine: PurchaseLine = {
  ...receiptLineBase,
  id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8204",
  seq: 4,
  raw_text: "CSTLN WHOLE MILK GAL",
  raw_text_norm: "CSTLN WHOLE MILK GAL",
  line_kind: "item",
  product: milkProduct,
  qty: "1",
  unit: "each",
  unit_price: "1.0100",
  line_total: "1.01",
  resolution: "alias",
  flags: ["price_outlier"],
};

export const receiptPurchase: Purchase = {
  id: receiptPurchaseId,
  vendor_location: { id: chainLocationId, name: chainLocation.name, vendor: { id: chainLocation.vendor.id, name: chainLocation.vendor.name, kind: "chain" } },
  receipt_document_id: receiptDocumentId,
  purchased_at: "2026-09-20T18:05:00Z",
  subtotal: "11.50",
  tax: "0.00",
  total: "12.00",
  computed_total: "11.50",
  status: "draft",
  source: "receipt",
  flags: ["total_mismatch"],
  lines: [aliasLine, unmatchedLine, discountLine, outlierLine],
  created_at: "2026-09-20T18:06:00Z",
  updated_at: "2026-09-20T18:06:00Z",
};
