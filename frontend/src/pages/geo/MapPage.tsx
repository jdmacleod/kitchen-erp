import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import type { IngredientSummary } from "../../api/catalog";
import { errorMessage } from "../../api/client";
import { formatAge, useCheapest, useLocationPricePanel, type PriceFilters as Filters } from "../../api/pricebook";
import {
  VENDOR_KINDS,
  formatLatLon,
  geoErrorMessage,
  useCreateHomeBase,
  useCreateLocation,
  useHomeBases,
  useIsOpen,
  useLocation,
  useLocations,
  vendorKindLabel,
  type HomeBase,
  type LocationCreateInput,
  type VendorKind,
  type VendorLocation,
} from "../../api/geo";
import { Badge, Disclosure, SelectField } from "../../components/catalog/fields";
import { IngredientPicker } from "../../components/catalog/IngredientPicker";
import { CoordinatesField } from "../../components/geo/CoordinatesField";
import { LazyMapView as MapView } from "../../components/geo/LazyMapView";
import type { MapPin } from "../../components/geo/MapView";
import { OpeningHoursInput } from "../../components/geo/OpeningHoursInput";
import { VendorPicker, type VendorChoice } from "../../components/geo/VendorPicker";
import { PriceAge, PromoBadge, formatUnitPrice } from "../../components/pricebook/PriceAge";
import { Alert, Button, Field, PageHeader, focusRing } from "../../components/ui";
import { formatMoney, stripZeros } from "../../lib/decimal";
import { formatDateTime } from "../../lib/format";
import { parseLatLon } from "../../lib/latlon";
import { describeOpeningHours, fromDateTimeLocal, toDateTimeLocal } from "../../lib/openingHours";
import { usePageTitle } from "../../lib/usePageTitle";

type Mode = "browse" | "location" | "home";
type Selection = { type: "location"; id: string } | { type: "home"; id: string };
type Point = { lat: string; lon: string };

const HOME_PREFIX = "home:";

