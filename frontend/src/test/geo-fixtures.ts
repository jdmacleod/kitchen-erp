// Synthetic geography for tests: every coordinate sits in the Pacific box from
// SECURITY.md (lat 33–34, lon -121 to -120). Vendors and names are invented.
import type { HomeBase, Vendor, VendorLocation, VendorLocationDetail } from "../api/geo";

export const homeBaseId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5e01";
export const chainVendorId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5f01";
export const marketVendorId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5f02";
export const chainLocationId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f6001";
export const marketLocationId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f6002";
export const stallLocationId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f6003";

export const homeBase: HomeBase = {
  id: homeBaseId,
  name: "Harbour flat",
  lat: "33.400000",
  lon: "-120.600000",
  label: null,
  created_at: "2026-03-01T00:00:00Z",
};

export const chainVendor: Vendor = {
  id: chainVendorId,
  name: "Millstone Market",
  kind: "chain",
  price_scope: "chain",
  website: null,
  notes: null,
  active: true,
  created_at: "2026-03-01T00:00:00Z",
};

export const marketVendor: Vendor = {
  id: marketVendorId,
  name: "Pier Farmers Market",
  kind: "market",
  price_scope: "location",
  website: null,
  notes: null,
  active: true,
  created_at: "2026-03-01T00:00:00Z",
};

const base = {
  address: null,
  home_base_id: homeBaseId,
  parent_location_id: null,
  stop_overhead_min: null,
  receipt_identifiers: [] as string[],
  osm_type: null,
  osm_id: null,
  active: true,
  is_open: null,
  distance_m: null,
  created_at: "2026-03-01T00:00:00Z",
};

export const chainLocation: VendorLocation = {
  ...base,
  id: chainLocationId,
  vendor: { id: chainVendorId, name: chainVendor.name, kind: "chain", price_scope: "chain" },
  name: "Millstone Harbour",
  lat: "33.450000",
  lon: "-120.550000",
  opening_hours: "Mo-Su 07:00-22:00",
  effective_opening_hours: "Mo-Su 07:00-22:00",
  opening_hours_inherited: false,
};

export const marketLocation: VendorLocation = {
  ...base,
  id: marketLocationId,
  vendor: { id: marketVendorId, name: marketVendor.name, kind: "market", price_scope: "location" },
  name: "Pier Farmers Market",
  lat: "33.520000",
  lon: "-120.480000",
  opening_hours: "Sa 08:00-13:00",
  effective_opening_hours: "Sa 08:00-13:00",
  opening_hours_inherited: false,
};

export const stallLocation: VendorLocation = {
  ...base,
  id: stallLocationId,
  vendor: { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5f03", name: "Sandy's Stone Fruit", kind: "stand", price_scope: "location" },
  name: "Sandy's Stone Fruit",
  lat: "33.520000",
  lon: "-120.480000",
  parent_location_id: marketLocationId,
  opening_hours: null,
  effective_opening_hours: "Sa 08:00-13:00",
  opening_hours_inherited: true,
};

export const marketDetail: VendorLocationDetail = { ...marketLocation, stalls: [stallLocation] };
