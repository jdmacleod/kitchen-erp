import { useEffect, useId, useMemo, useRef, useState, type FormEvent, type KeyboardEvent, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router";
import { errorMessage } from "../../api/client";
import { formatPack, isPositiveDecimal, productTitle, useProductSearch, type SearchHit } from "../../api/catalog";
import type { VendorLocation } from "../../api/geo";
import { useProductPrices } from "../../api/pricebook";
import { purchaseErrorMessage, useCreateObservation, useObservations, type Observation, type ObservationCreateInput } from "../../api/purchases";
import { Combobox } from "../../components/catalog/Combobox";
import { HitRow } from "../../components/catalog/ProductTypeahead";
import { UnitSelect } from "../../components/catalog/UnitSelect";
import { useNavigateWithNotice, useNotice, type NoticeData } from "../../components/Notice";
import { Drawer } from "../../components/Drawer";
import { LocationGuard } from "../../components/purchases/LocationGuard";
import { locationLabel, rememberLocation } from "../../components/purchases/LocationSelect";
import { InlineProductCreate, productRefFromHit, type ProductRef } from "../../components/purchases/ProductPicker";
import { StoreChip, useStoreGuess } from "../../components/purchases/StoreChip";
import { Alert, Button, Card, Field, PageHeader, focusRing } from "../../components/ui";
import { cmp, formatMoney, isNonNegativeDecimal, stripZeros } from "../../lib/decimal";
import { formatDate } from "../../lib/format";
import { fromDateTimeLocal, toDateTimeLocal } from "../../lib/openingHours";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { usePageTitle } from "../../lib/usePageTitle";

/** What Capture, or any other opener, hands this page in router state. */
export interface ShelfPriceState {
  /** Where plain Save returns to (G4). */
  from?: string;
  /** A store the person chose before arriving, e.g. in the Capture sheet. */
  locationId?: string;
}

/**
 * What reads as a barcode someone scanned or typed, for offering to create a
 * product with it: EAN-8 to GTIN-14. Matching needs no such rule; any exact
 * barcode hit is taken, whatever its shape.
 */
const BARCODE = /^\d{8,14}$/;

const RECENT_COUNT = 5;
/** How many pages of this store's observations recents will read, 50 each. */
const RECENT_PAGES = 10;

/**
 * The shelf price page (docs/spec/10, Shelf price): below lg a task screen with
 * its own Back and a thumb-zone footer. Plain Save returns to where Capture was
 * opened, or Home.
 */
export function ShelfPricePage() {
  usePageTitle("Shelf price");
  const navigate = useNavigate();
  const navigateWithNotice = useNavigateWithNotice();
  const routerLocation = useLocation();
  // Read once: the state belongs to the arrival, not to later renders.
  const [arrival] = useState(() => (routerLocation.state as ShelfPriceState | null) ?? {});

  // Back is Back when there is somewhere to go back to in this app.
  const goBack = () => {
    if ((window.history.state as { idx?: number } | null)?.idx) navigate(-1);
    else navigate(arrival.from ?? "/");
  };

  return (
    <>
      {/* Below lg this is a task screen: no tab bar, so it carries its own way back. */}
      <div className="-mt-3 mb-1 lg:hidden">
        <button
          type="button"
          onClick={goBack}
          aria-label="Back"
          className={`-ml-3 inline-flex size-11 items-center justify-center rounded-md text-neutral-800 dark:text-neutral-200 ${focusRing}`}
        >
          <svg viewBox="0 0 24 24" className="size-6" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
            <path d="M15 6l-6 6 6 6" />
          </svg>
        </button>
      </div>
      <PageHeader title="Shelf price" description="A price you saw on the shelf, without buying.">
        <Link to="/shop/purchases/new" className={`rounded text-sm underline ${focusRing}`}>
          Enter a purchase instead
        </Link>
      </PageHeader>
      <LocationGuard>
        <ShelfPriceForm arrival={arrival} variant="page" onSaved={(notice) => navigateWithNotice(arrival.from ?? "/", notice)} />
      </LocationGuard>
    </>
  );
}

/**
 * The shelf price at lg and wider, opened from Capture over the page it was
 * opened on (G14). Plain Save closes it and shows the notice on that page;
 * typed input asks before it is discarded (D5).
 */
export function ShelfPriceDrawer({ arrival, onClose }: { arrival: ShelfPriceState; onClose: () => void }) {
  const notice = useNotice();
  const formId = useId();
  const [state, setState] = useState({ dirty: false, busy: false });
  return (
    <Drawer
      title="Log a shelf price"
      thing="shelf price"
      dirty={state.dirty}
      onClose={onClose}
      formId={formId}
      primaryLabel="Save price"
      busy={state.busy}
      busyLabel="Saving…"
      secondaryAction={
        <Button type="submit" form={formId} variant="secondary" data-action="another" disabled={state.busy}>
          Save and scan another
        </Button>
      }
    >
      <LocationGuard>
        <ShelfPriceForm
          arrival={arrival}
          variant="drawer"
          formId={formId}
          onStateChange={setState}
          onSaved={(saved) => {
            onClose();
            notice.show(saved);
          }}
        />
      </LocationGuard>
    </Drawer>
  );
}

interface ShelfPriceFormProps {
  arrival: ShelfPriceState;
  /** The page carries its own thumb-zone footer; the drawer's footer is the Drawer's. */
  variant: "page" | "drawer";
  formId?: string;
  /** Plain Save succeeded: what to say, wherever the person lands. */
  onSaved: (notice: NoticeData) => void;
  onStateChange?: (state: { dirty: boolean; busy: boolean }) => void;
}

/**
 * Log a shelf price (docs/spec/10, Shelf price; G2–G4, UI-4.4–4.7). One field
 * takes a barcode or a product name; before typing it offers the last five
 * products logged at this store. Enter saves and readies the next tag.
 */
function ShelfPriceForm({ arrival, variant, formId, onSaved, onStateChange }: ShelfPriceFormProps) {
  const notice = useNotice();
  const create = useCreateObservation();

  const { guess, skip, locations } = useStoreGuess();
  const [chosenId, setChosenId] = useState<string | null>(arrival.locationId ?? null);
  const chosen = chosenId ? (locations.find((l) => l.id === chosenId) ?? null) : null;
  const guessed = guess.status === "near" || guess.status === "last" ? guess.location : null;
  const store: VendorLocation | null = chosen ?? (chosenId ? null : guessed);
  const source = chosen ? "chosen" : chosenId && locations.length === 0 ? "finding" : guess.status;

  const [product, setProduct] = useState<ProductRef | null>(null);
  const [creating, setCreating] = useState<{ barcode?: string; name?: string } | null>(null);
  const [price, setPrice] = useState("");
  const [promo, setPromo] = useState(false);
  const [qty, setQty] = useState("1");
  const [unit, setUnit] = useState("each");
  const [observedAt, setObservedAt] = useState(() => toDateTimeLocal(new Date()));
  const [observedTouched, setObservedTouched] = useState(false);
  const [invalid, setInvalid] = useState<string | null>(null);
  // A saved price that could not be normalized says what is missing (the bridge).
  const [unpriced, setUnpriced] = useState<Observation | null>(null);

  const entryRef = useRef<HTMLInputElement>(null);
  const priceRef = useRef<HTMLInputElement>(null);
  const focusNext = useRef<"entry" | "price" | null>(null);
  useEffect(() => {
    if (!focusNext.current) return;
    (focusNext.current === "entry" ? entryRef : priceRef).current?.focus();
    focusNext.current = null;
  });

  const pick = (p: ProductRef) => {
    setProduct(p);
    setCreating(null);
    setInvalid(null);
    // One "each" is one pack; loose goods change the amount under Details.
    setQty("1");
    setUnit("each");
    focusNext.current = "price";
  };

  const clearProduct = () => {
    setProduct(null);
    setPrice("");
    setPromo(false);
    setQty("1");
    setUnit("each");
    focusNext.current = "entry";
  };

  const validate = (): ObservationCreateInput | null => {
    if (!store) return fail("Choose the store.");
    if (!product) return fail("Scan or type a product first.");
    if (price.trim() === "" || !isNonNegativeDecimal(price)) return fail("Enter the price on the shelf.");
    if (!isPositiveDecimal(qty)) return fail("The amount must be a positive number.");
    if (!unit) return fail("Choose a unit.");
    const at = observedTouched ? fromDateTimeLocal(observedAt) : undefined;
    if (observedTouched && !at) return fail("Enter a valid time.");
    setInvalid(null);
    const input: ObservationCreateInput = { product_id: product.id, vendor_location_id: store.id, price: price.trim(), qty: qty.trim(), unit };
    if (promo) input.is_promo = true;
    if (at) input.observed_at = at;
    return input;
  };
  function fail(message: string): null {
    setInvalid(message);
    return null;
  }

  const save = (another: boolean) => {
    if (create.isPending) return;
    const input = validate();
    if (!input || !store) return;
    const vendor = locationLabel(store);
    create.mutate(input, {
      onSuccess: (observation) => {
        rememberLocation(store.id);
        const message = `Saved ${formatMoney(observation.price)} at ${vendor}`;
        const normalized = observation.norm === null || observation.norm.status === "ok";
        if (!another) {
          // Back to where Capture was opened; with nowhere recorded, Home. A price
          // that could not be normalized says what is missing, and links to it.
          const fix = normalized ? null : bridgeFix(observation);
          onSaved(fix ? { tone: "info", message: `${message}. ${fix.reason}`, action: { label: fix.label, to: fix.to } } : { tone: "success", message });
          return;
        }
        // The store stays; everything about the last tag goes (G4).
        setUnpriced(normalized ? null : observation);
        clearProduct();
        if (!observedTouched) setObservedAt(toDateTimeLocal(new Date()));
        notice.show({ tone: "success", message, timeoutMs: 3000 });
      },
    });
  };

  // Enter anywhere in the form is the stream action: save and ready the next tag.
  // In the drawer, its footer's Save price submits too, and means plain Save;
  // the button that submitted says which (Enter uses the first, "another").
  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const submitter = (event.nativeEvent as SubmitEvent).submitter as HTMLElement | null;
    save(variant === "page" || submitter?.dataset.action === "another");
  };
  // Except in the entry field: a wedge scanner ends every barcode with Enter,
  // which arrives before the lookup does. The match is taken when it lands.
  const onKeyDown = (event: KeyboardEvent<HTMLFormElement>) => {
    if (event.key !== "Enter" || event.isDefaultPrevented()) return;
    const target = event.target as HTMLElement;
    if (target.getAttribute("role") === "combobox") {
      event.preventDefault();
      return;
    }
    // In the drawer the submit buttons sit in the Drawer's footer, outside this
    // form's markup, so Enter in a field is handled here rather than left to
    // implicit submission.
    if (variant === "drawer" && target.tagName === "INPUT" && (target as HTMLInputElement).type !== "checkbox") {
      event.preventDefault();
      save(true);
    }
  };

  const busy = create.isPending;
  const pack = product ? formatPack(product.pack_qty, product.pack_unit) : "";
  const dirty = product !== null || creating !== null || price.trim() !== "";
  useEffect(() => {
    onStateChange?.({ dirty, busy });
  }, [dirty, busy, onStateChange]);

  return (
        <form
          id={formId}
          onSubmit={onSubmit}
          onKeyDown={onKeyDown}
          aria-label="Log a shelf price"
          noValidate
          className={`flex flex-col gap-4 ${variant === "page" ? "pb-36 lg:max-w-xl lg:pb-0" : ""}`}
        >
          <StoreChip
            value={store}
            source={source}
            locations={locations}
            onChoose={(l) => setChosenId(l.id)}
            onSkip={skip}
            disabled={busy}
          />

          {invalid ? <Alert tone="error">{invalid}</Alert> : null}
          {create.error ? <Alert tone="error">{purchaseErrorMessage(create.error)}</Alert> : null}
          {unpriced ? <ObservationResult observation={unpriced} /> : null}

          {product ? (
            <ProductCard product={product} onChange={clearProduct} disabled={busy} />
          ) : creating ? (
            <InlineProductCreate
              id="shelf-new-product"
              initialBarcode={creating.barcode}
              initialName={creating.name}
              onCreated={pick}
              onCancel={() => {
                setCreating(null);
                focusNext.current = "entry";
              }}
              disabled={busy}
            />
          ) : (
            <ProductEntry storeId={store?.id} storeName={store ? locationLabel(store) : null} inputRef={entryRef} onPick={pick} onCreate={setCreating} disabled={busy} />
          )}

          {product ? (
            <>
              <div className="flex flex-col gap-2">
                <label htmlFor="shelf-price" className="text-sm font-medium">
                  Price on the shelf
                </label>
                <div className="flex h-[4.5rem] items-center gap-1 rounded-2xl border-2 border-neutral-300 bg-white px-4 focus-within:border-blue-600 dark:border-neutral-700 dark:bg-neutral-900 dark:focus-within:border-blue-400">
                  <span aria-hidden="true" className="font-display text-3xl text-neutral-600 dark:text-neutral-400">
                    $
                  </span>
                  <input
                    ref={priceRef}
                    id="shelf-price"
                    type="text"
                    inputMode="decimal"
                    enterKeyHint="done"
                    autoComplete="off"
                    value={price}
                    onChange={(e) => setPrice(e.target.value)}
                    disabled={busy}
                    className="font-display w-full min-w-0 bg-transparent text-4xl font-semibold tabular-nums outline-none"
                  />
                  <span className="shrink-0 text-sm text-neutral-600 dark:text-neutral-400">
                    {stripZeros(qty) === "1" && unit === "each" ? "each" : `for ${stripZeros(qty)} ${unit}`}
                  </span>
                </div>
                <label className="inline-flex min-h-11 items-center gap-2.5 self-start text-base">
                  <input type="checkbox" checked={promo} onChange={(e) => setPromo(e.target.checked)} disabled={busy} className={`size-5 ${focusRing}`} />
                  On sale
                </label>
              </div>

              <PriceContext productId={product.id} store={store} />

              <details className="text-sm">
                <summary className={`group inline-flex min-h-11 cursor-pointer list-none items-center gap-1 rounded-md [&::-webkit-details-marker]:hidden ${focusRing}`}>
                  <svg viewBox="0 0 24 24" className="size-4 transition-transform group-open:rotate-90" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true">
                    <path d="M9 6l6 6-6 6" />
                  </svg>
                  Amount and time
                </summary>
                <div className="mt-2 grid gap-4 sm:grid-cols-2">
                  <Field id="shelf-qty" label="Amount" inputMode="decimal" autoComplete="off" value={qty} onChange={(e) => setQty(e.target.value)} disabled={busy} />
                  <UnitSelect id="shelf-unit" label="Unit" value={unit} onChange={setUnit} disabled={busy} hint={pack ? `1 each = one ${pack} pack.` : undefined} />
                  <Field
                    id="shelf-observed-at"
                    label="Seen at"
                    type="datetime-local"
                    value={observedAt}
                    onChange={(e) => {
                      setObservedAt(e.target.value);
                      setObservedTouched(true);
                    }}
                    disabled={busy}
                    hint={observedTouched ? undefined : "Now, unless you change it."}
                  />
                </div>
              </details>
            </>
          ) : null}

          {/* The thumb zone (G4): pinned to the bottom edge below lg, where this
              task screen has no tab bar. The form's bottom padding keeps the last
              field clear of it. The drawer has its own footer. */}
          {variant === "page" ? (
          <div className="fixed inset-x-0 bottom-0 z-20 flex flex-col gap-1 border-t border-neutral-200 bg-neutral-50 px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] md:px-8 lg:static lg:flex-row lg:gap-2 lg:border-0 lg:bg-transparent lg:px-0 lg:pt-2 lg:pb-0 dark:border-neutral-800 dark:bg-neutral-950 lg:dark:bg-transparent">
            <Button onClick={() => save(false)} disabled={busy} className="min-h-14 w-full rounded-2xl text-base lg:min-h-10 lg:w-auto lg:rounded-md lg:text-sm">
              {busy ? "Saving…" : "Save price"}
            </Button>
            {/* An action, so herb text; not the ghost button, whose neutral text would win. */}
            <button
              type="submit"
              disabled={busy}
              className={`inline-flex min-h-11 w-full items-center justify-center rounded-md px-3 text-base font-medium text-blue-700 hover:bg-neutral-200 disabled:opacity-50 lg:min-h-10 lg:w-auto lg:text-sm dark:text-blue-300 dark:hover:bg-neutral-800 ${focusRing}`}
            >
              Save and scan another
            </button>
          </div>
          ) : null}
        </form>
  );
}

