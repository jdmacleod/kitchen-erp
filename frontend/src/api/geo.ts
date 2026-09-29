// Phase 1D/1E wire types and hooks: home bases, vendors, vendor locations,
// opening hours validation, and the optional OpenStreetMap adoption.
// Coordinates are decimal strings on the wire; they become numbers only at the
// map edge, where MapLibre needs them.

import { useMutation, useQuery, useQueryClient, type QueryClient } from "@tanstack/react-query";
import { api, isApiError, newIdempotencyKey } from "./client";
import { qs } from "./catalog";
import type { ListResponse } from "./types";

// --- types ------------------------------------------------------------------

export type VendorKind = "chain" | "independent" | "market" | "stand";
export type PriceScope = "chain" | "location";
export type OsmType = "node" | "way" | "relation";

export const VENDOR_KINDS: readonly VendorKind[] = ["chain", "independent", "market", "stand"];
export const PRICE_SCOPES: readonly PriceScope[] = ["location", "chain"];

export const vendorKindLabel: Record<VendorKind, string> = {
  chain: "Chain",
  independent: "Independent",
  market: "Market",
  stand: "Stand",
};

/** A vendor's pricing scope in plain words (T15). The one place this copy lives. */
export const priceScopeLabel: Record<PriceScope, string> = {
  location: "Price set per location",
  chain: "Same price at every location",
};

export interface HomeBase {
  id: string;
  name: string;
  lat: string;
  lon: string;
  label: string | null;
  created_at: string;
}

/** Where a field's current value came from (1F). A field a person entered has none. */
export interface FieldSource {
  /** osm | observed | import | enriched:<tool> */
  source: string;
  ref: string | null;
  checked_at: string | null;
}

export type Sources = Partial<Record<string, FieldSource>>;

export interface VendorRef {
  id: string;
  name: string;
  kind: VendorKind;
  price_scope: PriceScope;
}

export interface Vendor extends VendorRef {
  /** The stable key in kitchen-erp-vendors files; never follows a rename. */
  slug: string;
  website: string | null;
  brand: string | null;
  wikidata: string | null;
  notes: string | null;
  active: boolean;
  created_at: string;
  sources: Sources;
}

/** A row of the vendor list, with what its card shows (T16). */
export interface VendorListItem extends Vendor {
  location_count: number;
  /** The latest committed purchase at any of its locations. */
  last_visit: string | null;
}

export interface VendorLocation {
  id: string;
  vendor: VendorRef;
  name: string;
  /** "<vendor slug>/<location slug>": the stable key in kitchen-erp-vendors files. */
  key: string;
  lat: string;
  lon: string;
  address: string | null;
  phone: string | null;
  home_base_id: string | null;
  parent_location_id: string | null;
  opening_hours: string | null;
  effective_opening_hours: string | null;
  opening_hours_inherited: boolean;
  stop_overhead_min: number | null;
  receipt_identifiers: string[];
  osm_type: OsmType | null;
  osm_id: number | null;
  /** May appear in a public export (1F). A linked location always may; a stand never. */
  publishable: boolean;
  sources: Sources;
  active: boolean;
  is_open: boolean | null;
  distance_m: string | null;
  created_at: string;
}

export interface VendorLocationDetail extends VendorLocation {
  stalls: VendorLocation[];
}

export interface IsOpenResult {
  id: string;
  at: string;
  is_open: boolean | null;
  effective_opening_hours: string | null;
}

export interface OsmCandidate {
  osm_type: OsmType;
  osm_id: number;
  name: string | null;
  kind_guess: VendorKind;
  lat: string;
  lon: string;
  address: string | null;
  opening_hours: string | null;
  already_adopted: boolean;
}

export type LinkedField = "name" | "address" | "opening_hours" | "phone" | "website";

