import { useState, type FormEvent } from "react";
import { Link } from "react-router";
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
import { Alert, Button, Card, EmptyState, Field, PageHeader, focusRing } from "../../components/ui";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { usePageTitle } from "../../lib/usePageTitle";
import { CategoryChip } from "../../components/CategoryChip";
import { useNotice } from "../../components/Notice";

export function IngredientsPage() {
  usePageTitle("Ingredients");
  const [q, setQ] = useState("");
  const [includeInactive, setIncludeInactive] = useState(false);
  const debouncedQ = useDebouncedValue(q.trim(), 250);
  const list = useIngredients(debouncedQ, includeInactive);
  const items = list.data?.pages.flatMap((p) => p.items) ?? [];

  return (
    <>
      <PageHeader title="Ingredients" />
      <div className="flex flex-col gap-6">
        <CreateIngredientForm />

        <Card>
          <div className="mb-3 flex flex-wrap items-end justify-between gap-3">
            <h2 className="text-lg font-medium">Ingredient catalog</h2>
            <label className="inline-flex min-h-10 items-center gap-2 text-sm">
              <input
                type="checkbox"
                checked={includeInactive}
                onChange={(e) => setIncludeInactive(e.target.checked)}
                className={`size-4 ${focusRing}`}
              />
              Show inactive
            </label>
          </div>
          <Field
            id="ingredient-search"
            label="Search"
            type="search"
            autoComplete="off"
            placeholder="Part of a name"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            className="mb-3"
          />
          {list.isPending ? (
            <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
              Loading…
            </p>
          ) : list.isError ? (
            <Alert tone="error">{errorMessage(list.error)}</Alert>
          ) : items.length === 0 ? (
            <EmptyState title={debouncedQ ? "No ingredients match" : "No ingredients yet"}>
              {debouncedQ ? "Try a shorter search." : "Ingredients are the things you cook with. Add one above."}
            </EmptyState>
          ) : (
            <>
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
            </>
          )}
        </Card>
      </div>
    </>
  );
}

function IngredientRow({ ingredient }: { ingredient: Ingredient }) {
  return (
    <li className="flex flex-wrap items-center justify-between gap-2 py-2">
      <Link to={`/catalog/ingredients/${ingredient.id}`} className={`rounded-md font-medium underline-offset-2 hover:underline ${focusRing}`}>
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

function CreateIngredientForm() {
  const create = useCreateIngredient();
  const [form, setForm] = useState(emptyForm);
  const [queued, setQueued] = useState<QueuedMeasure[]>([]);
  const [invalid, setInvalid] = useState<string | null>(null);
  const notice = useNotice();
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
    notice.show({
      tone: failed.length > 0 ? "info" : "success",
      message: `Added ${ingredient.name}${withMeasures}.${missed}`,
      action: { label: "Open it", to: `/catalog/ingredients/${ingredient.id}` },
    });
    setForm(emptyForm);
    setQueued([]);
    document.getElementById("new-ingredient-name")?.focus();
  };

  const busy = create.isPending || addingMeasures;

  return (
    <Card>
      <form onSubmit={onSubmit} className="flex flex-col gap-4" aria-labelledby="create-ingredient-heading" noValidate>
        <h2 id="create-ingredient-heading" className="text-lg font-medium">
          Add an ingredient
        </h2>
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
            value={form.category}
            onChange={(e) => set("category", e.target.value)}
          />
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

        <div>
          <Button type="submit" disabled={busy}>
            {create.isPending ? "Creating…" : addingMeasures ? "Adding measures…" : "Create ingredient"}
          </Button>
        </div>
      </form>
    </Card>
  );
}
