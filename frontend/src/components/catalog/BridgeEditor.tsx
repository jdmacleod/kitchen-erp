import { useState, type FormEvent } from "react";
import {
  BRIDGE_SOURCES,
  catalogErrorMessage,
  isPositiveDecimal,
  trimDecimal,
  useAddMeasure,
  useConfirmDensity,
  useConfirmMeasure,
  useDeleteMeasure,
  useUpdateIngredient,
  useUpdateMeasure,
  type BridgeSource,
  type Ingredient,
  type Measure,
} from "../../api/catalog";
import { Alert, Button, Card, Field } from "../ui";
import { ConfirmedBadge, SelectField } from "./fields";

/**
 * The density and named measures of one ingredient, each with its source and
 * confirmation state. Confirming is a distinct, recorded action; changing a
 * value resets it.
 */
export function BridgeEditor({ ingredient }: { ingredient: Ingredient }) {
  return (
    <Card>
      <h2 className="mb-1 text-lg font-medium">Bridges</h2>
      <p className="mb-4 text-sm text-neutral-600 dark:text-neutral-400">
        How volumes, counts, and measures turn into {ingredient.canonical_unit}. Nothing here is guessed: a
        conversion with no bridge fails and says why.
      </p>
      <DensityRow ingredient={ingredient} />
      <MeasuresTable ingredient={ingredient} />
    </Card>
  );
}

// --- density ----------------------------------------------------------------

function SourceSelect({
  id,
  value,
  onChange,
  disabled,
}: {
  id: string;
  value: BridgeSource;
  onChange: (v: BridgeSource) => void;
  disabled?: boolean;
}) {
  return (
    <SelectField
      id={id}
      label="Source"
      value={value}
      onChange={(e) => onChange(e.target.value as BridgeSource)}
      disabled={disabled}
    >
      {BRIDGE_SOURCES.map((s) => (
        <option key={s} value={s}>
          {s}
        </option>
      ))}
    </SelectField>
  );
}

function DensityRow({ ingredient }: { ingredient: Ingredient }) {
  const update = useUpdateIngredient(ingredient.id);
  const confirm = useConfirmDensity(ingredient.id);
  const [editing, setEditing] = useState(false);
  const [clearing, setClearing] = useState(false);
  const [value, setValue] = useState(ingredient.density_g_per_ml ?? "");
  const [source, setSource] = useState<BridgeSource>(ingredient.density_source ?? "measured");
  const [invalid, setInvalid] = useState<string | null>(null);
  const has = ingredient.density_g_per_ml !== null;

  const startEdit = () => {
    setValue(ingredient.density_g_per_ml ?? "");
    setSource(ingredient.density_source ?? "measured");
    setInvalid(null);
    update.reset();
    setEditing(true);
  };

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!isPositiveDecimal(value)) {
      setInvalid("Density must be a positive number of grams per millilitre.");
      return;
    }
    setInvalid(null);
    update.mutate(
      { density_g_per_ml: value.trim(), density_source: source },
      { onSuccess: () => setEditing(false) },
    );
  };

  return (
    <section aria-labelledby="density-heading" className="border-b border-neutral-200 pb-4 dark:border-neutral-800">
      <h3 id="density-heading" className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
        Density
      </h3>
      {editing ? (
        <form onSubmit={onSubmit} className="mt-2 flex flex-col gap-3" aria-label="Edit density">
          {invalid ? <Alert tone="error">{invalid}</Alert> : null}
          {update.isError ? <Alert tone="error">{catalogErrorMessage(update.error)}</Alert> : null}
          <div className="grid gap-3 sm:grid-cols-2">
            <Field
              id="density-value"
              label="Density (g/ml)"
              inputMode="decimal"
              autoComplete="off"
              required
              value={value}
              onChange={(e) => setValue(e.target.value)}
              hint={has ? "A new value resets confirmation." : undefined}
            />
            <SourceSelect id="density-source" value={source} onChange={setSource} />
          </div>
          <div className="flex flex-wrap gap-2">
            <Button type="submit" disabled={update.isPending}>
              {update.isPending ? "Saving…" : "Save density"}
            </Button>
            <Button variant="secondary" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <div className="mt-2 flex flex-wrap items-center justify-between gap-3">
          <p className="text-sm" data-testid="density-summary">
            {has ? (
              <>
                <span className="font-medium">{trimDecimal(ingredient.density_g_per_ml ?? "")} g/ml</span>
                <span className="text-neutral-600 dark:text-neutral-400"> · {ingredient.density_source}</span>{" "}
                <ConfirmedBadge confirmed={ingredient.density_confirmed} />
              </>
            ) : (
              <span className="text-neutral-600 dark:text-neutral-400">
                No density. Mass and volume cannot be crossed until one is added.
              </span>
            )}
          </p>
          <div className="flex flex-wrap gap-2">
            {has && !ingredient.density_confirmed ? (
              <Button variant="secondary" disabled={confirm.isPending} onClick={() => confirm.mutate()}>
                {confirm.isPending ? "Confirming…" : "Confirm density"}
              </Button>
            ) : null}
            <Button variant="secondary" onClick={startEdit}>
              {has ? "Edit density" : "Add density"}
            </Button>
            {has ? (
              clearing ? (
                <span className="flex gap-2" role="group" aria-label="Confirm clearing the density">
                  <Button
                    variant="danger"
                    disabled={update.isPending}
                    onClick={() => update.mutate({ clear_density: true }, { onSettled: () => setClearing(false) })}
                  >
                    {update.isPending ? "Clearing…" : "Confirm clear"}
                  </Button>
                  <Button variant="secondary" onClick={() => setClearing(false)}>
                    Cancel
                  </Button>
                </span>
              ) : (
                <Button variant="danger" onClick={() => setClearing(true)}>
                  Clear density
                </Button>
              )
            ) : null}
          </div>
        </div>
      )}
      {!editing && confirm.isError ? (
        <Alert tone="error" className="mt-2">
          {catalogErrorMessage(confirm.error)}
        </Alert>
      ) : null}
      {!editing && update.isError ? (
        <Alert tone="error" className="mt-2">
          {catalogErrorMessage(update.error)}
        </Alert>
      ) : null}
    </section>
  );
}

