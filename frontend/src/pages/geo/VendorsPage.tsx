import { useEffect, useId, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { errorMessage } from "../../api/client";
import {
  PRICE_SCOPES,
  VENDOR_KINDS,
  geoErrorMessage,
  isIntegrationDisabled,
  priceScopeLabel,
  useAdoptOsm,
  useCreateVendor,
  useHomeBases,
  useOsmCandidates,
  useVendors,
  vendorKindLabel,
  type OsmCandidate,
  type PriceScope,
  type VendorCreateInput,
  type VendorListItem,
  type VendorKind,
} from "../../api/geo";
import { Badge, RadioGroup, SelectField, TextAreaField } from "../../components/catalog/fields";
import { Alert, Button, Card, EmptyState, Field, PageHeader, focusRing, primaryLinkClass } from "../../components/ui";
import { Dialog } from "../../components/Dialog";
import { Drawer } from "../../components/Drawer";
import { SegmentedControl } from "../../components/SegmentedControl";
import { formatDate } from "../../lib/format";
import { MapPage } from "./MapPage";
import { describeOpeningHours } from "../../lib/openingHours";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { usePageTitle } from "../../lib/usePageTitle";
import { useNotice } from "../../components/Notice";

const KIND_OPTIONS: { value: VendorKind | ""; label: string }[] = [
  { value: "", label: "All" },
  { value: "chain", label: "Chains" },
  { value: "independent", label: "Independents" },
  { value: "market", label: "Markets" },
  { value: "stand", label: "Stands" },
];
const isKind = (v: string | null): v is VendorKind => VENDOR_KINDS.includes(v as VendorKind);
const muted = "text-neutral-600 dark:text-neutral-400";

/**
 * Vendors (docs/spec/10): search, the kind control and the list/map toggle, all
 * kept in the URL (`?view=map`, T9). The map view keeps every map function,
 * including dropping a pin to create a vendor and its location in one act.
 */
export function VendorsPage() {
  const [params, setParams] = useSearchParams();
  const view = params.get("view") === "map" ? "map" : "list";
  const q = params.get("q") ?? "";
  const kind = isKind(params.get("kind")) ? (params.get("kind") as VendorKind) : "";
  const includeInactive = params.get("inactive") === "1";
  const setParam = (key: string, value: string | null) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (value) next.set(key, value);
        else next.delete(key);
        return next;
      },
      { replace: true },
    );

  const [adding, setAdding] = useState(false);
  const [finding, setFinding] = useState(false);

  return (
    <>
      <PageHeader title="Vendors" description="The shops, markets and stands you buy from, and where they are.">
        <div className="flex flex-wrap gap-2">
          <Button variant="secondary" onClick={() => setFinding(true)}>
            Find nearby
          </Button>
          <Button onClick={() => setAdding(true)}>Add vendor</Button>
        </div>
      </PageHeader>

      <div className="mb-4">
        <SegmentedControl
          label="View"
          options={[
            { value: "list", label: "List" },
            { value: "map", label: "Map" },
          ]}
          value={view}
          onChange={(v) => setParam("view", v === "map" ? "map" : null)}
        />
      </div>

      {view === "map" ? (
        <MapPage embedded />
      ) : (
        <VendorList q={q} kind={kind} includeInactive={includeInactive} setParam={setParam} onAdd={() => setAdding(true)} />
      )}

      {adding ? <AddVendorDrawer onClose={() => setAdding(false)} /> : null}
      {finding ? <FindNearbyDialog onClose={() => setFinding(false)} /> : null}
    </>
  );
}