export function MapPage() {
  usePageTitle("Map");
  const [params, setParams] = useSearchParams();
  const homeBases = useHomeBases();

  // Filters. "Open at" defaults to now but only filters once switched on:
  // the server hides every location with unknown hours when open_at is sent.
  const [kind, setKind] = useState<VendorKind | "">("");
  const [homeBaseId, setHomeBaseId] = useState("");
  const [openAtOn, setOpenAtOn] = useState(false);
  const [openAtLocal, setOpenAtLocal] = useState(() => toDateTimeLocal(new Date()));
  const openAtIso = openAtOn ? (fromDateTimeLocal(openAtLocal) ?? undefined) : undefined;
  const locations = useLocations({ kind, home_base_id: homeBaseId, open_at: openAtIso });
  // The query above is filtered, so an empty result means "nothing matches these
  // filters", not "this household has nowhere to shop". Only the unfiltered case
  // can say the latter, and saying it wrongly would tell an established household
  // to go and create a location it already has.
  const filtered = kind !== "" || homeBaseId !== "" || openAtOn;

  const [tilesPresent, setTilesPresent] = useState<boolean | null>(null);
  // `?place=location` opens ready to drop a pin. MapView ignores map clicks
  // unless it is placing, so anything that sends a newcomer here to add their
  // first location has to turn placing on for them or the first click does
  // nothing and the instruction reads as broken.
  const [mode, setMode] = useState<Mode>(() => (params.get("place") === "location" ? "location" : "browse"));
  const [draft, setDraft] = useState<Point | null>(null);
  // A point chosen away from the map needs the view brought to it; a point that
  // came from a click is already in front of the person who clicked.
  const [centerOn, setCenterOn] = useState<Point | null>(null);
  const [selected, setSelected] = useState<Selection | null>(() => {
    const id = params.get("location");
    return id ? { type: "location", id } : null;
  });
  // ?center=lat,lon&zoom=12 opens the map at a place instead of fitting the pins.
  const [initialView] = useState(() => {
    const center = params.get("center")?.split(",") ?? [];
    if (center.length !== 2 || !/^-?\d+(\.\d+)?$/.test(center[0]) || !/^-?\d+(\.\d+)?$/.test(center[1])) return null;
    const zoom = Number(params.get("zoom") ?? "12");
    return { lat: center[0], lon: center[1], zoom: Number.isFinite(zoom) ? zoom : 12 };
  });

  const items = useMemo(() => locations.data ?? [], [locations.data]);
  // With the open-at filter on, the list is the set of locations open then;
  // stalls in a market's panel are narrowed to it too.
  const visibleIds = useMemo(() => (openAtOn ? new Set(items.map((l) => l.id)) : null), [openAtOn, items]);

  // "Where is this cheapest": one ingredient, and each location's pin is
  // labelled with its best normalized unit price. A chain-scoped vendor's
  // price is repeated at each of its locations by the server.
  const [cheapestOf, setCheapestOf] = useState<IngredientSummary | null>(null);
  const [cheapestFilters, setCheapestFilters] = useState<Filters>({ min_quality: "", exclude_stale: false });
  const cheapest = useCheapest(cheapestOf?.id, cheapestFilters);
  const priceByLocation = useMemo(() => {
    const out = new Map<string, { label: string; stale: boolean }>();
    if (!cheapestOf) return out;
    for (const item of cheapest.data?.items ?? []) {
      // Every price carries its age, even on a pin.
      out.set(item.location_id, { label: `${formatUnitPrice(item.norm_unit_price, item.norm_unit)} · ${formatAge(null, item.observed_at)}`, stale: item.stale });
    }
    return out;
  }, [cheapestOf, cheapest.data]);

  const pins = useMemo<MapPin[]>(() => {
    const out: MapPin[] = [];
    for (const l of items) {
      // Stalls share their market's pin; they are reached through the market.
      if (l.parent_location_id) continue;
      const price = priceByLocation.get(l.id);
      out.push({
        id: l.id,
        lat: l.lat,
        lon: l.lon,
        kind: l.vendor.kind,
        label: price ? `${l.name} (${vendorKindLabel[l.vendor.kind]}), ${price.label}${price.stale ? ", stale" : ""}` : `${l.name} (${vendorKindLabel[l.vendor.kind]})`,
        inactive: !l.active,
        priceLabel: price?.label,
        stale: price?.stale,
      });
    }
    for (const h of homeBases.data ?? []) {
      out.push({ id: `${HOME_PREFIX}${h.id}`, lat: h.lat, lon: h.lon, kind: "home", label: `${h.name} (home base)` });
    }
    return out;
  }, [items, homeBases.data, priceByLocation]);

  const selectedPinId = selected ? (selected.type === "home" ? `${HOME_PREFIX}${selected.id}` : selected.id) : null;

  // `place` is an instruction, not state. Strip it once it has been applied, or
  // the URL keeps claiming placing mode after the user leaves it, and a later
  // visit to this same URL would silently re-enter it.
  useEffect(() => {
    if (!params.has("place")) return;
    const next = new URLSearchParams(params);
    next.delete("place");
    setParams(next, { replace: true });
  }, [params, setParams]);

  const startPlacing = (next: Mode) => {
    setMode(next);
    setDraft(null);
    setCenterOn(null);
    setSelected(null);
  };
  const stopPlacing = () => {
    setMode("browse");
    setDraft(null);
    setCenterOn(null);
  };
  /** A point typed, pasted, or read off this device rather than clicked. */
  const placeAt = (point: Point) => {
    setDraft(point);
    setCenterOn(point);
  };

  /**
   * Select a location and bring the map to it.
   *
   * For the ways of choosing a place that are not a click on its pin: the list
   * beside the map, and the `?location=` link that every location row on a
   * vendor page points at. Those used to open the detail panel and leave the
   * map wherever it was, so "show me this shop" answered with a panel about one
   * place and a map showing another, with no tiles and several hundred pins to
   * navigate by. A pin the user just clicked is already in front of them, so
   * that path stays put.
   */
  const pointOf = (sel: Selection): Point | null => {
    const found =
      sel.type === "home"
        ? (homeBases.data ?? []).find((h) => h.id === sel.id)
        : items.find((l) => l.id === sel.id);
    return found ? { lat: found.lat, lon: found.lon } : null;
  };
  const selectAndCenter = (sel: Selection) => {
    setSelected(sel);
    const point = pointOf(sel);
    if (point) setCenterOn(point);
  };

  // The `?location=` link arrives before the locations have loaded, so the
  // centring waits for them and then happens once. A later selection goes
  // through selectAndCenter instead.
  const pendingDeepLink = useRef<string | null>(params.get("location"));
  useEffect(() => {
    const id = pendingDeepLink.current;
    if (id === null || items.length === 0) return;
    pendingDeepLink.current = null;
    const target = items.find((l) => l.id === id);
    if (target) setCenterOn({ lat: target.lat, lon: target.lon });
  }, [items]);

  return (
    <div className="flex flex-col gap-3">
      <PageHeader title="Map">
        <div className="flex flex-wrap gap-2">
          <Button variant={mode === "location" ? "primary" : "secondary"} aria-pressed={mode === "location"} onClick={() => (mode === "location" ? stopPlacing() : startPlacing("location"))}>
            Add location here
          </Button>
          <Button variant={mode === "home" ? "primary" : "secondary"} aria-pressed={mode === "home"} onClick={() => (mode === "home" ? stopPlacing() : startPlacing("home"))}>
            Add home base here
          </Button>
        </div>
      </PageHeader>

      <Filters
        kind={kind}
        onKind={setKind}
        homeBaseId={homeBaseId}
        onHomeBase={setHomeBaseId}
        homeBases={homeBases.data ?? []}
        openAtOn={openAtOn}
        onOpenAtOn={setOpenAtOn}
        openAtLocal={openAtLocal}
        onOpenAtLocal={setOpenAtLocal}
      />

      <CheapestControl
        ingredient={cheapestOf}
        onIngredient={setCheapestOf}
        filters={cheapestFilters}
        onFilters={setCheapestFilters}
        unit={cheapest.data?.unit ?? cheapestOf?.canonical_unit ?? null}
        count={cheapest.data?.items.length ?? 0}
        loading={cheapest.isFetching}
        error={cheapest.error}
      />

      {locations.isSuccess && !filtered && items.length === 0 ? (
        // Arriving from a blocked entry form, placing mode is already on and the
        // line below already says where to click, so repeating "use Add location
        // here" would name a button the visitor never had to press.
        <Alert tone="info">
          {mode === "browse"
            ? "No locations yet. Use “Add location here”, then click the map where you shop, or give its coordinates. "
            : "No locations yet. "}
          Naming the pin creates the vendor and the location together, so this is one step, not
          two.
        </Alert>
      ) : null}
      {/* Independent of whether any location exists: whether tiles are installed
          is a fact about the deployment, and folding it into the notice above
          made this sentence disappear on a database that happened to be empty. */}
      {tilesPresent === false ? (
        <Alert tone="info">
          Map tiles are missing; see docs/tiles.md. Pins are still placed at their coordinates on a plain background, and
          a new pin can be given its coordinates instead of being aimed at one.
        </Alert>
      ) : null}
      {locations.isError ? <Alert tone="error">{errorMessage(locations.error)}</Alert> : null}
      {mode !== "browse" && !draft ? (
        <p role="status" className="text-sm text-neutral-700 dark:text-neutral-300">
          Click or tap the map where the {mode === "home" ? "home base" : "location"} is, or give its
          coordinates on the right. You can drag the pin afterwards.
        </p>
      ) : null}

      <div className="relative h-[70dvh] min-h-[360px] overflow-hidden rounded-lg border border-neutral-200 md:flex dark:border-neutral-800">
        <MapView
          label="Vendor locations and home bases"
          className="h-full min-w-0 flex-1"
          pins={pins}
          selectedId={selectedPinId}
          placing={mode !== "browse"}
          draft={draft}
          onTilesStatus={setTilesPresent}
          initialView={initialView}
          centerOn={centerOn}
          onMapClick={(lat, lon) => {
            setDraft({ lat, lon });
            setCenterOn(null);
          }}
          onPinSelect={(id) => {
            if (mode !== "browse") return;
            setSelected(id.startsWith(HOME_PREFIX) ? { type: "home", id: id.slice(HOME_PREFIX.length) } : { type: "location", id });
          }}
        />
        <aside
          aria-label="Details"
          className="absolute inset-x-0 bottom-0 z-10 max-h-[45%] overflow-y-auto border-t border-neutral-200 bg-white p-3 text-sm md:static md:max-h-none md:w-80 md:shrink-0 md:border-t-0 md:border-l dark:border-neutral-800 dark:bg-neutral-900"
        >
          {mode === "location" ? (
            <LocationDraftForm
              draft={draft}
              onPoint={placeAt}
              onCancel={stopPlacing}
              onCreated={(location) => {
                stopPlacing();
                setSelected({ type: "location", id: location.id });
              }}
            />
          ) : mode === "home" ? (
            <HomeBaseDraftForm
              draft={draft}
              onPoint={placeAt}
              onCancel={stopPlacing}
              onCreated={(home) => {
                stopPlacing();
                setSelected({ type: "home", id: home.id });
              }}
            />
          ) : selected?.type === "location" ? (
            <LocationPanel id={selected.id} openAtIso={openAtIso} visibleIds={visibleIds} onClose={() => setSelected(null)} />
          ) : selected?.type === "home" ? (
            <HomeBasePanel id={selected.id} homeBases={homeBases.data ?? []} locations={items} onClose={() => setSelected(null)} />
          ) : (
            <LocationList items={items} loading={locations.isPending} onSelect={(id) => selectAndCenter({ type: "location", id })} />
          )}
        </aside>
      </div>
    </div>
  );
}