// --- measures ---------------------------------------------------------------

function MeasuresTable({ ingredient }: { ingredient: Ingredient }) {
  const unit = ingredient.canonical_unit;
  return (
    <section aria-labelledby="measures-heading" className="pt-4">
      <h3 id="measures-heading" className="text-sm font-semibold uppercase tracking-wide text-neutral-500">
        Named measures
      </h3>
      <p className="mt-1 text-xs text-neutral-600 dark:text-neutral-400">
        A label such as “cup”, “clove”, or “medium” and what one of it weighs in {unit}. Measures are checked
        before units, so “cup” here beats the generic cup.
      </p>
      {ingredient.measures.length === 0 ? (
        <p className="mt-3 text-sm text-neutral-600 dark:text-neutral-400">No measures yet.</p>
      ) : (
        <div className="mt-3 overflow-x-auto">
          <table className="w-full text-sm" aria-label="Named measures">
            <thead className="text-left text-xs uppercase tracking-wide text-neutral-500">
              <tr>
                <th scope="col" className="py-2 pr-3 font-semibold">
                  Label
                </th>
                <th scope="col" className="py-2 pr-3 font-semibold">
                  One is
                </th>
                <th scope="col" className="py-2 pr-3 font-semibold">
                  Source
                </th>
                <th scope="col" className="py-2 pr-3 font-semibold">
                  State
                </th>
                <th scope="col" className="py-2 font-semibold">
                  <span className="sr-only">Actions</span>
                </th>
              </tr>
            </thead>
            <tbody>
              {ingredient.measures.map((m) => (
                <MeasureRow key={m.id} measure={m} ingredientId={ingredient.id} unit={unit} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      <AddMeasureForm ingredientId={ingredient.id} unit={unit} />
    </section>
  );
}

function MeasureRow({ measure, ingredientId, unit }: { measure: Measure; ingredientId: string; unit: string }) {
  const update = useUpdateMeasure(ingredientId);
  const confirm = useConfirmMeasure(ingredientId);
  const remove = useDeleteMeasure(ingredientId);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [label, setLabel] = useState(measure.label);
  const [qty, setQty] = useState(measure.canonical_qty);
  const [source, setSource] = useState<BridgeSource>(measure.source);
  const [invalid, setInvalid] = useState<string | null>(null);
  const busy = update.isPending || confirm.isPending || remove.isPending;
  const error = update.error ?? confirm.error ?? remove.error;

  const startEdit = () => {
    setLabel(measure.label);
    setQty(measure.canonical_qty);
    setSource(measure.source);
    setInvalid(null);
    update.reset();
    setEditing(true);
  };

  const save = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!label.trim()) {
      setInvalid("A label is required.");
      return;
    }
    if (!isPositiveDecimal(qty)) {
      setInvalid("The quantity must be a positive number.");
      return;
    }
    setInvalid(null);
    const input: { id: string; label?: string; canonical_qty?: string; source?: BridgeSource } = { id: measure.id };
    if (label.trim() !== measure.label) input.label = label.trim();
    if (qty.trim() !== measure.canonical_qty) input.canonical_qty = qty.trim();
    if (source !== measure.source) input.source = source;
    update.mutate(input, { onSuccess: () => setEditing(false) });
  };

  if (editing) {
    const formId = `measure-${measure.id}-form`;
    return (
      <tr className="border-t border-neutral-200 dark:border-neutral-800">
        <td colSpan={5} className="py-3">
          <form id={formId} onSubmit={save} className="flex flex-col gap-3" aria-label={`Edit measure ${measure.label}`}>
            {invalid ? <Alert tone="error">{invalid}</Alert> : null}
            {update.isError ? <Alert tone="error">{catalogErrorMessage(update.error)}</Alert> : null}
            <div className="grid gap-3 sm:grid-cols-3">
              <Field
                id={`measure-${measure.id}-label`}
                label="Label"
                autoComplete="off"
                required
                value={label}
                onChange={(e) => setLabel(e.target.value)}
              />
              <Field
                id={`measure-${measure.id}-qty`}
                label={`One is (${unit})`}
                inputMode="decimal"
                autoComplete="off"
                required
                value={qty}
                onChange={(e) => setQty(e.target.value)}
                hint="A new quantity or source resets confirmation."
              />
              <SourceSelect id={`measure-${measure.id}-source`} value={source} onChange={setSource} />
            </div>
            <div className="flex flex-wrap gap-2">
              <Button type="submit" disabled={busy}>
                {update.isPending ? "Saving…" : "Save measure"}
              </Button>
              <Button variant="secondary" onClick={() => setEditing(false)}>
                Cancel
              </Button>
            </div>
          </form>
        </td>
      </tr>
    );
  }

  return (
    <tr className="border-t border-neutral-200 align-top dark:border-neutral-800">
      <td className="py-2 pr-3 font-medium">{measure.label}</td>
      <td className="py-2 pr-3 whitespace-nowrap">
        {trimDecimal(measure.canonical_qty)} {unit}
      </td>
      <td className="py-2 pr-3">{measure.source}</td>
      <td className="py-2 pr-3">
        <ConfirmedBadge confirmed={measure.confirmed} />
      </td>
      <td className="py-1">
        <div className="flex flex-wrap justify-end gap-1">
          {measure.confirmed ? null : (
            <Button
              variant="secondary"
              className="min-h-8 px-2 text-xs"
              disabled={busy}
              onClick={() => confirm.mutate(measure.id)}
              aria-label={`Confirm ${measure.label}`}
            >
              Confirm
            </Button>
          )}
          <Button
            variant="ghost"
            className="min-h-8 px-2 text-xs"
            disabled={busy}
            onClick={startEdit}
            aria-label={`Edit ${measure.label}`}
          >
            Edit
          </Button>
          {deleting ? (
            <span className="flex gap-1" role="group" aria-label={`Confirm deleting ${measure.label}`}>
              <Button
                variant="danger"
                className="min-h-8 px-2 text-xs"
                disabled={busy}
                onClick={() => remove.mutate(measure.id, { onSettled: () => setDeleting(false) })}
              >
                {remove.isPending ? "Deleting…" : "Confirm delete"}
              </Button>
              <Button variant="secondary" className="min-h-8 px-2 text-xs" onClick={() => setDeleting(false)}>
                Cancel
              </Button>
            </span>
          ) : (
            <Button
              variant="danger"
              className="min-h-8 px-2 text-xs"
              disabled={busy}
              onClick={() => setDeleting(true)}
              aria-label={`Delete ${measure.label}`}
            >
              Delete
            </Button>
          )}
        </div>
        {error ? (
          <p role="alert" className="mt-1 text-right text-xs text-red-700 dark:text-red-300">
            {catalogErrorMessage(error)}
          </p>
        ) : null}
      </td>
    </tr>
  );
}

function AddMeasureForm({ ingredientId, unit }: { ingredientId: string; unit: string }) {
  const add = useAddMeasure(ingredientId);
  const [label, setLabel] = useState("");
  const [qty, setQty] = useState("");
  const [source, setSource] = useState<BridgeSource>("measured");
  const [invalid, setInvalid] = useState<string | null>(null);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!label.trim()) {
      setInvalid("A label is required.");
      return;
    }
    if (!isPositiveDecimal(qty)) {
      setInvalid("The quantity must be a positive number.");
      return;
    }
    setInvalid(null);
    add.mutate(
      { label: label.trim(), canonical_qty: qty.trim(), source },
      {
        onSuccess: () => {
          setLabel("");
          setQty("");
          document.getElementById("new-measure-label")?.focus();
        },
      },
    );
  };

  return (
    <form onSubmit={onSubmit} className="mt-4 flex flex-col gap-3" aria-labelledby="add-measure-heading">
      <h4 id="add-measure-heading" className="text-sm font-medium">
        Add measure
      </h4>
      {invalid ? <Alert tone="error">{invalid}</Alert> : null}
      {add.isError ? <Alert tone="error">{catalogErrorMessage(add.error)}</Alert> : null}
      <div className="grid gap-3 sm:grid-cols-3">
        <Field
          id="new-measure-label"
          label="Label"
          placeholder="cup, clove, medium…"
          autoComplete="off"
          required
          value={label}
          onChange={(e) => setLabel(e.target.value)}
        />
        <Field
          id="new-measure-qty"
          label={`One is (${unit})`}
          inputMode="decimal"
          autoComplete="off"
          required
          value={qty}
          onChange={(e) => setQty(e.target.value)}
        />
        <SourceSelect id="new-measure-source" value={source} onChange={setSource} />
      </div>
      <div>
        <Button type="submit" variant="secondary" disabled={add.isPending}>
          {add.isPending ? "Adding…" : "Add measure"}
        </Button>
      </div>
    </form>
  );
}
