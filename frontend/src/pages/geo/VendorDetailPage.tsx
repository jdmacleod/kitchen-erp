import { useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";
import { errorMessage, isApiError } from "../../api/client";
import {
  PRICE_SCOPES,
  VENDOR_KINDS,
  formatLatLon,
  geoErrorMessage,
  priceScopeLabel,
  useCreateLocation,
  useHomeBases,
  useLocations,
  useRefreshOsm,
  useSetLocationActive,
  useSetVendorActive,
  useUpdateLocation,
  useUpdateVendor,
  useVendor,
  vendorKindLabel,
  type LocationCreateInput,
  type LocationUpdateInput,
  type PriceScope,
  type Vendor,
  type VendorKind,
  type VendorLocation,
} from "../../api/geo";
import { Badge, Disclosure, RadioGroup, SelectField, TextAreaField } from "../../components/catalog/fields";
import { CoordinatesField } from "../../components/geo/CoordinatesField";
import { OpeningHoursInput } from "../../components/geo/OpeningHoursInput";
import { Alert, Button, Card, EmptyState, Field, PageHeader, focusRing, secondaryLinkClass } from "../../components/ui";
import { parseLatLon } from "../../lib/latlon";
import { describeOpeningHours } from "../../lib/openingHours";
import { usePageTitle } from "../../lib/usePageTitle";

export function VendorDetailPage() {
  const { id } = useParams<{ id: string }>();
  const vendor = useVendor(id);
  usePageTitle(vendor.data?.name ?? "Vendor");

  if (vendor.isPending) {
    return (
      <>
        <PageHeader title="Vendor" />
        <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
          Loading…
        </p>
      </>
    );
  }
  if (vendor.isError) {
    const missing = isApiError(vendor.error) && vendor.error.status === 404;
    return (
      <>
        <PageHeader title="Vendor" />
        {missing ? (
          <EmptyState title="No such vendor">
            <Link to="/catalog/vendors" className={`rounded-md underline ${focusRing}`}>
              Back to vendors
            </Link>
          </EmptyState>
        ) : (
          <Alert tone="error">{errorMessage(vendor.error)}</Alert>
        )}
      </>
    );
  }
  return <VendorDetail vendor={vendor.data} />;
}

function VendorDetail({ vendor }: { vendor: Vendor }) {
  const setActive = useSetVendorActive(vendor.id);
  const [editing, setEditing] = useState(false);
  return (
    <>
      <PageHeader title={vendor.name}>
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => setEditing((v) => !v)} aria-expanded={editing}>
            {editing ? "Close editor" : "Edit vendor"}
          </Button>
          <Button variant={vendor.active ? "danger" : "secondary"} disabled={setActive.isPending} onClick={() => setActive.mutate(!vendor.active)}>
            {setActive.isPending ? "Saving…" : vendor.active ? "Deactivate" : "Activate"}
          </Button>
        </div>
      </PageHeader>
      <div className="flex flex-col gap-6">
        <p className="flex flex-wrap items-center gap-2 text-sm text-neutral-700 dark:text-neutral-300">
          <Link to="/catalog/vendors" className={`rounded underline ${focusRing}`}>
            All vendors
          </Link>
          <span aria-hidden="true">·</span>
          <span>{vendorKindLabel[vendor.kind]}</span>
          <span aria-hidden="true">·</span>
          <span>{priceScopeLabel[vendor.price_scope]}</span>
          {vendor.website ? (
            <>
              <span aria-hidden="true">·</span>
              <a href={vendor.website} rel="noreferrer noopener" target="_blank" className={`rounded underline ${focusRing}`}>
                website
              </a>
            </>
          ) : null}
          {vendor.active ? <Badge tone="good">active</Badge> : <Badge tone="warn">inactive</Badge>}
        </p>
        {setActive.isError ? <Alert tone="error">{geoErrorMessage(setActive.error)}</Alert> : null}
        {vendor.notes ? <p className="text-sm whitespace-pre-wrap">{vendor.notes}</p> : null}
        {editing ? <EditVendorForm vendor={vendor} onDone={() => setEditing(false)} /> : null}
        <VendorLocations vendor={vendor} />
      </div>
    </>
  );
}