function VendorList({
  q,
  kind,
  includeInactive,
  setParam,
  onAdd,
}: {
  q: string;
  kind: VendorKind | "";
  includeInactive: boolean;
  setParam: (key: string, value: string | null) => void;
  onAdd: () => void;
}) {
  usePageTitle("Vendors");
  const [text, setText] = useState(q);
  // The field follows ?q= when it changes from outside (Back, a link), and the
  // URL takes only text the debounce has settled on, so an old pending value
  // cannot be written back over the new one.
  const [seenQ, setSeenQ] = useState(q);
  if (q !== seenQ) {
    setSeenQ(q);
    setText(q);
  }
  const debounced = useDebouncedValue(text.trim(), 250);
  useEffect(() => {
    if (debounced === text.trim() && debounced !== q) setParam("q", debounced || null);
    // Only the typed text drives the URL.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [debounced]);

  const vendors = useVendors(q, includeInactive);
  // The API has no kind filter for vendors, and the list is not paged, so
  // narrowing the loaded list here is exact.
  const items = (vendors.data ?? []).filter((v) => !kind || v.kind === kind);
  const filtered = q !== "" || kind !== "";

  return (
    <>
      <div className="mb-4 flex flex-col gap-3">
        <label htmlFor="vendor-search" className="sr-only">
          Search vendors
        </label>
        <input
          id="vendor-search"
          type="text"
          enterKeyHint="search"
          autoComplete="off"
          maxLength={200}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Search by name"
          className={`min-h-12 w-full rounded-lg border border-neutral-300 bg-white px-4 text-base dark:border-neutral-700 dark:bg-neutral-900 ${focusRing}`}
        />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <SegmentedControl label="Kind" options={KIND_OPTIONS} value={kind} onChange={(v) => setParam("kind", v || null)} />
          <label className="inline-flex min-h-11 items-center gap-2 text-sm lg:min-h-9">
            <input
              type="checkbox"
              checked={includeInactive}
              onChange={(e) => setParam("inactive", e.target.checked ? "1" : null)}
              className={`size-4 ${focusRing}`}
            />
            Show inactive
          </label>
        </div>
      </div>

      {vendors.isPending ? (
        <p role="status" className={`text-sm ${muted}`}>
          Loading…
        </p>
      ) : vendors.isError ? (
        <Alert tone="error">{errorMessage(vendors.error)}</Alert>
      ) : items.length === 0 ? (
        filtered ? (
          <EmptyState
            title={`No vendors match${q ? ` ‘${q}’` : ""}${kind ? ` among ${KIND_OPTIONS.find((o) => o.value === kind)?.label.toLowerCase()}` : ""}`}
            action={
              <Button
                variant="secondary"
                onClick={() => {
                  setText("");
                  setParam("q", null);
                  setParam("kind", null);
                }}
              >
                Clear filters
              </Button>
            }
          />
        ) : (
          <EmptyState
            title="No vendors yet. Drop a pin on the map"
            action={
              <div className="flex flex-wrap justify-center gap-2">
                <Link to="/catalog/vendors?view=map&place=location" className={primaryLinkClass}>
                  Open the map
                </Link>
                <Button variant="secondary" onClick={onAdd}>
                  Add vendor
                </Button>
              </div>
            }
          >
            A pin names the vendor and its location in one step.
          </EmptyState>
        )
      ) : (
        <ul aria-label="Vendors" className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((v) => (
            <VendorCard key={v.id} vendor={v} />
          ))}
        </ul>
      )}
    </>
  );
}

function VendorCard({ vendor }: { vendor: VendorListItem }) {
  const locations = vendor.location_count;
  return (
    <li data-testid="vendor-card" className="flex flex-col gap-2 rounded-lg border border-neutral-200 bg-white p-4 dark:border-neutral-800 dark:bg-neutral-900">
      <div className="flex items-start justify-between gap-2">
        <Link id={`vendor-row-${vendor.id}`} to={`/catalog/vendors/${vendor.id}`} className={`font-display rounded text-lg leading-snug underline-offset-2 hover:underline ${focusRing}`}>
          {vendor.name}
        </Link>
        <Badge>{vendorKindLabel[vendor.kind]}</Badge>
      </div>
      <dl className={`grid gap-0.5 text-sm ${muted}`}>
        <div>
          <dt className="sr-only">Locations</dt>
          <dd>{locations === 0 ? "No locations yet" : `${locations} ${locations === 1 ? "location" : "locations"}`}</dd>
        </div>
        <div>
          <dt className="sr-only">Last visit</dt>
          <dd>{vendor.last_visit ? `Last visit ${formatDate(vendor.last_visit)}` : "Not visited yet"}</dd>
        </div>
        <div>
          <dt className="sr-only">Pricing</dt>
          <dd>{priceScopeLabel[vendor.price_scope]}</dd>
        </div>
      </dl>
      {vendor.active ? null : (
        <span>
          <Badge tone="warn">inactive</Badge>
        </span>
      )}
    </li>
  );
}

// --- create -----------------------------------------------------------------

const emptyForm = { name: "", kind: "independent" as VendorKind, price_scope: "location" as PriceScope, website: "", notes: "" };

function AddVendorDrawer({ onClose }: { onClose: () => void }) {
  const create = useCreateVendor();
  const [form, setForm] = useState(emptyForm);
  const [invalid, setInvalid] = useState<string | null>(null);
  const notice = useNotice();
  const vendors = useVendors();
  const set = <K extends keyof typeof emptyForm>(key: K, value: (typeof emptyForm)[K]) => setForm((f) => ({ ...f, [key]: value }));
  const dirty = JSON.stringify(form) !== JSON.stringify(emptyForm);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!form.name.trim()) {
      setInvalid("A name is required.");
      return;
    }
    setInvalid(null);
    const input: VendorCreateInput = { name: form.name.trim(), kind: form.kind, price_scope: form.price_scope };
    if (form.website.trim()) input.website = form.website.trim();
    if (form.notes.trim()) input.notes = form.notes.trim();
    create.mutate(input, {
      onSuccess: async (vendor) => {
        onClose();
        await vendors.refetch();
        // G10, and the one thing a new vendor needs said: it has no location yet.
        requestAnimationFrame(() => {
          const row = document.getElementById(`vendor-row-${vendor.id}`);
          row?.focus();
          notice.show({
            tone: "success",
            message: `Added ${vendor.name}. It can't be chosen for a purchase until it has a location; add one on its page.`,
            action: { label: "Open it", to: `/catalog/vendors/${vendor.id}` },
            focusAction: !row,
          });
        });
      },
    });
  };

  return (
    <Drawer title="Add vendor" thing="vendor" dirty={dirty} onClose={onClose} formId="add-vendor" primaryLabel="Add vendor" busy={create.isPending} busyLabel="Adding…">
      <form id="add-vendor" onSubmit={onSubmit} className="flex flex-col gap-4" aria-label="Add vendor" noValidate>
        {invalid ? <Alert tone="error">{invalid}</Alert> : null}
        {create.isError ? <Alert tone="error">{geoErrorMessage(create.error)}</Alert> : null}
        <Field id="new-vendor-name" label="Name" autoComplete="off" required value={form.name} onChange={(e) => set("name", e.target.value)} />
        <RadioGroup name="new-vendor-kind" legend="Kind" options={VENDOR_KINDS.map((k) => ({ value: k, label: vendorKindLabel[k] }))} value={form.kind} onChange={(v) => set("kind", v)} />
        <RadioGroup
          name="new-vendor-scope"
          legend="Price scope"
          options={PRICE_SCOPES.map((s) => ({ value: s, label: priceScopeLabel[s] }))}
          value={form.price_scope}
          onChange={(v) => set("price_scope", v)}
          hint="A chain usually charges the same everywhere; a market stall sets its own."
        />
        <Field id="new-vendor-website" label="Website" type="url" autoComplete="off" placeholder="https://" value={form.website} onChange={(e) => set("website", e.target.value)} />
        <TextAreaField id="new-vendor-notes" label="Notes" value={form.notes} onChange={(e) => set("notes", e.target.value)} />
      </form>
    </Drawer>
  );
}

