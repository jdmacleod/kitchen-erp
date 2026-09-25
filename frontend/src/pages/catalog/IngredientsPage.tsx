import { useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { errorMessage } from "../../api/client";
import {
  BRIDGE_SOURCES,
  CANONICAL_UNITS,
  PERISHABILITIES,
  addMeasure,
  catalogErrorMessage,
  isPositiveDecimal,
  perishabilityLabel,
  useCreateIngredient,
  useIngredients,
  type BridgeSource,
  type CanonicalUnit,
  type Ingredient,
  type IngredientCreateInput,
  type Perishability,
} from "../../api/catalog";
import { Badge, Disclosure, RadioGroup, SelectField, TextAreaField } from "../../components/catalog/fields";
import { UsdaSuggestions, type QueuedMeasure } from "../../components/catalog/UsdaSuggestions";
import { Alert, Button, Card, EmptyState, Field, PageHeader, focusRing, tapTarget } from "../../components/ui";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { usePageTitle } from "../../lib/usePageTitle";
import { CATEGORY_KEYS, CategoryChip } from "../../components/CategoryChip";
import { Drawer } from "../../components/Drawer";
import { useNotice, type NoticeData } from "../../components/Notice";

export function IngredientsPage() {
  usePageTitle("Ingredients");
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
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

  const list = useIngredients(q, includeInactive);
  const items = list.data?.pages.flatMap((p) => p.items) ?? [];
  const [adding, setAdding] = useState(false);

  const notice = useNotice();
  const onCreated = async (ingredient: Ingredient, message: NoticeData) => {
    setAdding(false);
    await list.refetch();
    // G10: focus the new row when it is in the list; otherwise the Notice links to it.
    requestAnimationFrame(() => {
      const row = document.getElementById(`ingredient-row-${ingredient.id}`);
      if (row && message.tone === "success") row.focus();
      else notice.show({ ...message, focusAction: true });
    });
  };

  return (
    <>
      <PageHeader title="Ingredients" description="The things you cook with, each measured in one unit.">
        <Button onClick={() => setAdding(true)}>Add ingredient</Button>
      </PageHeader>

      <div className="mb-4 flex flex-col gap-3">
        <label htmlFor="ingredient-search" className="sr-only">
          Search ingredients
        </label>
        <input
          id="ingredient-search"
          type="text"
          enterKeyHint="search"
          autoComplete="off"
          maxLength={200}
          value={text}
          onChange={(e) => setText(e.target.value)}
          placeholder="Search by name"
          className={`min-h-12 w-full rounded-lg border border-neutral-300 bg-white px-4 text-base dark:border-neutral-700 dark:bg-neutral-900 ${focusRing}`}
        />
        <label className="inline-flex min-h-11 items-center gap-2 self-end text-sm lg:min-h-9">
          <input
            type="checkbox"
            checked={includeInactive}
            onChange={(e) => setParam("inactive", e.target.checked ? "1" : null)}
            className={`size-4 ${focusRing}`}
          />
          Show inactive
        </label>
      </div>

      {list.isPending ? (
        <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
          Loading…
        </p>
      ) : list.isError ? (
        <Alert tone="error">{errorMessage(list.error)}</Alert>
      ) : items.length === 0 ? (
        q ? (
          <EmptyState
            title={`No ingredients match ‘${q}’`}
            action={
              <Button
                variant="secondary"
                onClick={() => {
                  setText("");
                  setParam("q", null);
                }}
              >
                Clear search
              </Button>
            }
          />
        ) : (
          <EmptyState title="Add your first ingredient" action={<Button onClick={() => setAdding(true)}>Add ingredient</Button>}>
            Ingredients are the things you cook with. Products are the packages they come in.
          </EmptyState>
        )
      ) : (
        <Card>
          <ul aria-label="Ingredients" className="divide-y divide-neutral-200 dark:divide-neutral-800">
            {items.map((i) => (
              <IngredientRow key={i.id} ingredient={i} />
            ))}
          </ul>
          {list.hasNextPage ? (
            <div className="mt-3">
              <Button variant="secondary" disabled={list.isFetchingNextPage} onClick={() => list.fetchNextPage()}>
                {list.isFetchingNextPage ? "Loading…" : "Load more"}
              </Button>
            </div>
          ) : null}
        </Card>
      )}

      {adding ? <AddIngredientDrawer onClose={() => setAdding(false)} onCreated={onCreated} /> : null}
    </>
  );
}

function IngredientRow({ ingredient }: { ingredient: Ingredient }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 py-2">
      <Link id={`ingredient-row-${ingredient.id}`} to={`/catalog/ingredients/${ingredient.id}`} className={`${tapTarget} rounded-md font-medium underline-offset-2 hover:underline ${focusRing}`}>
        {ingredient.name}
      </Link>
      <span className="flex flex-wrap items-center gap-2 text-xs text-neutral-600 dark:text-neutral-400">
        <CategoryChip category={ingredient.category} categoryKey={ingredient.category_key} />
        <span>{ingredient.canonical_unit}</span>
        {ingredient.density_g_per_ml ? <Badge>density</Badge> : null}
        {ingredient.measures.length > 0 ? (
          <Badge>
            {ingredient.measures.length} {ingredient.measures.length === 1 ? "measure" : "measures"}
          </Badge>
        ) : null}
        {ingredient.active ? null : <Badge tone="warn">inactive</Badge>}
      </span>
    </li>
  );
}

