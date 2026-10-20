import { useState, type SyntheticEvent } from "react";
import {
  geoErrorMessage,
  priceScopeLabel,
  useBrandFamilies,
  useUpdateVendor,
  vendorKindLabel,
  PRICE_SCOPES,
  VENDOR_KINDS,
  type Vendor,
  type VendorUpdateInput,
} from "../../api/geo";
import { RadioGroup, SelectField, TextAreaField } from "../catalog/fields";
import { Alert, Button, Card, Field, sectionHeading } from "../ui";

interface VendorForm {
  name: string;
  kind: Vendor["kind"];
  price_scope: Vendor["price_scope"];
  website: string;
  shop_url: string;
  search_url: string;
  notes: string;
  brand_family_id: string;
}

/** The optional text fields: an emptied one is sent as null, which clears it. */
const OPTIONAL = ["website", "shop_url", "search_url", "notes"] as const;

/** The fields the form changed, each as the API takes it. */
export function vendorChanges(vendor: Vendor, form: VendorForm): VendorUpdateInput {
  const input: VendorUpdateInput = {};
  if (form.name.trim() !== vendor.name) input.name = form.name.trim();
  if (form.kind !== vendor.kind) input.kind = form.kind;
  if (form.price_scope !== vendor.price_scope) input.price_scope = form.price_scope;
  for (const key of OPTIONAL) {
    const value = form[key].trim() || null;
    if (value !== (vendor[key] ?? null)) input[key] = value;
  }
  const family = form.brand_family_id || null;
  if (family !== (vendor.brand_family?.id ?? null)) input.brand_family_id = family;
  return input;
}

/** Where the store sells online, and its search page for finding a receipt line there. */
function OnlineFields({
  shopUrl,
  searchUrl,
  onShopUrl,
  onSearchUrl,
}: {
  shopUrl: string;
  searchUrl: string;
  onShopUrl: (v: string) => void;
  onSearchUrl: (v: string) => void;
}) {
  return (
    <>
      <Field
        id="edit-vendor-shop-url"
        label="Online shop"
        hint="Where it sells online when that is another site, such as a storefront on a delivery service. Pages you clip there count as this store’s."
        type="url"
        autoComplete="off"
        placeholder="https://"
        value={shopUrl}
        onChange={(e) => onShopUrl(e.target.value)}
      />
      <Field
        id="edit-vendor-search-url"
        label="Search page"
        hint="Its search address with {q} where the words go, such as https://shop.example.com/search?q={q}. Lines waiting to be identified link there."
        type="url"
        autoComplete="off"
        placeholder="https://…{q}"
        value={searchUrl}
        onChange={(e) => onSearchUrl(e.target.value)}
      />
    </>
  );
}

/** Edit a vendor's name, kind, scope, addresses, store brands and notes. */
export function EditVendorForm({ vendor, onDone }: { vendor: Vendor; onDone: () => void }) {
  const update = useUpdateVendor(vendor.id);
  const [form, setForm] = useState({
    name: vendor.name,
    kind: vendor.kind,
    price_scope: vendor.price_scope,
    website: vendor.website ?? "",
    shop_url: vendor.shop_url ?? "",
    search_url: vendor.search_url ?? "",
    notes: vendor.notes ?? "",
    brand_family_id: vendor.brand_family?.id ?? "",
  });
  const families = useBrandFamilies();
  const retailers = (families.data?.items ?? []).filter((f) => f.kind === "retailer");
  const [invalid, setInvalid] = useState<string | null>(null);
  const set = <K extends keyof typeof form>(key: K, value: (typeof form)[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const onSubmit = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!form.name.trim()) {
      setInvalid("A name is required.");
      return;
    }
    setInvalid(null);
    const input = vendorChanges(vendor, form);
    if (Object.keys(input).length === 0) {
      onDone();
      return;
    }
    update.mutate(input, { onSuccess: onDone });
  };

  return (
    <Card>
      <form
        onSubmit={onSubmit}
        className="flex flex-col gap-4"
        aria-labelledby="edit-vendor-heading"
        noValidate
      >
        <h2 id="edit-vendor-heading" className={sectionHeading}>
          Edit vendor
        </h2>
        {invalid ? <Alert tone="error">{invalid}</Alert> : null}
        {update.isError ? <Alert tone="error">{geoErrorMessage(update.error)}</Alert> : null}
        <Field
          id="edit-vendor-name"
          label="Name"
          autoComplete="off"
          required
          value={form.name}
          onChange={(e) => set("name", e.target.value)}
        />
        <RadioGroup
          name="edit-vendor-kind"
          legend="Kind"
          options={VENDOR_KINDS.map((k) => ({ value: k, label: vendorKindLabel[k] }))}
          value={form.kind}
          onChange={(v) => set("kind", v)}
        />
        <RadioGroup
          name="edit-vendor-scope"
          legend="Price scope"
          options={PRICE_SCOPES.map((s) => ({ value: s, label: priceScopeLabel[s] }))}
          value={form.price_scope}
          onChange={(v) => set("price_scope", v)}
        />
        <Field
          id="edit-vendor-website"
          label="Website"
          type="url"
          autoComplete="off"
          value={form.website}
          onChange={(e) => set("website", e.target.value)}
        />
        <OnlineFields
          shopUrl={form.shop_url}
          searchUrl={form.search_url}
          onShopUrl={(v) => set("shop_url", v)}
          onSearchUrl={(v) => set("search_url", v)}
        />
        {retailers.length > 0 || vendor.brand_family ? (
          <SelectField
            id="edit-vendor-brand-family"
            label="Store brands"
            hint="Whose own brands this store sells. Matched from the brand list when it's imported; your choice is kept."
            value={form.brand_family_id}
            onChange={(e) => set("brand_family_id", e.target.value)}
          >
            <option value="">None or not known</option>
            {retailers.map((f) => (
              <option key={f.id} value={f.id}>
                {f.name}
              </option>
            ))}
          </SelectField>
        ) : null}
        <TextAreaField
          id="edit-vendor-notes"
          label="Notes"
          value={form.notes}
          onChange={(e) => set("notes", e.target.value)}
        />
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