/** An OpenStreetMap object near a location's pin, and what linking it would change (1F). */
export interface LinkCandidate {
  osm_type: OsmType;
  osm_id: number;
  name: string | null;
  kind_guess: VendorKind;
  address: string | null;
  opening_hours: string | null;
  phone: string | null;
  website: string | null;
  distance_m: number;
  linked_to: { id: string; name: string } | null;
  fills: LinkedField[];
  /** Fields a person edited, which linking leaves alone. */
  keeps: LinkedField[];
}

// --- inputs -----------------------------------------------------------------

export interface HomeBaseInput {
  name: string;
  lat: string;
  lon: string;
  label?: string | null;
}

export interface VendorCreateInput {
  name: string;
  kind: VendorKind;
  price_scope?: PriceScope;
  website?: string;
  notes?: string;
}

export interface VendorUpdateInput {
  name?: string;
  kind?: VendorKind;
  price_scope?: PriceScope;
  website?: string | null;
  notes?: string | null;
}

export interface VendorInline {
  name: string;
  kind: VendorKind;
  price_scope?: PriceScope;
}

export interface LocationCreateInput {
  vendor_id?: string;
  vendor?: VendorInline;
  name: string;
  lat: string;
  lon: string;
  address?: string;
  phone?: string;
  parent_location_id?: string;
  opening_hours?: string;
  /** Omit for the nearest home base; explicit null for none. */
  home_base_id?: string | null;
  stop_overhead_min?: number;
  receipt_identifiers?: string[];
}

export interface LocationUpdateInput {
  name?: string;
  lat?: string;
  lon?: string;
  address?: string | null;
  phone?: string | null;
  publishable?: boolean;
  parent_location_id?: string | null;
  opening_hours?: string | null;
  home_base_id?: string | null;
  stop_overhead_min?: number | null;
  receipt_identifiers?: string[];
}

export interface LocationFilters {
  near?: string;
  kind?: VendorKind | "";
  home_base_id?: string;
  vendor_id?: string;
  /** ISO-8601 instant; when given the server returns only locations open then. */
  open_at?: string;
  include_inactive?: boolean;
}

export interface OsmAdoptInput {
  osm_type: OsmType;
  osm_id: number;
  home_base_id: string;
  radius_m: number;
  vendor_kind?: VendorKind;
}

// --- errors -----------------------------------------------------------------

export const INTEGRATION_DISABLED = "integration_disabled";

/** True when the server said the OpenStreetMap integration is switched off. */
export function isIntegrationDisabled(e: unknown): boolean {
  return isApiError(e) && e.code === INTEGRATION_DISABLED;
}

const knownMessages: Record<string, string> = {
  vendor_name_taken: "A vendor with that name already exists.",
  parent_is_stall: "A stall cannot contain stalls; choose the market itself.",
  has_stalls: "This location has stalls of its own and cannot become a stall.",
  [INTEGRATION_DISABLED]: "OpenStreetMap adoption is off; set ENABLE_OVERPASS=true.",
  already_linked: "This location is already linked to OpenStreetMap; unlink it first.",
  already_adopted: "That OpenStreetMap place is already linked to another location.",
  osm_candidate_not_found: "That place is no longer among the OpenStreetMap places near this location.",
};

/** A message for a geo mutation error, with known codes spelled out. */
export function geoErrorMessage(e: unknown): string {
  if (isApiError(e)) {
    if (e.code === "home_base_in_use") {
      const locations = (e.details as { locations?: unknown } | undefined)?.locations;
      const count = Array.isArray(locations) ? locations.length : null;
      return count
        ? `This home base is the default for ${count} ${count === 1 ? "location" : "locations"}. Reassign them first.`
        : "This home base is in use by locations. Reassign them first.";
    }
    return knownMessages[e.code] ?? e.message;
  }
  if (e instanceof TypeError) return "Could not reach the server.";
  if (e instanceof Error && e.message) return e.message;
  return "Something went wrong.";
}

const idem = () => ({ "Idempotency-Key": newIdempotencyKey() });
const enc = encodeURIComponent;