// --- filters ----------------------------------------------------------------

interface FiltersProps {
  kind: VendorKind | "";
  onKind: (k: VendorKind | "") => void;
  homeBaseId: string;
  onHomeBase: (id: string) => void;
  homeBases: HomeBase[];
  openAtOn: boolean;
  onOpenAtOn: (on: boolean) => void;
  openAtLocal: string;
  onOpenAtLocal: (v: string) => void;
}

function Filters({ kind, onKind, homeBaseId, onHomeBase, homeBases, openAtOn, onOpenAtOn, openAtLocal, onOpenAtLocal }: FiltersProps) {
  return (
    <form aria-label="Map filters" className="grid gap-3 sm:grid-cols-3" onSubmit={(e) => e.preventDefault()}>
      <SelectField id="filter-kind" label="Kind" value={kind} onChange={(e) => onKind(e.target.value as VendorKind | "")}>
        <option value="">All kinds</option>
        {VENDOR_KINDS.map((k) => (
          <option key={k} value={k}>
            {vendorKindLabel[k]}
          </option>
        ))}
      </SelectField>
      <SelectField id="filter-home-base" label="Home base" value={homeBaseId} onChange={(e) => onHomeBase(e.target.value)}>
        <option value="">Any home base</option>
        {homeBases.map((h) => (
          <option key={h.id} value={h.id}>
            {h.name}
          </option>
        ))}
      </SelectField>
      <div className="flex flex-col gap-1">
        <label className="inline-flex min-h-6 items-center gap-2 text-sm font-medium">
          <input type="checkbox" checked={openAtOn} onChange={(e) => onOpenAtOn(e.target.checked)} className={`size-4 ${focusRing}`} />
          Only open at
        </label>
        <div className="flex gap-2">
          <input
            id="filter-open-at"
            aria-label="Open at"
            type="datetime-local"
            value={openAtLocal}
            onChange={(e) => onOpenAtLocal(e.target.value)}
            className={`min-h-10 min-w-0 flex-1 rounded-md border border-neutral-300 bg-white px-2 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-900 ${focusRing}`}
          />
          <Button variant="secondary" className="min-h-10 px-2" onClick={() => onOpenAtLocal(toDateTimeLocal(new Date()))}>
            Now
          </Button>
        </div>
      </div>
    </form>
  );
}