function ProductCard({ product, onChange, disabled }: { product: ProductRef; onChange: () => void; disabled?: boolean }) {
  const pack = formatPack(product.pack_qty, product.pack_unit);
  return (
    <Card>
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0" data-testid="shelf-product-choice">
          <p className="text-lg font-semibold">{productTitle(product)}</p>
          <p className="text-sm text-neutral-600 dark:text-neutral-400">{[product.ingredient?.name, pack].filter(Boolean).join(" · ")}</p>
        </div>
        <Button variant="ghost" onClick={onChange} disabled={disabled} className="shrink-0">
          Change
        </Button>
      </div>
    </Card>
  );
}

interface ProductEntryProps {
  storeId: string | undefined;
  storeName: string | null;
  inputRef: React.RefObject<HTMLInputElement | null>;
  onPick: (product: ProductRef) => void;
  onCreate: (seed: { barcode?: string; name?: string }) => void;
  disabled?: boolean;
}

/**
 * The one entry field (G3): a barcode or a product name, ranked by search. An
 * exact barcode match is taken at once, as a wedge scanner expects; a barcode no
 * product has offers to create one with it filled in (UI-4.7).
 */
function ProductEntry({ storeId, storeName, inputRef, onPick, onCreate, disabled }: ProductEntryProps) {
  const [text, setText] = useState("");
  const typed = text.trim();
  const debounced = useDebouncedValue(text, 150);
  const search = useProductSearch(debounced, 8);
  const current = typed !== "" && debounced.trim() === typed && !search.isPlaceholderData && search.isSuccess;
  // Only results for the text in the field: while a new lookup is pending the
  // last one's hits are for other words, and a tap on one would log the wrong
  // product.
  const hits = current ? (search.data ?? []) : [];
  const isBarcode = BARCODE.test(typed);
  const barcodeHit = hits.find((h) => h.match === "barcode" && h.barcode === typed);
  // No product has this barcode: offer to create one, even beside name matches.
  const unknownBarcode = current && isBarcode && !barcodeHit;

  // A scanned barcode that matches is the product; there is nothing to choose.
  // Taken once per hit, however often the parent re-renders before this unmounts.
  const taken = useRef<string | null>(null);
  useEffect(() => {
    if (!barcodeHit || taken.current === barcodeHit.id) return;
    taken.current = barcodeHit.id;
    onPick(productRefFromHit(barcodeHit));
  }, [barcodeHit, onPick]);

  let status: ReactNode;
  if (typed && !search.isError) {
    if (!current) status = "Looking up…";
    else if (hits.length === 0) status = "No products match.";
  }

  return (
    <div className="flex flex-col gap-3">
      <Combobox<SearchHit>
        id="shelf-product"
        label="Barcode or product name"
        placeholder="Scan or type"
        listLabel="Products"
        inputValue={text}
        onInputChange={setText}
        items={barcodeHit ? [] : hits}
        getKey={(hit) => hit.id}
        status={status}
        disabled={disabled}
        inputRef={inputRef}
        onSelect={(hit) => {
          setText("");
          onPick(productRefFromHit(hit));
        }}
        renderItem={(hit) => <HitRow hit={hit} />}
      />

      {typed && search.isError ? (
        <div role="alert" className="flex flex-wrap items-center justify-between gap-2 text-sm">
          <span>Couldn&apos;t look that up: {errorMessage(search.error)}</span>
          <Button variant="secondary" onClick={() => void search.refetch()}>
            Retry
          </Button>
        </div>
      ) : unknownBarcode || (current && hits.length === 0) ? (
        <div className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700">
          <span>{isBarcode ? `No product has barcode ${typed}.` : `Nothing called “${typed}” yet.`}</span>
          <Button variant="secondary" onClick={() => onCreate(isBarcode ? { barcode: typed } : { name: typed })}>
            Create product
          </Button>
        </div>
      ) : null}

      {!typed ? <RecentAtStore storeId={storeId} storeName={storeName} onPick={onPick} /> : null}
    </div>
  );
}