// --- keys -------------------------------------------------------------------

export const geoKeys = {
  homeBases: ["home-bases"] as const,
  vendors: ["vendors"] as const,
  vendorList: (q: string, includeInactive: boolean) => ["vendors", "list", { q, includeInactive }] as const,
  vendor: (id: string) => ["vendors", "detail", id] as const,
  locations: ["vendor-locations"] as const,
  locationList: (filters: LocationFilters) => ["vendor-locations", "list", filters] as const,
  location: (id: string) => ["vendor-locations", "detail", id] as const,
  isOpen: (id: string, at: string) => ["vendor-locations", "is-open", id, at] as const,
  osmCandidates: (homeBaseId: string, radius: number) => ["osm", "candidates", homeBaseId, radius] as const,
  exportSummary: ["vendors", "export-summary"] as const,
  linkCandidates: (locationId: string) => ["vendor-locations", "osm-candidates", locationId] as const,
  mapPlaces: (q: string, near: { lat: string; lon: string } | null) => ["map", "places", q, near] as const,
};

function invalidateLocations(client: QueryClient) {
  void client.invalidateQueries({ queryKey: geoKeys.locations });
  // Sharing, linking and deactivating all change what a public export holds.
  void client.invalidateQueries({ queryKey: geoKeys.exportSummary });
}

// --- home bases -------------------------------------------------------------

export function useHomeBases() {
  return useQuery({
    queryKey: geoKeys.homeBases,
    queryFn: () => api<ListResponse<HomeBase>>("/home-bases"),
    select: (data) => data.items,
    staleTime: 60_000,
  });
}

export function useCreateHomeBase() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: HomeBaseInput) => api<HomeBase>("/home-bases", { method: "POST", body: input, headers: idem() }),
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: geoKeys.homeBases });
      invalidateLocations(client);
    },
  });
}

export function useUpdateHomeBase() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...input }: Partial<HomeBaseInput> & { id: string }) =>
      api<HomeBase>(`/home-bases/${enc(id)}`, { method: "PATCH", body: input }),
    onSuccess: () => void client.invalidateQueries({ queryKey: geoKeys.homeBases }),
  });
}

export function useDeleteHomeBase() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => api<void>(`/home-bases/${enc(id)}`, { method: "DELETE" }),
    onSuccess: () => void client.invalidateQueries({ queryKey: geoKeys.homeBases }),
  });
}

// --- vendors ----------------------------------------------------------------

export function useVendors(q = "", includeInactive = false, enabled = true) {
  const trimmed = q.trim();
  return useQuery({
    queryKey: geoKeys.vendorList(trimmed, includeInactive),
    queryFn: () => api<ListResponse<VendorListItem>>(`/vendors${qs({ q: trimmed, include_inactive: includeInactive })}`),
    select: (data) => data.items,
    enabled,
    placeholderData: (previous) => previous,
  });
}

export function useVendor(id: string | undefined) {
  return useQuery({
    queryKey: geoKeys.vendor(id ?? ""),
    queryFn: () => api<Vendor>(`/vendors/${enc(id ?? "")}`),
    enabled: Boolean(id),
  });
}

export function useCreateVendor() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: VendorCreateInput) => api<Vendor>("/vendors", { method: "POST", body: input, headers: idem() }),
    onSuccess: () => void client.invalidateQueries({ queryKey: geoKeys.vendors }),
  });
}

export function useUpdateVendor(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: VendorUpdateInput) => api<Vendor>(`/vendors/${enc(id)}`, { method: "PATCH", body: input }),
    onSuccess: (updated) => {
      client.setQueryData(geoKeys.vendor(id), updated);
      void client.invalidateQueries({ queryKey: geoKeys.vendors });
      invalidateLocations(client);
    },
  });
}