interface CheapestControlProps {
  ingredient: IngredientSummary | null;
  onIngredient: (i: IngredientSummary | null) => void;
  filters: Filters;
  onFilters: (f: Filters) => void;
  unit: string | null;
  count: number;
  loading: boolean;
  error: unknown;
}

/** Choose an ingredient and each pin is labelled with its best price per canonical unit. */
function CheapestControl({ ingredient, onIngredient, filters, onFilters, unit, count, loading, error }: CheapestControlProps) {
  return (
    <form aria-label="Where is this cheapest" className="flex flex-col gap-2 rounded-lg border border-neutral-200 bg-white p-3 dark:border-neutral-800 dark:bg-neutral-900" onSubmit={(e) => e.preventDefault()}>
      <h2 className="text-sm font-medium">Where is this cheapest?</h2>
      <div className="grid gap-3 sm:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_auto]">
        <IngredientPicker
          id="cheapest-ingredient"
          label="Ingredient"
          allowCreate={false}
          value={ingredient ? { kind: "existing", ingredient } : null}
          onChange={(choice) => onIngredient(choice?.kind === "existing" ? choice.ingredient : null)}
        />
        <SelectField id="cheapest-min-quality" label="Minimum quality" value={filters.min_quality ?? ""} onChange={(e) => onFilters({ ...filters, min_quality: e.target.value ? Number(e.target.value) : "" })}>
          <option value="">Any</option>
          {[1, 2, 3, 4, 5].map((q) => (
            <option key={q} value={q}>
              {q}/5 or better
            </option>
          ))}
        </SelectField>
        <label className="inline-flex min-h-10 items-center gap-2 self-end text-sm">
          <input type="checkbox" checked={Boolean(filters.exclude_stale)} onChange={(e) => onFilters({ ...filters, exclude_stale: e.target.checked })} className={`size-4 ${focusRing}`} />
          Exclude stale
        </label>
      </div>
      {ingredient ? (
        error ? (
          <Alert tone="error">{errorMessage(error)}</Alert>
        ) : (
          <p role="status" className="text-xs text-neutral-600 dark:text-neutral-400" data-testid="cheapest-summary">
            {loading && count === 0
              ? "Looking up prices…"
              : count === 0
                ? `No normalized price for ${ingredient.name} at any location.`
                : `Pins show the best price per ${unit ?? ingredient.canonical_unit} for ${ingredient.name} at ${count} ${count === 1 ? "location" : "locations"}; stale prices say so.`}
          </p>
        )
      ) : null}
    </form>
  );
}