/** Before typing: the last five products logged at this store, newest first (G3). */
function RecentAtStore({ storeId, storeName, onPick }: { storeId: string | undefined; storeName: string | null; onPick: (p: ProductRef) => void }) {
  const observations = useObservations({ vendor_location_id: storeId }, 50, Boolean(storeId));
  const pages = observations.data?.pages ?? [];
  const recent = useMemo(() => {
    const seen = new Set<string>();
    const out: ProductRef[] = [];
    for (const o of pages.flatMap((p) => p.items)) {
      if (seen.has(o.product.id)) continue;
      seen.add(o.product.id);
      const { id, name, brand, pack_qty, pack_unit, ingredient } = o.product;
      out.push({ id, name, brand, pack_qty, pack_unit, ingredient: { id: ingredient.id, name: ingredient.name, canonical_unit: ingredient.canonical_unit } });
      if (out.length === RECENT_COUNT) break;
    }
    return out;
  }, [pages]);

  // One product logged fifty times would otherwise hide the four before it:
  // read further back until there are five, within a bound.
  const { hasNextPage, isFetchingNextPage, fetchNextPage } = observations;
  useEffect(() => {
    if (recent.length < RECENT_COUNT && hasNextPage && !isFetchingNextPage && pages.length < RECENT_PAGES) void fetchNextPage();
  }, [recent.length, hasNextPage, isFetchingNextPage, pages.length, fetchNextPage]);

  if (!storeId || observations.isPending || observations.isError || recent.length === 0) return null;
  return (
    <section aria-labelledby="shelf-recent">
      <h2 id="shelf-recent" className="mb-1 text-sm font-medium text-neutral-600 dark:text-neutral-400">
        Recently logged at {storeName}
      </h2>
      <ul className="flex flex-col">
        {recent.map((p) => (
          <li key={p.id}>
            <button
              type="button"
              onClick={() => onPick(p)}
              className={`flex min-h-11 w-full items-center justify-between gap-3 rounded-md px-2 text-left text-sm hover:bg-neutral-100 dark:hover:bg-neutral-800 ${focusRing}`}
            >
              <span className="min-w-0 truncate font-medium">{productTitle(p)}</span>
              <span className="shrink-0 text-neutral-600 dark:text-neutral-400">{formatPack(p.pack_qty, p.pack_unit)}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** "Last paid here" and "Best known" for the matched product (G3). */
function PriceContext({ productId, store }: { productId: string; store: VendorLocation | null }) {
  const prices = useProductPrices(productId);
  let lastPaid: ReactNode = "—";
  let best: ReactNode = "—";
  if (prices.isPending) {
    lastPaid = best = "…";
  } else if (prices.isError) {
    lastPaid = best = "Couldn't load";
  } else {
    // Paid means bought: a shelf price someone only saw is not what they paid.
    const paid = prices.data.points.filter((p) => p.location_id === store?.id && p.source !== "shelf").at(-1);
    if (paid) lastPaid = `${formatMoney(paid.price)} · ${formatDate(paid.observed_at)}`;
    // Stale offers are left out: an old low price is not what the shelf costs now.
    const cheapest = prices.data.latest
      .filter((l) => l.norm_unit_price !== null && !l.stale)
      .reduce<(typeof prices.data.latest)[number] | null>((low, l) => (low === null || cmp(l.norm_unit_price!, low.norm_unit_price!) < 0 ? l : low), null);
    if (cheapest) best = `${formatMoney(cheapest.price)} · ${cheapest.vendor_name}`;
  }
  return (
    <Card>
      <dl className="-my-2 divide-y divide-neutral-200 text-sm dark:divide-neutral-800">
        <div className="flex min-h-11 items-center justify-between gap-3 py-2">
          <dt className="shrink-0 text-neutral-600 dark:text-neutral-400">Last paid here</dt>
          <dd className="min-w-0 text-right font-semibold tabular-nums">{lastPaid}</dd>
        </div>
        <div className="flex min-h-11 items-center justify-between gap-3 py-2">
          <dt className="shrink-0 text-neutral-600 dark:text-neutral-400">Best known</dt>
          <dd className="min-w-0 text-right font-semibold tabular-nums">{best}</dd>
        </div>
      </dl>
    </Card>
  );
}

/** What a price that could not be normalized is missing, in one sentence, and where to add it. */
export function bridgeFix(observation: Observation): { reason: string; label: string; to: string } | null {
  const { product, norm } = observation;
  if (!norm || norm.status === "ok") return null;
  const ingredient = `/catalog/ingredients/${product.ingredient.id}`;
  switch (norm.status) {
    case "no_density":
      return { reason: `It can't be compared yet: ${product.ingredient.name} has no density.`, label: "Add a density", to: `${ingredient}#density-heading` };
    case "unknown_measure":
      return { reason: `It can't be compared yet: ${product.ingredient.name} has no measure named “${observation.unit}”.`, label: "Add the measure", to: `${ingredient}#measures-heading` };
    case "no_pack":
      return { reason: `It can't be compared yet: ${productTitle(product)} has no pack size.`, label: "Set the pack", to: `/catalog/products/${product.id}` };
    default:
      return { reason: "It can't be compared yet: the quantity could not be used.", label: "Check the product", to: `/catalog/products/${product.id}` };
  }
}

/** What normalization made of the observation, or what it is missing and where to fix it. */
export function ObservationResult({ observation }: { observation: Observation }) {
  const { product, norm } = observation;
  const recorded = (
    <>
      Recorded {formatMoney(observation.price)} for {stripZeros(observation.qty)} {observation.unit} of{" "}
      <Link to={`/catalog/products/${product.id}`} className={`rounded font-medium underline ${focusRing}`}>
        {productTitle(product)}
      </Link>
      {observation.is_promo ? " (sale)" : ""}.
    </>
  );

  if (!norm) {
    return (
      <Alert tone="success">
        {recorded} Normalization is pending.
      </Alert>
    );
  }

  if (norm.status === "ok") {
    const bridge = norm.bridge_kind && norm.bridge_kind !== "none" ? ` via ${norm.bridge_kind}${norm.bridge_source ? ` (${norm.bridge_source}${norm.bridge_confirmed === false ? ", unconfirmed" : ""})` : ""}` : "";
    return (
      <Alert tone="success">
        {recorded}{" "}
        <span data-testid="norm-price" className="font-medium">
          {formatMoney(norm.norm_unit_price, 2, 6)} per {norm.norm_unit}
        </span>
        {bridge}.
      </Alert>
    );
  }

  const ingredientLink = (hash: string, text: string) => (
    <Link to={`/catalog/ingredients/${product.ingredient.id}#${hash}`} className={`rounded font-medium underline ${focusRing}`}>
      {text}
    </Link>
  );
  const productLink = (text: string) => (
    <Link to={`/catalog/products/${product.id}`} className={`rounded font-medium underline ${focusRing}`}>
      {text}
    </Link>
  );

  let missing: ReactNode;
  switch (norm.status) {
    case "no_density":
      missing = (
        <>
          {product.ingredient.name} has no density, so {observation.unit} cannot be turned into {product.ingredient.canonical_unit}.{" "}
          {ingredientLink("density-heading", "Add a density")}.
        </>
      );
      break;
    case "unknown_measure":
      missing = (
        <>
          {product.ingredient.name} has no measure named “{observation.unit}”. {ingredientLink("measures-heading", "Add the measure")}.
        </>
      );
      break;
    case "no_pack":
      missing = <>{productTitle(product)} has no pack size, so “each” cannot be priced per {product.ingredient.canonical_unit}. {productLink("Set the pack")}.</>;
      break;
    default:
      missing = <>The quantity could not be used. {productLink("Check the product")}.</>;
  }

  return (
    <Alert tone="info">
      {recorded} Not yet priced per {product.ingredient.canonical_unit}: {missing}
    </Alert>
  );
}