export function useSetVendorActive(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (active: boolean) =>
      api<Vendor>(`/vendors/${enc(id)}/${active ? "activate" : "deactivate"}`, { method: "POST" }),
    onSuccess: (updated) => {
      client.setQueryData(geoKeys.vendor(id), updated);
      void client.invalidateQueries({ queryKey: geoKeys.vendors });
    },
  });
}

// --- vendor locations -------------------------------------------------------

export function useLocations(filters: LocationFilters, enabled = true) {
  return useQuery({
    queryKey: geoKeys.locationList(filters),
    queryFn: () =>
      api<ListResponse<VendorLocation>>(
        `/vendor-locations${qs({
          near: filters.near,
          kind: filters.kind,
          home_base_id: filters.home_base_id,
          vendor_id: filters.vendor_id,
          open_at: filters.open_at,
          include_inactive: filters.include_inactive,
        })}`,
      ),
    select: (data) => data.items,
    enabled,
    placeholderData: (previous) => previous,
  });
}

export function useLocation(id: string | undefined) {
  return useQuery({
    queryKey: geoKeys.location(id ?? ""),
    queryFn: () => api<VendorLocationDetail>(`/vendor-locations/${enc(id ?? "")}`),
    enabled: Boolean(id),
  });
}

export function useIsOpen(id: string | undefined, at: string | undefined) {
  return useQuery({
    queryKey: geoKeys.isOpen(id ?? "", at ?? ""),
    queryFn: () => api<IsOpenResult>(`/vendor-locations/${enc(id ?? "")}/is-open${qs({ at })}`),
    enabled: Boolean(id) && Boolean(at),
  });
}

export function useCreateLocation() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: LocationCreateInput) =>
      api<VendorLocationDetail>("/vendor-locations", { method: "POST", body: input, headers: idem() }),
    onSuccess: () => {
      invalidateLocations(client);
      // An inline vendor may have been created too.
      void client.invalidateQueries({ queryKey: geoKeys.vendors });
    },
  });
}

export function useUpdateLocation(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: LocationUpdateInput) =>
      api<VendorLocationDetail>(`/vendor-locations/${enc(id)}`, { method: "PATCH", body: input }),
    onSuccess: () => invalidateLocations(client),
  });
}

export function useSetLocationActive(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (active: boolean) =>
      api<VendorLocationDetail>(`/vendor-locations/${enc(id)}/${active ? "activate" : "deactivate"}`, { method: "POST" }),
    onSuccess: () => invalidateLocations(client),
  });
}

export function useRefreshOsm(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<VendorLocationDetail>(`/vendor-locations/${enc(id)}/refresh-osm`, { method: "POST" }),
    // A refresh may fill the vendor's website too.
    onSuccess: () => {
      invalidateLocations(client);
      void client.invalidateQueries({ queryKey: geoKeys.vendors });
    },
  });
}

/** OpenStreetMap places near a location's pin, nearest first (1F). */
export function useLinkCandidates(locationId: string, enabled: boolean) {
  return useQuery({
    queryKey: geoKeys.linkCandidates(locationId),
    queryFn: () => api<ListResponse<LinkCandidate>>(`/vendor-locations/${enc(locationId)}/osm-candidates`),
    select: (data) => data.items,
    enabled,
    retry: false,
    staleTime: 5 * 60_000,
  });
}

export function useLinkOsm(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: { osm_type: OsmType; osm_id: number }) =>
      api<VendorLocationDetail>(`/vendor-locations/${enc(id)}/link-osm`, { method: "POST", body: input }),
    onSuccess: () => {
      invalidateLocations(client);
      void client.invalidateQueries({ queryKey: geoKeys.vendors });
    },
  });
}

export function useUnlinkOsm(id: string) {
  const client = useQueryClient();
  return useMutation({
    mutationFn: () => api<VendorLocationDetail>(`/vendor-locations/${enc(id)}/unlink-osm`, { method: "POST" }),
    onSuccess: () => invalidateLocations(client),
  });
}

// --- vendor files (1F) --------------------------------------------------------