// --- create form ------------------------------------------------------------

const emptyForm = {
  name: "",
  category: "",
  canonical_unit: "g" as CanonicalUnit,
  density: "",
  density_source: "measured" as BridgeSource,
  yield_pct: "",
  perishability: "shelf_stable" as Perishability,
  notes: "",
};

function AddIngredientDrawer({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (ingredient: Ingredient, notice: NoticeData) => void;
}) {
  const create = useCreateIngredient();
  const [form, setForm] = useState(emptyForm);
  const [queued, setQueued] = useState<QueuedMeasure[]>([]);
  const [invalid, setInvalid] = useState<string | null>(null);
  const [addingMeasures, setAddingMeasures] = useState(false);
  const [moreOpen, setMoreOpen] = useState(false);
  const debouncedName = useDebouncedValue(form.name.trim(), 300);
  const set = <K extends keyof typeof emptyForm>(key: K, value: (typeof emptyForm)[K]) =>
    setForm((f) => ({ ...f, [key]: value }));

  const onSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = form.name.trim();
    if (!name) {
      setInvalid("A name is required.");
      return;
    }
    if (form.density.trim() && !isPositiveDecimal(form.density)) {
      setInvalid("Density must be a positive number of grams per millilitre.");
      return;
    }
    if (form.yield_pct.trim() && !isPositiveDecimal(form.yield_pct)) {
      setInvalid("Yield must be a positive fraction such as 0.85.");
      return;
    }
    setInvalid(null);

    const input: IngredientCreateInput = { name, canonical_unit: form.canonical_unit };
    if (form.category.trim()) input.category = form.category.trim();
    if (form.density.trim()) {
      input.density_g_per_ml = form.density.trim();
      input.density_source = form.density_source;
    }
    if (form.yield_pct.trim()) input.yield_pct = form.yield_pct.trim();
    if (form.perishability !== "shelf_stable") input.perishability = form.perishability;
    if (form.notes.trim()) input.notes = form.notes.trim();

    let ingredient: Ingredient;
    try {
      ingredient = await create.mutateAsync(input);
    } catch {
      return; // create.error is rendered below
    }

    // Measures accepted from a suggestion are created right after, one by one,
    // so a failure on one never loses the ingredient or the others.
    let added = 0;
    const failed: string[] = [];
    if (queued.length > 0) {
      setAddingMeasures(true);
      for (const m of queued) {
        try {
          await addMeasure(ingredient.id, { label: m.label, canonical_qty: m.canonical_qty, source: "usda" });
          added += 1;
        } catch {
          failed.push(m.label);
        }
      }
      setAddingMeasures(false);
    }
    const withMeasures = added > 0 ? ` with ${added} ${added === 1 ? "measure" : "measures"}` : "";
    const missed = failed.length > 0 ? ` Could not add: ${failed.join(", ")}. Add them on the ingredient page.` : "";
    onCreated(ingredient, {
      tone: failed.length > 0 ? "info" : "success",
      message: `Added ${ingredient.name}${withMeasures}.${missed}`,
      action: { label: "Open it", to: `/catalog/ingredients/${ingredient.id}` },
    });
  };

  const busy = create.isPending || addingMeasures;

  const dirty = JSON.stringify(form) !== JSON.stringify(emptyForm) || queued.length > 0;

  return (
    <Drawer
      title="Add ingredient"
      thing="ingredient"
      dirty={dirty}
      onClose={onClose}
      formId="add-ingredient"
      primaryLabel="Add ingredient"
      busy={busy}
      busyLabel={addingMeasures ? "Adding measures…" : "Adding…"}
    >
      <form id="add-ingredient" onSubmit={onSubmit} className="flex flex-col gap-4" aria-label="Add ingredient" noValidate>
        {invalid ? <Alert tone="error">{invalid}</Alert> : null}
        {create.isError ? <Alert tone="error">{catalogErrorMessage(create.error)}</Alert> : null}

        <div className="grid gap-4 sm:grid-cols-2">
          <Field
            id="new-ingredient-name"
            label="Name"
            autoComplete="off"
            required
            value={form.name}
            onChange={(e) => set("name", e.target.value)}
          />
          <Field
            id="new-ingredient-category"
            label="Category"
            autoComplete="off"
            placeholder="produce, dairy, pantry…"
            list="ingredient-categories"
            value={form.category}
            onChange={(e) => set("category", e.target.value)}
          />
          {/* The nine categories the chips colour (UI-3.6); free text still works. */}
          <datalist id="ingredient-categories">
            {CATEGORY_KEYS.map((key) => (
              <option key={key} value={key} />
            ))}
          </datalist>
        </div>
        <RadioGroup
          name="new-ingredient-unit"
          legend="Canonical unit"
          options={CANONICAL_UNITS.map((u) => ({ value: u, label: u }))}
          value={form.canonical_unit}
          onChange={(v) => set("canonical_unit", v)}
          hint="Everything about this ingredient is stored in this unit."
        />

        <UsdaSuggestions
          name={debouncedName}
          canonicalUnit={form.canonical_unit}
          queued={queued}
          onUseDensity={(density) => {
            setForm((f) => ({ ...f, density, density_source: "usda" }));
            setMoreOpen(true);
          }}
          onAddMeasure={(m) => setQueued((list) => [...list, m])}
        />
        {queued.length > 0 ? (
          <div className="flex flex-wrap items-center gap-2" role="group" aria-label="Measures to add">
            <span className="text-sm">Measures to add:</span>
            {queued.map((m) => (
              <span
                key={m.label}
                className="inline-flex items-center gap-1 rounded-md border border-neutral-300 bg-neutral-50 px-2 py-1 text-xs dark:border-neutral-700 dark:bg-neutral-900"
              >
                {m.label} = {m.canonical_qty} g
                <button
                  type="button"
                  aria-label={`Remove ${m.label}`}
                  className={`rounded px-1 hover:bg-neutral-200 dark:hover:bg-neutral-800 ${focusRing}`}
                  onClick={() => setQueued((list) => list.filter((x) => x !== m))}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
        ) : null}

        <Disclosure summary="More: density, yield, perishability, notes" open={moreOpen} onOpenChange={setMoreOpen}>
          <div className="grid gap-4 sm:grid-cols-2">
            <Field
              id="new-ingredient-density"
              label="Density (g/ml)"
              inputMode="decimal"
              autoComplete="off"
              value={form.density}
              onChange={(e) => set("density", e.target.value)}
              hint="Needed to cross mass and volume. Leave blank if unknown."
            />
            <SelectField
              id="new-ingredient-density-source"
              label="Density source"
              value={form.density_source}
              onChange={(e) => set("density_source", e.target.value as BridgeSource)}
              disabled={form.density.trim() === ""}
            >
              {BRIDGE_SOURCES.map((s) => (
                <option key={s} value={s}>
                  {s}
                </option>
              ))}
            </SelectField>
            <Field
              id="new-ingredient-yield"
              label="Yield (fraction)"
              inputMode="decimal"
              autoComplete="off"
              placeholder="1"
              value={form.yield_pct}
              onChange={(e) => set("yield_pct", e.target.value)}
              hint="Usable part after trimming, between 0 and 1."
            />
            <SelectField
              id="new-ingredient-perishability"
              label="Perishability"
              value={form.perishability}
              onChange={(e) => set("perishability", e.target.value as Perishability)}
            >
              {PERISHABILITIES.map((p) => (
                <option key={p} value={p}>
                  {perishabilityLabel[p]}
                </option>
              ))}
            </SelectField>
          </div>
          <TextAreaField
            id="new-ingredient-notes"
            label="Notes"
            value={form.notes}
            onChange={(e) => set("notes", e.target.value)}
          />
        </Disclosure>
      </form>
    </Drawer>
  );
}
