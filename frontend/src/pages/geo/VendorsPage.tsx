import { useState, type FormEvent } from "react";
import { Link } from "react-router";
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
  type Vendor,
  type VendorCreateInput,
  type VendorKind,
} from "../../api/geo";
import { Badge, RadioGroup, SelectField, TextAreaField } from "../../components/catalog/fields";
import { Alert, Button, Card, EmptyState, Field, PageHeader, focusRing, secondaryLinkClass } from "../../components/ui";
import { describeOpeningHours } from "../../lib/openingHours";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { usePageTitle } from "../../lib/usePageTitle";
import { useNotice } from "../../components/Notice";

export function VendorsPage() {
  usePageTitle("Vendors");
  const [q, setQ] = useState("");
  const [kind, setKind] = useState<VendorKind | "">("");
  const [includeInactive, setIncludeInactive] = useState(false);
  const debouncedQ = useDebouncedValue(q.trim(), 250);
  const vendors = useVendors(debouncedQ, includeInactive);
  // The API has no kind filter for vendors; narrow the loaded list here.
  const items = (vendors.data ?? []).filter((v) => !kind || v.kind === kind);

  return (
    <>
      <PageHeader title="Vendors">
        {/* Until T6's list/map toggle: the map is the Vendors page's map view (T9). */}
        <Link to="/catalog/vendors?view=map" className={secondaryLinkClass}>
          Map view
        </Link>
      </PageHeader>
      <div className="flex flex-col gap-6">
        <CreateVendorForm />

        <Card>
          <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
            <h2 className="text-lg font-medium">Vendor directory</h2>
            <label className="inline-flex min-h-10 items-center gap-2 text-sm">
              <input type="checkbox" checked={includeInactive} onChange={(e) => setIncludeInactive(e.target.checked)} className={`size-4 ${focusRing}`} />
              Show inactive
            </label>
          </div>
          <div className="mb-3 grid gap-3 sm:grid-cols-2">
            <Field id="vendor-search" label="Search" type="search" autoComplete="off" placeholder="Part of a name" value={q} onChange={(e) => setQ(e.target.value)} />
            <SelectField id="vendor-kind-filter" label="Kind" value={kind} onChange={(e) => setKind(e.target.value as VendorKind | "")}>
              <option value="">All kinds</option>
              {VENDOR_KINDS.map((k) => (
                <option key={k} value={k}>
                  {vendorKindLabel[k]}
                </option>
              ))}
            </SelectField>
          </div>
          {vendors.isPending ? (
            <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
              Loading…
            </p>
          ) : vendors.isError ? (
            <Alert tone="error">{errorMessage(vendors.error)}</Alert>
          ) : items.length === 0 ? (
            <EmptyState title={debouncedQ || kind ? "No vendors match" : "No vendors yet"}>
              {debouncedQ || kind ? "Try a shorter search or another kind." : "Vendors are the shops, markets, and stands you buy from. Add one above, then give it a location on its page."}
            </EmptyState>
          ) : (
            <ul aria-label="Vendors" className="divide-y divide-neutral-200 dark:divide-neutral-800">
              {items.map((v) => (
                <VendorRow key={v.id} vendor={v} />
              ))}
            </ul>
          )}
        </Card>

        <OsmAdoptionPanel />
      </div>
    </>
  );
}

function VendorRow({ vendor }: { vendor: Vendor }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 py-2">
      <Link to={`/catalog/vendors/${vendor.id}`} className={`rounded-md font-medium underline-offset-2 hover:underline ${focusRing}`}>
        {vendor.name}
      </Link>
      <span className="flex flex-wrap items-center gap-2 text-xs text-neutral-600 dark:text-neutral-400">
        <span>{vendorKindLabel[vendor.kind]}</span>
        <Badge>{priceScopeLabel[vendor.price_scope]}</Badge>
        {vendor.active ? null : <Badge tone="warn">inactive</Badge>}
      </span>
    </li>
  );
}

// --- create -----------------------------------------------------------------

const emptyForm = { name: "", kind: "independent" as VendorKind, price_scope: "location" as PriceScope, website: "", notes: "" };

function CreateVendorForm() {
  const create = useCreateVendor();
  const [form, setForm] = useState(emptyForm);
  const [invalid, setInvalid] = useState<string | null>(null);
  const notice = useNotice();
  const set = <K extends keyof typeof emptyForm>(key: K, value: (typeof emptyForm)[K]) => setForm((f) => ({ ...f, [key]: value }));

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
      onSuccess: (vendor) => {
        notice.show({
          tone: "success",
          message: `Added ${vendor.name}. It can't be chosen for a purchase until it has a location; add one on its page.`,
          action: { label: "Open it", to: `/catalog/vendors/${vendor.id}` },
        });
        setForm(emptyForm);
        document.getElementById("new-vendor-name")?.focus();
      },
    });
  };

  return (
    <Card>
      <form onSubmit={onSubmit} className="flex flex-col gap-4" aria-labelledby="create-vendor-heading" noValidate>
        <h2 id="create-vendor-heading" className="text-lg font-medium">
          Add a vendor
        </h2>
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
          hint="Chain-wide: one price applies at every location. Per location: each location has its own prices."
        />
        <Field id="new-vendor-website" label="Website" type="url" autoComplete="off" placeholder="https://" value={form.website} onChange={(e) => set("website", e.target.value)} />
        <TextAreaField id="new-vendor-notes" label="Notes" value={form.notes} onChange={(e) => set("notes", e.target.value)} />
        <div>
          <Button type="submit" disabled={create.isPending}>
            {create.isPending ? "Creating…" : "Create vendor"}
          </Button>
        </div>
      </form>
    </Card>
  );
}

// --- OpenStreetMap adoption -------------------------------------------------

const RADII = [500, 1000, 2000, 5000, 10000, 20000];

export function OsmAdoptionPanel() {
  const homeBases = useHomeBases();
  const [homeBaseId, setHomeBaseId] = useState("");
  const [radius, setRadius] = useState(2000);
  const [searched, setSearched] = useState<{ homeBaseId: string; radius: number } | null>(null);
  const candidates = useOsmCandidates(searched?.homeBaseId ?? "", searched?.radius ?? 0, searched !== null);
  const disabled = candidates.isError && isIntegrationDisabled(candidates.error);

  return (
    <Card>
      <h2 className="mb-1 text-lg font-medium">Adopt from OpenStreetMap</h2>
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
      {homeBases.data && homeBases.data.length === 0 ? (
        <p className="mt-3 text-sm text-neutral-600 dark:text-neutral-400">Create a home base first, on the map or under Settings.</p>
      ) : null}
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
    </Card>
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