export type ExportMode = "public" | "household";
export type ExportFormat = "yaml" | "json";

/** Where a kitchen-erp-vendors/1 file downloads from; the session cookie goes with it. */
export function exportUrl(mode: ExportMode, format: ExportFormat): string {
  return `/api/v1/vendors/export${qs({ format, mode })}`;
}

/** What a public export would hold, shown before downloading (design D12). */
export function useExportSummary(enabled: boolean) {
  return useQuery({
    queryKey: geoKeys.exportSummary,
    queryFn: () => api<{ locations: number; public: number }>("/vendors/export-summary"),
    enabled,
  });
}

/** Whether a location is in a public export, and the plain-words reason when that is fixed. */
export function shareState(location: VendorLocation, vendorKind: VendorKind): { shared: boolean; fixed: string | null } {
  if (vendorKind === "stand") return { shared: false, fixed: "Stands are never shared" };
  if (!location.active) return { shared: false, fixed: "An inactive location is never shared" };
  if (location.osm_id !== null) return { shared: true, fixed: "Shared because it's linked to OpenStreetMap" };
  return { shared: location.publishable, fixed: null };
}

// --- opening hours ----------------------------------------------------------

export interface OpeningHoursValidation {
  valid: boolean;
  error: string | null;
}

export function validateOpeningHours(text: string): Promise<OpeningHoursValidation> {
  return api<OpeningHoursValidation>("/opening-hours/validate", { method: "POST", body: { text } });
}

// --- OpenStreetMap ----------------------------------------------------------

export function useOsmCandidates(homeBaseId: string, radiusM: number, enabled: boolean) {
  return useQuery({
    queryKey: geoKeys.osmCandidates(homeBaseId, radiusM),
    queryFn: () =>
      api<ListResponse<OsmCandidate>>(`/osm/candidates${qs({ home_base_id: homeBaseId, radius_m: radiusM })}`),
    select: (data) => data.items,
    enabled: enabled && Boolean(homeBaseId) && radiusM >= 100,
    retry: false,
    staleTime: 5 * 60_000,
  });
}

export function useAdoptOsm() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (input: OsmAdoptInput) =>
      api<VendorLocationDetail>("/osm/adopt", { method: "POST", body: input, headers: idem() }),
    onSuccess: (_created, input) => {
      invalidateLocations(client);
      void client.invalidateQueries({ queryKey: geoKeys.vendors });
      void client.invalidateQueries({ queryKey: geoKeys.osmCandidates(input.home_base_id, input.radius_m) });
    },
  });
}

// --- formatting -------------------------------------------------------------

/** Latitude and longitude as "lat, lon", for display only. */
// --- places in the map extract (#62) -----------------------------------------

export interface MapPlace {
  name: string;
  kind: "town" | "neighbourhood" | "poi";
  detail: string | null;
  lat: string;
  lon: string;
  distance_m: number | null;
}

/**
 * Places by name from the deployment's own map extract: towns anywhere in it,
 * and neighbourhoods and shops near `near` (the map's centre). Nothing is sent
 * anywhere but this deployment's API.
 */
export function useMapPlaces(q: string, near: { lat: string; lon: string } | null) {
  const trimmed = q.trim();
  return useQuery({
    queryKey: geoKeys.mapPlaces(trimmed, near),
    queryFn: () => api<ListResponse<MapPlace>>(`/map/places${qs({ q: trimmed, lat: near?.lat, lon: near?.lon })}`),
    select: (data) => data.items,
    enabled: trimmed.length >= 2,
    placeholderData: (previous) => previous,
    staleTime: 5 * 60_000,
  });
}

export function formatDistance(metres: number | null): string | null {
  if (metres === null) return null;
  return metres < 1000 ? `${Math.round(metres / 10) * 10} m` : `${(metres / 1000).toFixed(1)} km`;
}

export function formatLatLon(lat: string, lon: string): string {
  return `${lat}, ${lon}`;
}