// --- panels -----------------------------------------------------------------

function OpenBadge({ isOpen }: { isOpen: boolean | null | undefined }) {
  if (isOpen === null || isOpen === undefined) return <Badge>hours unknown</Badge>;
  return isOpen ? <Badge tone="good">open</Badge> : <Badge tone="warn">closed</Badge>;
}

function LocationList({ items, loading, onSelect }: { items: VendorLocation[]; loading: boolean; onSelect: (id: string) => void }) {
  const top = items.filter((l) => !l.parent_location_id);
  return (
    <div>
      <h2 className="mb-2 text-base font-medium">Locations</h2>
      {loading ? (
        <p role="status" className="text-neutral-600 dark:text-neutral-400">
          Loading…
        </p>
      ) : top.length === 0 ? (
        <p className="text-neutral-600 dark:text-neutral-400">No locations match. Use “Add location here” to drop the first pin.</p>
      ) : (
        <ul aria-label="Locations on the map" className="divide-y divide-neutral-200 dark:divide-neutral-800">
          {top.map((l) => (
            <li key={l.id}>
              <button type="button" onClick={() => onSelect(l.id)} className={`flex w-full items-center justify-between gap-2 rounded-md px-1 py-2 text-left hover:bg-neutral-100 dark:hover:bg-neutral-800 ${focusRing}`}>
                <span className="min-w-0 truncate">
                  <span className="font-medium">{l.name}</span>
                  <span className="text-neutral-600 dark:text-neutral-400"> · {vendorKindLabel[l.vendor.kind]}</span>
                </span>
                {l.is_open !== null ? <OpenBadge isOpen={l.is_open} /> : null}
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function PanelHeader({ title, onClose, children }: { title: string; onClose: () => void; children?: React.ReactNode }) {
  return (
    <div className="mb-2 flex items-start justify-between gap-2">
      <div className="min-w-0">
        <h2 className="text-base font-medium">{title}</h2>
        {children}
      </div>
      <Button variant="ghost" className="min-h-8 px-2" onClick={onClose} aria-label="Close details">
        ×
      </Button>
    </div>
  );
}

function LocationPanel({ id, openAtIso, visibleIds, onClose }: { id: string; openAtIso: string | undefined; visibleIds: Set<string> | null; onClose: () => void }) {
  const detail = useLocation(id);
  const [stallId, setStallId] = useState<string | null>(null);
  // "Now" is fixed while the panel is open so the query key stays stable.
  const nowIso = useMemo(() => new Date().toISOString(), []);
  const at = openAtIso ?? nowIso;
  const open = useIsOpen(id, at);

  if (detail.isPending) {
    return (
      <p role="status" className="text-neutral-600 dark:text-neutral-400">
        Loading…
      </p>
    );
  }
  if (detail.isError) return <Alert tone="error">{errorMessage(detail.error)}</Alert>;
  const l = detail.data;
  const stalls = visibleIds ? l.stalls.filter((s) => visibleIds.has(s.id)) : l.stalls;
  const stall = stallId ? l.stalls.find((s) => s.id === stallId) : null;
  if (stall) {
    return <StallPanel stall={stall} market={l} at={at} onBack={() => setStallId(null)} onClose={onClose} />;
  }
  return (
    <div data-testid="location-panel">
      <PanelHeader title={l.name} onClose={onClose}>
        <p className="text-neutral-600 dark:text-neutral-400">
          <Link to={`/vendors/${l.vendor.id}`} className={`rounded underline ${focusRing}`}>
            {l.vendor.name}
          </Link>{" "}
          · {vendorKindLabel[l.vendor.kind]}
          {l.active ? null : (
            <>
              {" "}
              <Badge tone="warn">inactive</Badge>
            </>
          )}
        </p>
      </PanelHeader>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-neutral-600 dark:text-neutral-400">Hours</dt>
        <dd>
          {describeOpeningHours(l.effective_opening_hours)}
          {l.opening_hours_inherited ? (
            <>
              {" "}
              <Badge>inherited</Badge>
            </>
          ) : null}
        </dd>
        <dt className="text-neutral-600 dark:text-neutral-400">At {new Date(at).toLocaleString()}</dt>
        <dd>
          <OpenBadge isOpen={open.data ? open.data.is_open : undefined} />
        </dd>
        {l.address ? (
          <>
            <dt className="text-neutral-600 dark:text-neutral-400">Address</dt>
            <dd>{l.address}</dd>
          </>
        ) : null}
        <dt className="text-neutral-600 dark:text-neutral-400">Position</dt>
        <dd className="font-mono text-xs">{formatLatLon(l.lat, l.lon)}</dd>
        {l.osm_id ? (
          <>
            <dt className="text-neutral-600 dark:text-neutral-400">Source</dt>
            <dd>
              OpenStreetMap {l.osm_type} {l.osm_id}
            </dd>
          </>
        ) : null}
      </dl>
      <LocationPrices id={l.id} />
      {l.vendor.kind === "market" || l.stalls.length > 0 ? (
        <div className="mt-3">
          <h3 className="text-sm font-medium">Stalls</h3>
          {stalls.length === 0 ? (
            <p className="text-neutral-600 dark:text-neutral-400">{l.stalls.length === 0 ? "No stalls yet." : "No stalls open at the chosen time."}</p>
          ) : (
            <ul aria-label="Stalls" className="mt-1 divide-y divide-neutral-200 dark:divide-neutral-800">
              {stalls.map((s) => (
                <li key={s.id}>
                  <button type="button" onClick={() => setStallId(s.id)} className={`flex w-full items-center justify-between gap-2 rounded-md px-1 py-2 text-left hover:bg-neutral-100 dark:hover:bg-neutral-800 ${focusRing}`}>
                    <span className="font-medium">{s.name}</span>
                    <span className="text-xs text-neutral-600 dark:text-neutral-400">{s.opening_hours_inherited ? "inherits hours" : "own hours"}</span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
    </div>
  );
}

const PERIODS = [7, 30, 90, 365] as const;

/** Last visit, spend over a chosen period, and the most recent prices seen at a location. */
function LocationPrices({ id }: { id: string }) {
  const [days, setDays] = useState<number>(30);
  const panel = useLocationPricePanel(id, days);
  return (
    <section aria-label="Prices here" className="mt-3 border-t border-neutral-200 pt-3 dark:border-neutral-800" data-testid="location-prices">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium">Prices here</h3>
        <label className="inline-flex items-center gap-1 text-xs">
          Over
          <select aria-label="Spend period" value={days} onChange={(e) => setDays(Number(e.target.value))} className={`min-h-8 rounded-md border border-neutral-300 bg-white px-1 text-xs dark:border-neutral-700 dark:bg-neutral-900 ${focusRing}`}>
            {PERIODS.map((d) => (
              <option key={d} value={d}>
                {d} days
              </option>
            ))}
          </select>
        </label>
      </div>
      {panel.isPending ? (
        <p role="status" className="text-neutral-600 dark:text-neutral-400">
          Loading…
        </p>
      ) : panel.isError ? (
        <Alert tone="error">{errorMessage(panel.error)}</Alert>
      ) : (
        <>
          <dl className="mt-1 grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            <dt className="text-neutral-600 dark:text-neutral-400">Last visit</dt>
            <dd>{panel.data.last_visit ? formatDateTime(panel.data.last_visit) : "never"}</dd>
            <dt className="text-neutral-600 dark:text-neutral-400">Spend</dt>
            <dd className="tabular-nums">
              {formatMoney(panel.data.spend)} over {panel.data.visits} {panel.data.visits === 1 ? "visit" : "visits"} in {panel.data.period_days} days
            </dd>
          </dl>
          {panel.data.recent.length === 0 ? (
            <p className="mt-1 text-neutral-600 dark:text-neutral-400">No prices seen here yet.</p>
          ) : (
            <ul aria-label="Recent prices" className="mt-1 divide-y divide-neutral-200 dark:divide-neutral-800">
              {panel.data.recent.map((r) => (
                <li key={r.observation_id} className="flex flex-wrap items-baseline justify-between gap-x-2 py-1">
                  <Link to={`/products/${r.product_id}`} className={`min-w-0 truncate rounded underline-offset-2 hover:underline ${focusRing}`}>
                    {r.brand ? `${r.brand} ${r.product_name}` : r.product_name}
                  </Link>
                  <span className="text-xs whitespace-nowrap tabular-nums">
                    {formatMoney(r.price)} / {stripZeros(r.qty)} {r.unit} <PromoBadge promo={r.is_promo} />
                    {r.norm_unit_price ? <span className="text-neutral-600 dark:text-neutral-400"> · {formatUnitPrice(r.norm_unit_price, r.norm_unit)}</span> : null}
                    {" · "}
                    <PriceAge observedAt={r.observed_at} />
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </section>
  );
}

function StallPanel({ stall, market, at, onBack, onClose }: { stall: VendorLocation; market: VendorLocation; at: string; onBack: () => void; onClose: () => void }) {
  const open = useIsOpen(stall.id, at);
  return (
    <div data-testid="stall-panel">
      <PanelHeader title={stall.name} onClose={onClose}>
        <p className="text-neutral-600 dark:text-neutral-400">
          Stall at{" "}
          <button type="button" onClick={onBack} className={`rounded underline ${focusRing}`}>
            {market.name}
          </button>
        </p>
      </PanelHeader>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-neutral-600 dark:text-neutral-400">Hours</dt>
        <dd>
          {describeOpeningHours(stall.effective_opening_hours)}{" "}
          {stall.opening_hours_inherited ? <Badge>inherited from the market</Badge> : <Badge tone="good">own hours</Badge>}
        </dd>
        <dt className="text-neutral-600 dark:text-neutral-400">At {new Date(at).toLocaleString()}</dt>
        <dd>
          <OpenBadge isOpen={open.data ? open.data.is_open : undefined} />
        </dd>
        <dt className="text-neutral-600 dark:text-neutral-400">Vendor</dt>
        <dd>
          <Link to={`/vendors/${stall.vendor.id}`} className={`rounded underline ${focusRing}`}>
            {stall.vendor.name}
          </Link>
        </dd>
      </dl>
      <Button variant="secondary" className="mt-3" onClick={onBack}>
        Back to {market.name}
      </Button>
    </div>
  );
}

function HomeBasePanel({ id, homeBases, locations, onClose }: { id: string; homeBases: HomeBase[]; locations: VendorLocation[]; onClose: () => void }) {
  const home = homeBases.find((h) => h.id === id);
  if (!home) return null;
  const count = locations.filter((l) => l.home_base_id === id).length;
  return (
    <div data-testid="home-base-panel">
      <PanelHeader title={home.name} onClose={onClose}>
        <p className="text-neutral-600 dark:text-neutral-400">Home base{home.label ? ` · ${home.label}` : ""}</p>
      </PanelHeader>
      <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
        <dt className="text-neutral-600 dark:text-neutral-400">Position</dt>
        <dd className="font-mono text-xs">{formatLatLon(home.lat, home.lon)}</dd>
        <dt className="text-neutral-600 dark:text-neutral-400">Locations</dt>
        <dd>{count} shown default to this base</dd>
      </dl>
      <Link to="/settings/home-bases" className={`mt-3 inline-block rounded underline ${focusRing}`}>
        Manage home bases
      </Link>
    </div>
  );
}

// --- creation forms ---------------------------------------------------------

/**
 * Where the draft pin is, and the two ways to say so that are not a click.
 *
 * On a fresh deployment the map has no tiles and no pins, so the only way to
 * place the first location was to click a featureless grey field and hope
 * (issue #18). Those coordinates then drive nearest-store defaulting, the
 * distance labels and the cheapest-nearby layer for the life of the deployment,
 * which is a lot to rest on a guess.
 *
 * The empty map still opens on open ocean: that default is deliberate, so that
 * nothing about the household is implied before the first pin exists. Reading
 * this device's position is a button someone presses, not something the page
 * does on arrival, so the default is untouched.
 */
function DraftPointControl({ draft, onPoint, disabled }: { draft: Point | null; onPoint: (point: Point) => void; disabled?: boolean }) {
  const [text, setText] = useState(() => (draft ? formatLatLon(draft.lat, draft.lon) : ""));

  // A click on the map is the other direction of the same state, so it has to
  // reach the field. Adjusted during render rather than in an effect: React
  // restarts the render with the new value before anything is shown, where an
  // effect would paint the stale text first and then cascade a second render.
  // Compared by value, so typing "33.5,-120" is not rewritten under the cursor
  // by the point it just produced.
  const draftText = draft ? formatLatLon(draft.lat, draft.lon) : "";
  const [mirrored, setMirrored] = useState(draftText);
  if (draftText !== mirrored) {
    setMirrored(draftText);
    const shown = parseLatLon(text);
    if (!draft || !shown || shown.lat !== draft.lat || shown.lon !== draft.lon) {
      setText(draftText);
    }
  }

  const onText = (value: string) => {
    setText(value);
    const point = parseLatLon(value);
    if (point) onPoint(point);
  };

  return (
    <div className="flex flex-col gap-2">
      <p className="font-mono text-xs text-neutral-600 dark:text-neutral-400" data-testid="draft-point">
        {draft ? formatLatLon(draft.lat, draft.lon) : "No pin yet — click the map, or give the coordinates below."}
      </p>
      <CoordinatesField id="draft-coordinates" value={text} onChange={onText} disabled={disabled} />
    </div>
  );
}

function LocationDraftForm({ draft, onPoint, onCancel, onCreated }: { draft: Point | null; onPoint: (point: Point) => void; onCancel: () => void; onCreated: (l: VendorLocation) => void }) {
  const create = useCreateLocation();
  const [vendor, setVendor] = useState<VendorChoice | null>(null);
  const [name, setName] = useState("");
  const [hours, setHours] = useState("");
  const [hoursValid, setHoursValid] = useState(true);
  const [invalid, setInvalid] = useState<string | null>(null);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!draft) {
      setInvalid("Click the map, or give the coordinates, so the pin has a place.");
      return;
    }
    if (!vendor) {
      setInvalid("Choose a vendor, or create one by name.");
      return;
    }
    if (!name.trim()) {
      setInvalid("A name is required.");
      return;
    }
    if (hours.trim() && !hoursValid) {
      setInvalid("Fix the opening hours first.");
      return;
    }
    setInvalid(null);
    const input: LocationCreateInput = { name: name.trim(), lat: draft.lat, lon: draft.lon };
    if (vendor.kind === "existing") input.vendor_id = vendor.vendor.id;
    else input.vendor = { name: vendor.name, kind: vendor.vendorKind };
    if (hours.trim()) input.opening_hours = hours.trim();
    create.mutate(input, { onSuccess: onCreated });
  };

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-3" aria-label="Add location here" noValidate>
      <div className="flex items-start justify-between gap-2">
        <h2 className="text-base font-medium">New location</h2>
        <Button variant="ghost" className="min-h-8 px-2" onClick={onCancel}>
          Cancel
        </Button>
      </div>
      <DraftPointControl draft={draft} onPoint={onPoint} disabled={create.isPending} />
      {invalid ? <Alert tone="error">{invalid}</Alert> : null}
      {create.isError ? <Alert tone="error">{geoErrorMessage(create.error)}</Alert> : null}
      <VendorPicker id="new-location-vendor" value={vendor} onChange={setVendor} disabled={create.isPending} />
      <Field id="new-location-name" label="Name" autoComplete="off" required value={name} onChange={(e) => setName(e.target.value)} hint="What you call this place, e.g. “Corner stand on the coast road”." />
      <Disclosure summary="Opening hours (optional)">
        <OpeningHoursInput idPrefix="new-location" value={hours} onChange={setHours} onValidated={setHoursValid} disabled={create.isPending} />
      </Disclosure>
      <div>
        <Button type="submit" disabled={create.isPending || !draft}>
          {create.isPending ? "Creating…" : "Create location"}
        </Button>
      </div>
    </form>
  );
}

function HomeBaseDraftForm({ draft, onPoint, onCancel, onCreated }: { draft: Point | null; onPoint: (point: Point) => void; onCancel: () => void; onCreated: (h: HomeBase) => void }) {
  const create = useCreateHomeBase();
  const [name, setName] = useState("");
  const [invalid, setInvalid] = useState<string | null>(null);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!draft) {
      setInvalid("Click the map, or give the coordinates, so the pin has a place.");
      return;
    }
    if (!name.trim()) {
      setInvalid("A name is required.");
      return;
    }
    setInvalid(null);
    create.mutate({ name: name.trim(), lat: draft.lat, lon: draft.lon }, { onSuccess: onCreated });
  };

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-3" aria-label="Add home base here" noValidate>
      <div className="flex items-start justify-between gap-2">
        <h2 className="text-base font-medium">New home base</h2>
        <Button variant="ghost" className="min-h-8 px-2" onClick={onCancel}>
          Cancel
        </Button>
      </div>
      <DraftPointControl draft={draft} onPoint={onPoint} disabled={create.isPending} />
      {invalid ? <Alert tone="error">{invalid}</Alert> : null}
      {create.isError ? <Alert tone="error">{geoErrorMessage(create.error)}</Alert> : null}
      <Field id="new-home-name" label="Name" autoComplete="off" required value={name} onChange={(e) => setName(e.target.value)} hint="e.g. “Home” or “The cabin”." />
      <div>
        <Button type="submit" disabled={create.isPending || !draft}>
          {create.isPending ? "Creating…" : "Create home base"}
        </Button>
      </div>
    </form>
  );
}
