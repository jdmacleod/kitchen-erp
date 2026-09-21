// Synthetic catalog records for tests. Names are invented; ids are fixed v7-shaped UUIDs.
import type { Ingredient, Measure, Product, SearchHit, Unit } from "../api/catalog";

export const units: Unit[] = [
  { code: "g", dimension: "mass", to_base_factor: "1", system: "metric", aliases: ["gram", "grams"] },
  { code: "kg", dimension: "mass", to_base_factor: "1000", system: "metric", aliases: ["kilogram"] },
  { code: "oz", dimension: "mass", to_base_factor: "28.349523125", system: "us", aliases: ["ounce"] },
  { code: "lb", dimension: "mass", to_base_factor: "453.59237", system: "us", aliases: ["lbs", "pound"] },
  { code: "ml", dimension: "volume", to_base_factor: "1", system: "metric", aliases: ["millilitre"] },
  { code: "cup", dimension: "volume", to_base_factor: "236.5882365", system: "us", aliases: ["cups"] },
  { code: "each", dimension: "count", to_base_factor: "1", system: "count", aliases: ["ea"] },
];

export const flourId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5b01";
export const cupMeasureId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5c01";

export const cupMeasure: Measure = {
  id: cupMeasureId,
  ingredient_id: flourId,
  label: "cup",
  canonical_qty: "125",
  source: "usda",
  confirmed: false,
  created_at: "2026-03-01T00:00:00Z",
  updated_at: "2026-03-01T00:00:00Z",
};

export const flour: Ingredient = {
  id: flourId,
  name: "all-purpose flour",
  category: "pantry",
  canonical_unit: "g",
  density_g_per_ml: null,
  density_source: null,
  density_confirmed: false,
  yield_pct: "1",
  perishability: "shelf_stable",
  active: true,
  notes: null,
  measures: [cupMeasure],
  created_at: "2026-03-01T00:00:00Z",
  updated_at: "2026-03-01T00:00:00Z",
};

export const flourProductId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5d01";

export const flourProduct: Product = {
  id: flourProductId,
  ingredient: { id: flourId, name: flour.name, canonical_unit: "g", active: true },
  brand: "Millstone",
  name: "All-Purpose Flour",
  pack_qty: "5",
  pack_unit: "lb",
  barcode: "012345678905", // pii-scan: allow documented placeholder UPC
  quality_rating: 4,
  exclusive_vendor_id: null,
  density_override: null,
  density_override_source: null,
  density_override_confirmed: false,
  active: true,
  notes: null,
  created_at: "2026-03-02T00:00:00Z",
  updated_at: "2026-03-02T00:00:00Z",
};

export const hits: SearchHit[] = [
  {
    id: flourProductId,
    name: "All-Purpose Flour",
    brand: "Millstone",
    barcode: flourProduct.barcode,
    pack_qty: "5",
    pack_unit: "lb",
    quality_rating: 4,
    ingredient: flourProduct.ingredient,
    match: "name",
    score: "0.9",
  },
  {
    id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5d02",
    name: "Bread Flour",
    brand: "Riverbend",
    barcode: null,
    pack_qty: "2",
    pack_unit: "kg",
    quality_rating: null,
    ingredient: { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5b02", name: "bread flour", canonical_unit: "g", active: true },
    match: "ingredient",
    score: "0.6",
  },
];