/** Find nearby: the OpenStreetMap adoption flow in a dialog (UI-3.8). */
function FindNearbyDialog({ onClose }: { onClose: () => void }) {
  const titleId = useId();
  return (
    <Dialog open onClose={onClose} labelledBy={titleId} className="max-h-[80vh] overflow-y-auto p-5">
      <div className="mb-3 flex items-start justify-between gap-3">
        <h2 id={titleId} className="font-display text-xl">
          Find nearby
        </h2>
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
      <OsmAdoptionPanel bare />
    </Dialog>
  );
}

// --- OpenStreetMap adoption -------------------------------------------------

const RADII = [500, 1000, 2000, 5000, 10000, 20000];

export function OsmAdoptionPanel({ bare = false }: { bare?: boolean }) {
  const homeBases = useHomeBases();
  const [chosen, setChosen] = useState("");
  // Pre-set to the only home base when there is exactly one (UI-3.8).
  const only = homeBases.data?.length === 1 ? homeBases.data[0].id : "";
  const homeBaseId = chosen || only;
  const setHomeBaseId = setChosen;
  const [radius, setRadius] = useState(2000);
  const [searched, setSearched] = useState<{ homeBaseId: string; radius: number } | null>(null);
  const candidates = useOsmCandidates(searched?.homeBaseId ?? "", searched?.radius ?? 0, searched !== null);
  const disabled = candidates.isError && isIntegrationDisabled(candidates.error);

  if (homeBases.data && homeBases.data.length === 0) {
    return (
      <p className={`text-sm ${muted}`}>
        Finding nearby shops starts from one of your kitchens. Add one first, under{" "}
        <Link to="/settings/kitchens" className={`rounded font-medium underline ${focusRing}`}>
          Settings → Kitchens
        </Link>
        .
      </p>
    );
  }

  const Wrapper = bare ? "div" : Card;
  return (
    <Wrapper>
      {bare ? null : <h2 className="mb-1 text-lg font-medium">Adopt from OpenStreetMap</h2>}
      <p className="mb-3 text-sm text-neutral-600 dark:text-neutral-400">
        Lists food shops and marketplaces near a home base so you can adopt the ones you use. Optional; needs
        the Overpass integration switched on. © OpenStreetMap contributors.
      </p>
      <form
        className="grid gap-3 sm:grid-cols-3"
        aria-label="Find OpenStreetMap candidates"
        onSubmit={(e) => {
          e.preventDefault();
          if (homeBaseId) setSearched({ homeBaseId, radius });
        }}
      >
        <SelectField id="osm-home-base" label="Home base" value={homeBaseId} onChange={(e) => setHomeBaseId(e.target.value)} required>
          <option value="">{homeBases.isPending ? "Loading…" : "Choose"}</option>
          {(homeBases.data ?? []).map((h) => (
            <option key={h.id} value={h.id}>
              {h.name}
            </option>
          ))}
        </SelectField>
        <SelectField id="osm-radius" label="Radius" value={String(radius)} onChange={(e) => setRadius(Number(e.target.value))}>
          {RADII.map((r) => (
            <option key={r} value={r}>
              {r >= 1000 ? `${r / 1000} km` : `${r} m`}
            </option>
          ))}
        </SelectField>
        <div className="flex items-end">
          <Button type="submit" variant="secondary" disabled={!homeBaseId || candidates.isFetching}>
            {candidates.isFetching ? "Searching…" : "Find candidates"}
          </Button>
        </div>
      </form>
      {disabled ? (
        <p role="status" className="mt-3 text-sm text-neutral-600 dark:text-neutral-400">
          OpenStreetMap adoption is off; set ENABLE_OVERPASS=true.
        </p>
      ) : candidates.isError ? (
        <Alert tone="error" className="mt-3">
          {geoErrorMessage(candidates.error)}
        </Alert>
      ) : candidates.data ? (
        candidates.data.length === 0 ? (
          <p className="mt-3 text-sm text-neutral-600 dark:text-neutral-400">No candidates within that radius.</p>
        ) : (
          <ul aria-label="OpenStreetMap candidates" className="mt-3 divide-y divide-neutral-200 dark:divide-neutral-800">
            {candidates.data.map((c) => (
              <CandidateRow key={`${c.osm_type}-${c.osm_id}`} candidate={c} homeBaseId={searched?.homeBaseId ?? ""} radius={searched?.radius ?? 0} />
            ))}
          </ul>
        )
      ) : null}
    </Wrapper>
  );
}