function EditVendorForm({ vendor, onDone }: { vendor: Vendor; onDone: () => void }) {
  const update = useUpdateVendor(vendor.id);
  const [form, setForm] = useState({
    name: vendor.name,
    kind: vendor.kind as VendorKind,
    price_scope: vendor.price_scope as PriceScope,
    website: vendor.website ?? "",
    notes: vendor.notes ?? "",
  });
  const [invalid, setInvalid] = useState<string | null>(null);
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => setForm((f) => ({ ...f, [key]: value }));

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!form.name.trim()) {
      setInvalid("A name is required.");
      return;
    }
    setInvalid(null);
    const input: Parameters<typeof update.mutate>[0] = {};
    if (form.name.trim() !== vendor.name) input.name = form.name.trim();
    if (form.kind !== vendor.kind) input.kind = form.kind;
    if (form.price_scope !== vendor.price_scope) input.price_scope = form.price_scope;
    if ((form.website.trim() || null) !== vendor.website) input.website = form.website.trim() || null;
    if ((form.notes.trim() || null) !== vendor.notes) input.notes = form.notes.trim() || null;
    if (Object.keys(input).length === 0) {
      onDone();
      return;
    }
    update.mutate(input, { onSuccess: onDone });
  };

  return (
    <Card>
      <form onSubmit={onSubmit} className="flex flex-col gap-4" aria-labelledby="edit-vendor-heading" noValidate>
        <h2 id="edit-vendor-heading" className="text-lg font-medium">
          Edit vendor
        </h2>
        {invalid ? <Alert tone="error">{invalid}</Alert> : null}
        {update.isError ? <Alert tone="error">{geoErrorMessage(update.error)}</Alert> : null}
        <Field id="edit-vendor-name" label="Name" autoComplete="off" required value={form.name} onChange={(e) => set("name", e.target.value)} />
        <RadioGroup name="edit-vendor-kind" legend="Kind" options={VENDOR_KINDS.map((k) => ({ value: k, label: vendorKindLabel[k] }))} value={form.kind} onChange={(v) => set("kind", v)} />
        <RadioGroup name="edit-vendor-scope" legend="Price scope" options={PRICE_SCOPES.map((s) => ({ value: s, label: priceScopeLabel[s] }))} value={form.price_scope} onChange={(v) => set("price_scope", v)} />
        <Field id="edit-vendor-website" label="Website" type="url" autoComplete="off" value={form.website} onChange={(e) => set("website", e.target.value)} />
        <TextAreaField id="edit-vendor-notes" label="Notes" value={form.notes} onChange={(e) => set("notes", e.target.value)} />
        <div className="flex flex-wrap gap-2">
          <Button type="submit" disabled={update.isPending}>
            {update.isPending ? "Saving…" : "Save vendor"}
          </Button>
          <Button variant="secondary" onClick={onDone}>
            Cancel
          </Button>
        </div>
      </form>
    </Card>
  );
}

// --- locations --------------------------------------------------------------

