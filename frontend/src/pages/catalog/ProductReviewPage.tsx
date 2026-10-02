import { useEffect, useId, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { api, errorMessage, isApiError } from "../../api/client";
import { useProduct, type Ingredient } from "../../api/catalog";
import { ROLE_LABELS, type PhotoRole, type ProductPhoto } from "../../api/productPhotos";
import {
  CHANNEL_WORDS,
  KIND_LABELS,
  SOURCE_BADGES,
  isReading,
  readingWords,
  takenBy,
  useAcceptProposal,
  usePendingProposals,
  useProposal,
  useRejectProposal,
  type AcceptInput,
  type FieldCandidate,
  type Pack,
  type ProductKind,
  type Proposal,
  type ProposalField,
} from "../../api/proposals";
import { Badge } from "../../components/catalog/fields";
import { IngredientPicker, type IngredientChoice } from "../../components/catalog/IngredientPicker";
import { useNavigateWithNotice } from "../../components/Notice";
import { SegmentedControl } from "../../components/SegmentedControl";
import { Alert, Button, EmptyState, Field, PageHeader, focusRing } from "../../components/ui";
import { formatMoney } from "../../lib/decimal";
import { formatDate, formatDateTime } from "../../lib/format";
import { usePageTitle } from "../../lib/usePageTitle";

const muted = "text-neutral-600 dark:text-neutral-400";
const sectionHeading = "font-display mb-2 text-lg";
const ROLE_OPTIONS = Object.entries(ROLE_LABELS) as [PhotoRole, string][];
const KIND_OPTIONS = (Object.entries(KIND_LABELS) as [ProductKind, string][]).map(([value, label]) => ({ value, label }));
/** The fields a review shows, in order, with their labels. */
const REVIEWED: [string, string][] = [
  ["title", "Name"],
  ["brand", "Brand"],
  ["pack", "Pack"],
  ["gtin", "Barcode"],
];

/** A field's value as a person reads it. */
export function showValue(field: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (field === "pack" && typeof value === "object") {
    const pack = value as Pack;
    return `${pack.qty.replace(/\.0+$|(\.\d*?)0+$/, "$1")} ${pack.unit}`;
  }
  if (field === "gtin" && typeof value === "string") return value.replace(/^0+(?=\d{12,13}$)/, "");
  if (field === "price" && typeof value === "string") return formatMoney(value);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

export function ProductReviewPage() {
  const { id } = useParams<{ id: string }>();
  const proposal = useProposal(id);
  usePageTitle("Review product");
  if (proposal.isPending) {
    return (
      <>
        <PageHeader title="Review product" />
        <p role="status" className={`text-sm ${muted}`}>
          Loading…
        </p>
      </>
    );
  }
  if (proposal.isError) {
    const missing = isApiError(proposal.error) && proposal.error.status === 404;
    return (
      <>
        <PageHeader title="Review product" />
        {missing ? (
          <EmptyState title="No such proposal">
            <Link to="/" className={`rounded-md underline ${focusRing}`}>
              Back to Home
            </Link>
          </EmptyState>
        ) : (
          <Alert tone="error">{errorMessage(proposal.error)}</Alert>
        )}
      </>
    );
  }
  return <Review key={proposal.data.id} proposal={proposal.data} />;
}

interface Choice {
  /** The index into [chosen, ...alternatives], or null while a conflict is open. */
  index: number | null;
}

function candidates(field: ProposalField): FieldCandidate[] {
  return [field, ...field.alternatives];
}

function Review({ proposal }: { proposal: Proposal }) {
  const fields = proposal.fields;
  const readOnly = proposal.status !== "pending";
  const reading = isReading(proposal);
  const nothingRead = !reading && Object.keys(fields).length === 0;
  const strong = proposal.match.strong ?? null;
  const strongProduct = useProduct(strong?.product_id);
  const pending = usePendingProposals();
  const navigateWithNotice = useNavigateWithNotice();
  const accept = useAcceptProposal(proposal.id);
  const reject = useRejectProposal(proposal.id);
  const id = useId();
  const matchRef = useRef<HTMLFieldSetElement>(null);

  const [match, setMatch] = useState<string | null>(proposal.match.preselect ?? null);
  const [choices, setChoices] = useState<Record<string, Choice>>(() =>
    Object.fromEntries(Object.entries(fields).map(([name, f]) => [name, { index: f && f.conflict ? null : 0 }])),
  );
  const [typed, setTyped] = useState<Record<string, string>>({});
  const [ingredient, setIngredient] = useState<IngredientChoice | null>(null);
  const productPhotos = proposal.photos.filter((p) => p.role === "product");
  const [mainPhoto, setMainPhoto] = useState<string | null>(productPhotos[0]?.id ?? null);
  const [roles, setRoles] = useState<Record<string, PhotoRole>>({});
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const brandOrCode = Boolean(fields.brand?.value || fields.gtin?.value);
  const [kind, setKind] = useState<ProductKind>(brandOrCode ? "branded" : "loose");
  const vendor = proposal.vendor;
  const [recordPrice, setRecordPrice] = useState(Boolean(proposal.price && vendor?.suggested_location_id));
  const [location, setLocation] = useState<string | null>(vendor?.suggested_location_id ?? null);
  const [taken, setTaken] = useState<{ product_id: string; name: string | null } | null>(null);
  const isNew = match === "new";
  const conflicts = REVIEWED.filter(([name]) => fields[name]?.conflict && choices[name]?.index === null);
  const collapsible = Boolean(strong) && match === proposal.match.preselect && conflicts.length === 0 && !nothingRead;
  const [editing, setEditing] = useState(!collapsible);

  useEffect(() => {
    // Focus starts on Match (10).
    matchRef.current?.querySelector<HTMLInputElement>("input:checked, input")?.focus();
  }, []);

  const chosenValue = (name: string): unknown => {
    if (typed[name] !== undefined) return typed[name];
    const field = fields[name];
    const index = choices[name]?.index;
    if (!field || index === null || index === undefined) return undefined;
    return candidates(field)[index]?.value;
  };
  const title = String(chosenValue("title") ?? "").trim();

  const blocker = ((): string | null => {
    if (readOnly) return null;
    if (!match) return "Choose whether to update a product or create one";
    if (conflicts.length > 0) return `Choose a ${conflicts[0][1].toLowerCase()} to accept`;
    if (isNew && !ingredient) return "Choose an ingredient to accept";
    if (isNew && !title) return "Give the product a name to accept";
    if (recordPrice && !location) return "Choose the store for the posted price";
    return null;
  })();

  const edits = (): Record<string, unknown> => {
    const out: Record<string, unknown> = {};
    for (const [name, field] of Object.entries(fields)) {
      if (!field || typed[name] !== undefined) continue;
      const index = choices[name]?.index;
      // A choice other than the merge's, or one that settles a conflict, is the person's.
      if (index !== null && index !== undefined && (index !== 0 || field.conflict)) out[name] = candidates(field)[index].value;
    }
    for (const [name, value] of Object.entries(typed)) {
      if (name === "pack") {
        const m = value.trim().match(/^(\d+(?:\.\d+)?)\s*([a-zA-Z_ ]+)$/);
        if (m) out.pack = { qty: m[1], unit: m[2].trim().toLowerCase() };
      } else if (value.trim() !== "") out[name] = value.trim();
    }
    return out;
  };

  const ingredientId = async (): Promise<string | undefined> => {
    if (!ingredient) return undefined;
    if (ingredient.kind === "existing") return ingredient.ingredient.id;
    const body = ingredient.kind === "standard" ? { name: ingredient.name, standard_key: ingredient.key } : { name: ingredient.name };
    return (await api<Ingredient>("/ingredients", { method: "POST", body })).id;
  };

  const goNext = async (notice: { message: string; productId?: string }) => {
    const refreshed = await pending.refetch();
    const next = refreshed.data?.items.find((p) => p.id !== proposal.id);
    const action = notice.productId ? { label: "Open it", to: `/catalog/products/${notice.productId}` } : undefined;
    if (next) navigateWithNotice(`/catalog/products/review/${next.id}`, { tone: "success", message: notice.message, action });
    else if (notice.productId) navigateWithNotice(`/catalog/products/${notice.productId}`, { tone: "success", message: notice.message });
    else navigateWithNotice("/", { tone: "success", message: notice.message });
  };

  const doAccept = async (target: string | null = match) => {
    if (!target) return;
    setTaken(null);
    const input: AcceptInput = {
      action: target === "new" ? "new" : "update",
      product_id: target.startsWith("update:") ? target.slice("update:".length) : undefined,
      edits: edits(),
      main_photo_id: mainPhoto ?? undefined,
      photo_roles: roles,
      hidden_photo_ids: [...hidden],
      record_price: recordPrice,
      vendor_location_id: recordPrice ? (location ?? undefined) : undefined,
    };
    if (target === "new") {
      input.kind = kind;
      input.ingredient_id = await ingredientId();
    }
    accept.mutate(input, {
      onSuccess: (done) => {
        const name = title || "the product";
        void goNext({ message: `Added ${name}.`, productId: done.result?.product_id });
      },
      onError: (e) => setTaken(takenBy(e)),
    });
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey) && !blocker && !readOnly && !accept.isPending) {
      e.preventDefault();
      void doAccept();
    }
  };

  const heading = match?.startsWith("update:")
    ? `Update ${matchName(match, proposal, strongProduct.data?.name)}`
    : title
      ? `New product: ${title}`
      : "New product";
  const how = proposal.capture ? CHANNEL_WORDS[proposal.capture.channel] : "from the lookup helper";
  const when = proposal.capture?.captured_at ?? proposal.created_at;
  const evidence = productPhotos.find((p) => p.id === mainPhoto) ?? productPhotos[0] ?? proposal.photos[0];

  return (
    <div onKeyDown={onKeyDown} className="pb-24 lg:pb-0">
      <PageHeader title={heading} description={[vendor ? `From ${vendor.name}` : null, how[0].toUpperCase() + how.slice(1), formatDate(when)].filter(Boolean).join(" · ")} />
      <StatusNotice proposal={proposal} />
      <div className="flex flex-col gap-8 lg:grid lg:grid-cols-[20rem_1fr] lg:items-start">
        <aside aria-label="Evidence" className="flex flex-col gap-2 lg:sticky lg:top-4">
          {evidence ? <EvidencePhoto photo={evidence} /> : <div className={`flex h-30 items-center justify-center rounded-md bg-neutral-100 text-sm dark:bg-neutral-800 ${muted}`}>No photo</div>}
          {proposal.capture?.source_url ? <p className={`text-sm break-all ${muted}`}>{proposal.capture.source_url}</p> : null}
          <p className={`text-sm ${muted}`}>
            {how[0].toUpperCase() + how.slice(1)} · <time dateTime={when}>{formatDateTime(when)}</time>
          </p>
          {readingWords(proposal.reading) ? <p className={`text-sm ${muted}`}>{readingWords(proposal.reading)}</p> : null}
        </aside>

        <div className="flex min-w-0 flex-col gap-8">
          {reading && Object.keys(fields).length === 0 ? (
            <div role="status" className="flex flex-col gap-2">
              <p className={`text-sm ${muted}`}>{proposal.capture?.channel === "photo" ? "Reading this photo…" : "Reading this page…"}</p>
              {[0, 1, 2].map((i) => (
                <div key={i} className="h-5 w-2/3 animate-pulse rounded bg-neutral-200 dark:bg-neutral-800" />
              ))}
            </div>
          ) : null}
          {nothingRead && !readOnly ? <Alert tone="info">Nothing could be read from this photo. Fill in what you know.</Alert> : null}

          <fieldset ref={matchRef} disabled={readOnly}>
            <legend className={sectionHeading}>Match</legend>
            <div className="flex flex-col gap-1">
              {matchOptions(proposal, strongProduct.data?.name).map((o) => (
                <RadioRow key={o.value} name={`${id}-match`} checked={match === o.value} onChange={() => setMatch(o.value)}>
                  {o.label}
                  {o.note ? <span className={`ml-2 text-xs ${muted}`}>{o.note}</span> : null}
                </RadioRow>
              ))}
            </div>
          </fieldset>

          <section aria-labelledby={`${id}-summary`}>
            <h2 id={`${id}-summary`} className={sectionHeading}>
              Summary
            </h2>
            <p data-testid="review-summary" className="text-sm">
              {REVIEWED.map(([name]) => showValue(name, chosenValue(name))).join(" · ")}
            </p>
            {taken ? (
              <TakenPanel
                code={showValue("gtin", chosenValue("gtin"))}
                taken={taken}
                onUpdateInstead={(pid) => {
                  setMatch(`update:${pid}`);
                  void doAccept(`update:${pid}`);
                }}
                onKeepReviewing={() => setTaken(null)}
              />
            ) : null}
            {!editing && !readOnly ? (
              <Button variant="secondary" className="mt-2" onClick={() => setEditing(true)}>
                Edit details
              </Button>
            ) : null}
            {editing ? (
              <div className="mt-3 flex flex-col gap-4">
                {REVIEWED.map(([name, label]) => {
                  const field = fields[name];
                  if (field && (field.alternatives.length > 0 || field.conflict)) {
                    return (
                      <Alternatives
                        key={name}
                        name={name}
                        label={label}
                        field={field}
                        index={choices[name]?.index ?? null}
                        disabled={readOnly}
                        onChoose={(index) => {
                          setChoices({ ...choices, [name]: { index } });
                          const rest = { ...typed };
                          delete rest[name];
                          setTyped(rest);
                        }}
                      />
                    );
                  }
                  return (
                    <div key={name}>
                      <Field
                        id={`${id}-${name}`}
                        label={label}
                        disabled={readOnly}
                        value={typed[name] ?? (field ? showValue(name, field.value).replace(/^—$/, "") : "")}
                        onChange={(e) => setTyped({ ...typed, [name]: e.target.value })}
                        hint={field ? `${SOURCE_BADGES[field.source]}${field.source === "model" ? " · a guess" : ""}` : undefined}
                      />
                    </div>
                  );
                })}
              </div>
            ) : null}
          </section>

          {isNew ? (
            <section aria-labelledby={`${id}-ingredient`}>
              <h2 id={`${id}-ingredient`} className={sectionHeading}>
                Ingredient
              </h2>
              <IngredientPicker id={`${id}-ingredient-picker`} value={ingredient} onChange={setIngredient} suggestFrom={title} disabled={readOnly} />
            </section>
          ) : null}

          {proposal.photos.length > 0 ? (
            <fieldset disabled={readOnly}>
              <legend className={sectionHeading}>Images</legend>
              <ul className="grid grid-cols-2 gap-4 sm:grid-cols-3">
                {proposal.photos.map((photo) => (
                  <ReviewTile
                    key={photo.id}
                    photo={photo}
                    name={`${id}-main`}
                    role={roles[photo.id] ?? photo.role}
                    main={mainPhoto === photo.id}
                    hidden={hidden.has(photo.id)}
                    onMain={() => setMainPhoto(photo.id)}
                    onRole={(role) => setRoles({ ...roles, [photo.id]: role })}
                    onHidden={(h) => {
                      const next = new Set(hidden);
                      if (h) next.add(photo.id);
                      else next.delete(photo.id);
                      setHidden(next);
                    }}
                  />
                ))}
              </ul>
            </fieldset>
          ) : null}

          {isNew ? (
            <section aria-labelledby={`${id}-kind`}>
              <h2 id={`${id}-kind`} className={sectionHeading}>
                Kind
              </h2>
              <SegmentedControl label="Kind" options={KIND_OPTIONS} value={kind} onChange={setKind} />
            </section>
          ) : null}

          {proposal.price ? (
            <section aria-labelledby={`${id}-price`}>
              <h2 id={`${id}-price`} className={sectionHeading}>
                Price
              </h2>
              <p className="text-sm">
                Posted at {formatMoney(proposal.price.amount)}
                {proposal.price.is_promo ? " on sale" : ""}. Posted prices are kept, but not counted in cheapest.
              </p>
              {vendor && vendor.locations.length > 0 ? (
                <div className="mt-2 flex flex-col gap-2">
                  <label className="inline-flex min-h-11 items-center gap-2 text-sm lg:min-h-9">
                    <input type="checkbox" checked={recordPrice} disabled={readOnly} onChange={(e) => setRecordPrice(e.target.checked)} className={`size-4 ${focusRing}`} />
                    Record this posted price
                  </label>
                  {recordPrice ? (
                    <label className="flex flex-col gap-1 text-sm">
                      At which {vendor.name} store?
                      <select
                        value={location ?? ""}
                        disabled={readOnly}
                        onChange={(e) => setLocation(e.target.value || null)}
                        className={`min-h-11 rounded-md border border-neutral-300 bg-white px-2 lg:min-h-9 dark:border-neutral-700 dark:bg-neutral-900 ${focusRing}`}
                      >
                        <option value="">Choose a store</option>
                        {vendor.locations.map((l) => (
                          <option key={l.id} value={l.id}>
                            {l.name}
                          </option>
                        ))}
                      </select>
                    </label>
                  ) : null}
                </div>
              ) : (
                <p className="mt-2 rounded-md border border-amber-400 px-3 py-2 text-sm text-amber-900 dark:border-amber-600 dark:text-amber-200">
                  Not one of your stores, so no price is recorded.
                </p>
              )}
            </section>
          ) : null}

          {accept.isError && !taken ? <Alert tone="error">{errorMessage(accept.error)}</Alert> : null}
          {/* A task screen below lg has no tab bar, so it offers its own way out (10, PD17). */}
          <Link to="/" className={`inline-flex min-h-11 items-center self-start text-sm underline lg:hidden ${focusRing}`}>
            Cancel
          </Link>
          {reject.isError ? <Alert tone="error">{errorMessage(reject.error)}</Alert> : null}

          {readOnly ? null : (
            <div className="fixed inset-x-0 bottom-0 z-10 flex flex-wrap items-center gap-3 border-t border-neutral-200 bg-white px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] lg:sticky lg:bottom-4 lg:inset-x-auto lg:flex-nowrap lg:rounded-lg lg:border lg:pb-3 dark:border-neutral-800 dark:bg-neutral-900">
              {blocker ? (
                <span className={`basis-full text-sm lg:min-w-0 lg:flex-1 lg:basis-auto ${muted}`}>{blocker}</span>
              ) : (
                <span className={`hidden text-xs lg:inline lg:flex-1 ${muted}`}>Ctrl or ⌘ + Enter accepts</span>
              )}
              <Button
                variant="secondary"
                className="min-h-14 flex-1 lg:min-h-10 lg:flex-none"
                disabled={reject.isPending || accept.isPending}
                onClick={() => reject.mutate(undefined, { onSuccess: () => void goNext({ message: "Rejected. The capture is kept." }) })}
              >
                Reject
              </Button>
              <Button className="min-h-14 flex-1 lg:min-h-10 lg:flex-none" disabled={Boolean(blocker) || accept.isPending} onClick={() => void doAccept()}>
                {accept.isPending ? "Accepting…" : "Accept"}
              </Button>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function matchName(match: string, proposal: Proposal, strongName: string | undefined): string {
  const pid = match.slice("update:".length);
  if (proposal.match.strong?.product_id === pid && strongName) return strongName;
  return proposal.match.candidates?.find((c) => c.product_id === pid)?.name ?? "product";
}

function matchOptions(proposal: Proposal, strongName: string | undefined) {
  const out: { value: string; label: string; note?: string }[] = [];
  const strong = proposal.match.strong;
  if (strong) out.push({ value: `update:${strong.product_id}`, label: `Update ${strongName ?? "the matching product"}`, note: strong.reason === "identifier" ? "Same barcode" : "Same store page" });
  for (const c of proposal.match.candidates ?? []) {
    if (c.product_id === strong?.product_id) continue;
    out.push({ value: `update:${c.product_id}`, label: `Update ${c.name}${c.brand ? ` (${c.brand})` : ""}`, note: "Similar name" });
  }
  out.push({ value: "new", label: "Create new product" });
  return out;
}

function RadioRow({ name, checked, onChange, children, outline }: { name: string; checked: boolean; onChange: () => void; children: ReactNode; outline?: boolean }) {
  return (
    <label
      className={`flex min-h-11 cursor-pointer items-center gap-3 rounded-md px-3 py-2 text-sm hover:bg-neutral-50 lg:min-h-10 dark:hover:bg-neutral-800 ${
        outline ? "border border-amber-400 dark:border-amber-600" : ""
      } ${checked ? "bg-neutral-100 dark:bg-neutral-800" : ""}`}
    >
      <input type="radio" name={name} checked={checked} onChange={onChange} className={`size-4 ${focusRing}`} />
      <span className="min-w-0">{children}</span>
    </label>
  );
}

function Alternatives({
  name,
  label,
  field,
  index,
  disabled,
  onChoose,
}: {
  name: string;
  label: string;
  field: ProposalField;
  index: number | null;
  disabled: boolean;
  onChoose: (index: number) => void;
}) {
  const id = useId();
  return (
    <fieldset disabled={disabled} className={field.conflict ? "rounded-md border border-amber-400 p-3 dark:border-amber-600" : ""}>
      <legend className="text-sm font-medium">{label}</legend>
      {field.conflict ? <p className="mb-1 text-sm text-amber-900 dark:text-amber-200">These disagree. Choose one.</p> : null}
      <div className="flex flex-col gap-1">
        {candidates(field).map((c, i) => (
          <RadioRow key={`${name}-${i}`} name={`${id}-${name}`} checked={index === i} onChange={() => onChoose(i)} outline={c.source === "model"}>
            <span className="mr-2">{showValue(name, c.value)}</span>
            <Badge>{SOURCE_BADGES[c.source]}</Badge>
          </RadioRow>
        ))}
      </div>
    </fieldset>
  );
}

function TakenPanel({ code, taken, onUpdateInstead, onKeepReviewing }: { code: string; taken: { product_id: string; name: string | null }; onUpdateInstead: (id: string) => void; onKeepReviewing: () => void }) {
  const name = taken.name ?? "another product";
  return (
    <div role="alert" className="mt-2 flex flex-col gap-2 rounded-md border border-amber-400 px-3 py-2 text-sm text-amber-900 dark:border-amber-600 dark:text-amber-200">
      <p>
        Barcode {code} already belongs to{" "}
        <Link to={`/catalog/products/${taken.product_id}`} className={`rounded font-medium underline ${focusRing}`}>
          {name}
        </Link>
        .
      </p>
      <span className="flex flex-wrap gap-2">
        <Button variant="secondary" onClick={() => onUpdateInstead(taken.product_id)}>
          Update {name} instead
        </Button>
        <Button variant="secondary" onClick={onKeepReviewing}>
          Keep reviewing
        </Button>
      </span>
    </div>
  );
}

function EvidencePhoto({ photo }: { photo: ProductPhoto }) {
  if (!photo.urls) {
    return (
      <div role="status" className={`flex aspect-square w-full items-center justify-center rounded-md bg-neutral-100 text-sm dark:bg-neutral-800 ${muted}`}>
        {photo.status === "failed" ? "Couldn't process this photo" : "Preparing photo…"}
      </div>
    );
  }
  const src = photo.has_cutout && photo.urls.cutout_medium ? photo.urls.cutout_medium : photo.urls.medium;
  return (
    <img
      src={src}
      alt="The captured product"
      className={`aspect-square w-full max-lg:max-h-30 ${photo.has_cutout ? "object-contain cutout-shadow" : "rounded-md bg-neutral-100 object-contain dark:bg-neutral-800"}`}
    />
  );
}

function ReviewTile({
  photo,
  name,
  role,
  main,
  hidden,
  onMain,
  onRole,
  onHidden,
}: {
  photo: ProductPhoto;
  name: string;
  role: PhotoRole;
  main: boolean;
  hidden: boolean;
  onMain: () => void;
  onRole: (role: PhotoRole) => void;
  onHidden: (hidden: boolean) => void;
}) {
  const id = useId();
  const picture = photo.urls ? (
    <img src={photo.urls.small} alt={`${ROLE_LABELS[role]} photo`} className={`aspect-square w-full rounded-md bg-neutral-100 object-cover dark:bg-neutral-800 ${hidden ? "opacity-50" : ""}`} />
  ) : (
    <span role="status" className={`flex aspect-square w-full items-center justify-center rounded-md bg-neutral-100 p-2 text-center text-sm dark:bg-neutral-800 ${muted}`}>
      {photo.status === "failed" ? "Couldn't process this photo" : "Preparing photo…"}
    </span>
  );
  return (
    <li data-testid="review-tile" className={`flex flex-col gap-2 rounded-md p-1 ${main ? "border-2 border-neutral-500 dark:border-neutral-400" : "border-2 border-transparent"}`}>
      {picture}
      {role === "product" ? (
        <label className="inline-flex min-h-11 items-center gap-2 text-sm lg:min-h-9">
          <input type="radio" name={name} checked={main} onChange={onMain} className={`size-4 ${focusRing}`} />
          {main ? "Main photo" : "Use as main photo"}
        </label>
      ) : null}
      <label className="sr-only" htmlFor={`${id}-role`}>
        What this photo shows
      </label>
      <select
        id={`${id}-role`}
        value={role}
        onChange={(e) => onRole(e.target.value as PhotoRole)}
        className={`min-h-11 rounded-md border border-neutral-300 bg-white px-2 text-sm lg:min-h-9 dark:border-neutral-700 dark:bg-neutral-900 ${focusRing}`}
      >
        {ROLE_OPTIONS.map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </select>
      <label className="inline-flex min-h-11 items-center gap-2 text-sm lg:min-h-9">
        <input type="checkbox" checked={hidden} onChange={(e) => onHidden(e.target.checked)} className={`size-4 ${focusRing}`} />
        Hide
      </label>
    </li>
  );
}

function StatusNotice({ proposal }: { proposal: Proposal }) {
  const product = useProduct(proposal.status === "accepted" ? (proposal.product_id ?? undefined) : undefined);
  if (proposal.status === "pending") return null;
  const when = proposal.decided_at ? formatDate(proposal.decided_at) : "";
  if (proposal.status === "superseded") {
    const newer = proposal.result?.superseded_by;
    return (
      <Alert tone="info" className="mb-4">
        A newer capture replaced this
        {newer ? (
          <>
            {" · "}
            <Link to={`/catalog/products/review/${newer}`} className={`rounded font-medium underline ${focusRing}`}>
              Open it
            </Link>
          </>
        ) : null}
      </Alert>
    );
  }
  if (proposal.status === "accepted") {
    return (
      <Alert tone="info" className="mb-4">
        Accepted {when}
        {proposal.product_id ? (
          <>
            {" · "}
            <Link to={`/catalog/products/${proposal.product_id}`} className={`rounded font-medium underline ${focusRing}`}>
              Open {product.data?.name ?? "the product"}
            </Link>
          </>
        ) : null}
      </Alert>
    );
  }
  return (
    <Alert tone="info" className="mb-4">
      Rejected {when}. The capture is kept.
    </Alert>
  );
}