function CandidateRow({ candidate, homeBaseId, radius }: { candidate: OsmCandidate; homeBaseId: string; radius: number }) {
  const adopt = useAdoptOsm();
  const [kind, setKind] = useState<VendorKind>(candidate.kind_guess);
  const name = candidate.name ?? `${candidate.osm_type} ${candidate.osm_id}`;
  const adopted = candidate.already_adopted || adopt.isSuccess;
  const selectId = `osm-kind-${candidate.osm_type}-${candidate.osm_id}`;
  return (
    <li className="flex flex-wrap items-center justify-between gap-3 py-2 text-sm">
      <div className="min-w-0">
        <p className="font-medium">{name}</p>
        <p className="text-xs text-neutral-600 dark:text-neutral-400">
          {candidate.address ?? "no address"} · {describeOpeningHours(candidate.opening_hours)}
        </p>
        {adopt.isError ? (
          <p role="alert" className="text-xs text-red-700 dark:text-red-300">
            {geoErrorMessage(adopt.error)}
          </p>
        ) : null}
      </div>
      {adopted ? (
        <Badge>adopted</Badge>
      ) : (
        <div className="flex items-end gap-2">
          <div className="flex flex-col gap-1">
            <label htmlFor={selectId} className="text-xs font-medium">
              Kind
            </label>
            <select id={selectId} value={kind} onChange={(e) => setKind(e.target.value as VendorKind)} className={`min-h-8 rounded-md border border-neutral-300 bg-white px-2 text-sm dark:border-neutral-700 dark:bg-neutral-900 ${focusRing}`}>
              {VENDOR_KINDS.map((k) => (
                <option key={k} value={k}>
                  {vendorKindLabel[k]}
                </option>
              ))}
            </select>
          </div>
          <Button
            variant="secondary"
            className="min-h-8 px-2"
            disabled={adopt.isPending}
            aria-label={`Adopt ${name}`}
            onClick={() => adopt.mutate({ osm_type: candidate.osm_type, osm_id: candidate.osm_id, home_base_id: homeBaseId, radius_m: radius, vendor_kind: kind })}
          >
            {adopt.isPending ? "Adopting…" : "Adopt"}
          </Button>
        </div>
      )}
    </li>
  );
}