function VendorLocations({ vendor }: { vendor: Vendor }) {
  const [includeInactive, setIncludeInactive] = useState(false);
  const [adding, setAdding] = useState(false);
  const locations = useLocations({ vendor_id: vendor.id, include_inactive: includeInactive });
  const homeBases = useHomeBases();
  const items = locations.data ?? [];
  const top = items.filter((l) => !l.parent_location_id);
  const stallsOf = (id: string) => items.filter((l) => l.parent_location_id === id);
  // Stalls whose market belongs to another vendor still show, under a heading.
  const orphanStalls = items.filter((l) => l.parent_location_id && !items.some((m) => m.id === l.parent_location_id));

  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <h2 className="text-lg font-medium">Locations</h2>
        <div className="flex flex-wrap items-center gap-3">
          <label className="inline-flex min-h-10 items-center gap-2 text-sm">
            <input type="checkbox" checked={includeInactive} onChange={(e) => setIncludeInactive(e.target.checked)} className={`size-4 ${focusRing}`} />
            Show inactive
          </label>
          <Button variant="secondary" onClick={() => setAdding((v) => !v)} aria-expanded={adding}>
            {adding ? "Cancel" : "Add a location"}
          </Button>
          <Link to={`/catalog/vendors?view=map&place=location`} className={secondaryLinkClass}>
            Add on the map
          </Link>
        </div>
      </div>
      {adding ? <AddLocationForm vendor={vendor} markets={top} onDone={() => setAdding(false)} /> : null}
      {locations.isPending ? (
        <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
          Loading…
        </p>
      ) : locations.isError ? (
        <Alert tone="error">{errorMessage(locations.error)}</Alert>
      ) : items.length === 0 ? (
        <p className="text-sm text-neutral-600 dark:text-neutral-400">
          No locations yet, so this vendor cannot be chosen for a purchase. Add one above, with its coordinates or by dropping a pin on the map.
        </p>
      ) : (
        <ul aria-label="Locations" className="flex flex-col gap-3">
          {top.map((l) => (
            <li key={l.id}>
              <LocationCard location={l} homeBases={homeBases.data ?? []} markets={top} />
              {stallsOf(l.id).length > 0 ? (
                <ul aria-label={`Stalls at ${l.name}`} className="mt-2 ml-4 flex flex-col gap-2 border-l-2 border-neutral-200 pl-3 dark:border-neutral-800">
                  {stallsOf(l.id).map((s) => (
                    <li key={s.id}>
                      <LocationCard location={s} homeBases={homeBases.data ?? []} markets={top} />
                    </li>
                  ))}
                </ul>
              ) : null}
            </li>
          ))}
          {orphanStalls.map((s) => (
            <li key={s.id}>
              <LocationCard location={s} homeBases={homeBases.data ?? []} markets={top} />
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

/**
 * Create a location for this vendor without going to the map.
 *
 * `useCreateLocation` used to have exactly one caller, the map's pin-drop flow
 * (issue #20), so a vendor added on the Vendors page could not be used for a
 * purchase until someone separately found it on a map with no tiles. The vendor
 * is already known here, so the only thing this form has to answer that the map
 * answered by being a map is where the place is.
 */
function AddLocationForm({ vendor, markets, onDone }: { vendor: Vendor; markets: VendorLocation[]; onDone: () => void }) {
  const create = useCreateLocation();
  const [form, setForm] = useState({ name: "", coordinates: "", address: "", opening_hours: "", parent_location_id: "" });
  const [hoursValid, setHoursValid] = useState(true);
  const [invalid, setInvalid] = useState<string | null>(null);
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => setForm((f) => ({ ...f, [key]: value }));

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!form.name.trim()) {
      setInvalid("A name is required.");
      return;
    }
    const point = parseLatLon(form.coordinates);
    if (!point) {
      setInvalid("Coordinates must be a latitude and a longitude, like 33.512345, -120.487654.");
      return;
    }
    if (form.opening_hours.trim() && !hoursValid) {
      setInvalid("Fix the opening hours first.");
      return;
    }
    setInvalid(null);
    const input: LocationCreateInput = { vendor_id: vendor.id, name: form.name.trim(), lat: point.lat, lon: point.lon };
    if (form.address.trim()) input.address = form.address.trim();
    if (form.opening_hours.trim()) input.opening_hours = form.opening_hours.trim();
    if (form.parent_location_id) input.parent_location_id = form.parent_location_id;
    create.mutate(input, { onSuccess: onDone });
  };

  return (
    <form onSubmit={onSubmit} className="mb-4 flex flex-col gap-3 rounded-md border border-neutral-200 p-3 dark:border-neutral-800" aria-label={`Add a location for ${vendor.name}`} noValidate>
      <h3 className="text-base font-medium">New location</h3>
      {invalid ? <Alert tone="error">{invalid}</Alert> : null}
      {create.isError ? <Alert tone="error">{geoErrorMessage(create.error)}</Alert> : null}
      <Field id="add-location-name" label="Name" autoComplete="off" required value={form.name} onChange={(e) => set("name", e.target.value)} hint="What you call this place, e.g. “the one on the coast road”." />
      <CoordinatesField id="add-location-coordinates" value={form.coordinates} onChange={(v) => set("coordinates", v)} disabled={create.isPending} />
      <Field id="add-location-address" label="Address" autoComplete="off" value={form.address} onChange={(e) => set("address", e.target.value)} />
      {markets.length > 0 ? (
        <SelectField id="add-location-parent" label="Stall at" value={form.parent_location_id} onChange={(e) => set("parent_location_id", e.target.value)} hint="Choose a market to make this a stall of it.">
          <option value="">Not a stall</option>
          {markets.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </SelectField>
      ) : null}
      <Disclosure summary="Opening hours (optional)">
        <OpeningHoursInput idPrefix="add-location" value={form.opening_hours} onChange={(v) => set("opening_hours", v)} onValidated={setHoursValid} disabled={create.isPending} />
      </Disclosure>
      {/* Home base and stop overhead are left to the editor on the row this
          creates: neither has to be right before the location can be used, and
          the create form is the one a new deployment meets first. */}
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={create.isPending}>
          {create.isPending ? "Creating…" : "Create location"}
        </Button>
        <Button variant="secondary" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}

function LocationCard({ location, homeBases, markets }: { location: VendorLocation; homeBases: { id: string; name: string }[]; markets: VendorLocation[] }) {
  const [editing, setEditing] = useState(false);
  const setActive = useSetLocationActive(location.id);
  const refresh = useRefreshOsm(location.id);
  const home = homeBases.find((h) => h.id === location.home_base_id);
  return (
    <div className="rounded-md border border-neutral-200 p-3 text-sm dark:border-neutral-800">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="font-medium">
            {location.name}{" "}
            {location.parent_location_id ? <Badge>stall</Badge> : null} {location.active ? null : <Badge tone="warn">inactive</Badge>}
          </p>
          <p className="text-xs text-neutral-600 dark:text-neutral-400">
            {describeOpeningHours(location.effective_opening_hours)}
            {location.opening_hours_inherited ? " (inherited)" : ""}
            {location.address ? ` · ${location.address}` : ""}
            {home ? ` · from ${home.name}` : ""}
            {location.osm_id ? ` · OSM ${location.osm_type} ${location.osm_id}` : ""}
          </p>
          <p className="font-mono text-xs text-neutral-600 dark:text-neutral-400">{formatLatLon(location.lat, location.lon)}</p>
        </div>
        <div className="flex flex-wrap gap-1">
          <Link to={`/catalog/vendors?view=map&location=${encodeURIComponent(location.id)}`} className={`inline-flex min-h-8 items-center rounded-md px-2 text-xs font-medium text-neutral-700 hover:bg-neutral-200 dark:text-neutral-300 dark:hover:bg-neutral-800 ${focusRing}`}>
            Map
          </Link>
          <Button variant="ghost" className="min-h-8 px-2 text-xs" onClick={() => setEditing((v) => !v)} aria-expanded={editing} aria-label={`Edit ${location.name}`}>
            Edit
          </Button>
          {location.osm_id ? (
            <Button variant="ghost" className="min-h-8 px-2 text-xs" disabled={refresh.isPending} onClick={() => refresh.mutate()} aria-label={`Refresh ${location.name} from OpenStreetMap`}>
              {refresh.isPending ? "Refreshing…" : "Refresh from OSM"}
            </Button>
          ) : null}
          <Button variant={location.active ? "danger" : "secondary"} className="min-h-8 px-2 text-xs" disabled={setActive.isPending} onClick={() => setActive.mutate(!location.active)} aria-label={`${location.active ? "Deactivate" : "Activate"} ${location.name}`}>
            {location.active ? "Deactivate" : "Activate"}
          </Button>
        </div>
      </div>
      {setActive.isError ? (
        <Alert tone="error" className="mt-2">
          {geoErrorMessage(setActive.error)}
        </Alert>
      ) : null}
      {refresh.isError ? (
        <Alert tone="error" className="mt-2">
          {geoErrorMessage(refresh.error)}
        </Alert>
      ) : null}
      {editing ? <EditLocationForm location={location} homeBases={homeBases} markets={markets.filter((m) => m.id !== location.id)} onDone={() => setEditing(false)} /> : null}
    </div>
  );
}

function EditLocationForm({ location, homeBases, markets, onDone }: { location: VendorLocation; homeBases: { id: string; name: string }[]; markets: VendorLocation[]; onDone: () => void }) {
  const update = useUpdateLocation(location.id);
  const [form, setForm] = useState({
    name: location.name,
    address: location.address ?? "",
    opening_hours: location.opening_hours ?? "",
    home_base_id: location.home_base_id ?? "",
    parent_location_id: location.parent_location_id ?? "",
    stop_overhead_min: location.stop_overhead_min === null ? "" : String(location.stop_overhead_min),
  });
  const [hoursValid, setHoursValid] = useState(true);
  const [invalid, setInvalid] = useState<string | null>(null);
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) => setForm((f) => ({ ...f, [key]: value }));
  const prefix = `loc-${location.id}`;

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!form.name.trim()) {
      setInvalid("A name is required.");
      return;
    }
    if (form.opening_hours.trim() && !hoursValid) {
      setInvalid("Fix the opening hours first.");
      return;
    }
    if (form.stop_overhead_min.trim() && !/^\d+$/.test(form.stop_overhead_min.trim())) {
      setInvalid("Stop overhead must be a whole number of minutes.");
      return;
    }
    setInvalid(null);
    const input: LocationUpdateInput = {};
    if (form.name.trim() !== location.name) input.name = form.name.trim();
    if ((form.address.trim() || null) !== location.address) input.address = form.address.trim() || null;
    if ((form.opening_hours.trim() || null) !== location.opening_hours) input.opening_hours = form.opening_hours.trim() || null;
    if ((form.home_base_id || null) !== location.home_base_id) input.home_base_id = form.home_base_id || null;
    if ((form.parent_location_id || null) !== location.parent_location_id) input.parent_location_id = form.parent_location_id || null;
    const overhead = form.stop_overhead_min.trim() === "" ? null : Number(form.stop_overhead_min.trim());
    if (overhead !== location.stop_overhead_min) input.stop_overhead_min = overhead;
    if (Object.keys(input).length === 0) {
      onDone();
      return;
    }
    update.mutate(input, { onSuccess: onDone });
  };

  return (
    <form onSubmit={onSubmit} className="mt-3 flex flex-col gap-3 border-t border-neutral-200 pt-3 dark:border-neutral-800" aria-label={`Edit location ${location.name}`} noValidate>
      {invalid ? <Alert tone="error">{invalid}</Alert> : null}
      {update.isError ? <Alert tone="error">{geoErrorMessage(update.error)}</Alert> : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <Field id={`${prefix}-name`} label="Name" autoComplete="off" required value={form.name} onChange={(e) => set("name", e.target.value)} />
        <Field id={`${prefix}-address`} label="Address" autoComplete="off" value={form.address} onChange={(e) => set("address", e.target.value)} />
        <SelectField id={`${prefix}-home`} label="Home base" value={form.home_base_id} onChange={(e) => set("home_base_id", e.target.value)} hint="Cleared means no default base.">
          <option value="">None</option>
          {homeBases.map((h) => (
            <option key={h.id} value={h.id}>
              {h.name}
            </option>
          ))}
        </SelectField>
        <SelectField id={`${prefix}-parent`} label="Stall at" value={form.parent_location_id} onChange={(e) => set("parent_location_id", e.target.value)} hint="Choose a market to make this a stall of it.">
          <option value="">Not a stall</option>
          {markets.map((m) => (
            <option key={m.id} value={m.id}>
              {m.name}
            </option>
          ))}
        </SelectField>
        <Field id={`${prefix}-overhead`} label="Stop overhead (minutes)" inputMode="numeric" autoComplete="off" value={form.stop_overhead_min} onChange={(e) => set("stop_overhead_min", e.target.value)} hint="Parking, queueing, and so on. Used by later phases." />
      </div>
      <OpeningHoursInput idPrefix={prefix} value={form.opening_hours} onChange={(v) => set("opening_hours", v)} onValidated={setHoursValid} noneHint={location.parent_location_id ? "A stall without hours inherits the market's." : undefined} />
      <div className="flex flex-wrap gap-2">
        <Button type="submit" disabled={update.isPending}>
          {update.isPending ? "Saving…" : "Save location"}
        </Button>
        <Button variant="secondary" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
