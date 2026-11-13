import { useEffect, useId, useRef, useState, type SyntheticEvent } from "react";
import { Link, useLocation } from "react-router";
import { errorMessage } from "../../api/client";
import {
  useConfirmIngredient,
  useUnconfirmed,
  useUnconfirmedAction,
  type UnconfirmedValue,
} from "../../api/unconfirmedBridges";
import { useNotice } from "../../components/Notice";
import { Alert, Button, Card, Field, focusRing, sectionHeading } from "../../components/ui";

/** Rows shown at first, and added by each "Show more". */
const PAGE = 20;

const sources: Record<string, string> = {
  usda: "from USDA",
  label: "from a label",
  measured: "measured",
  llm: "from a model reading",
  manual: "entered by hand",
};

const trim = (value: string) => Number(value).toString();

/** "1 cup = 125 g" for a measure, "1.42 g/ml" for a density. */
function valueText(v: UnconfirmedValue): string {
  if (v.kind === "measure") return `1 ${v.label ?? ""} = ${trim(v.value)} ${v.canonical_unit}`;
  return `${trim(v.value)} g/ml`;
}

function whatText(v: UnconfirmedValue): string {
  if (v.kind === "density") return "Density";
  if (v.kind === "product_density") return `Density of ${v.product_name ?? "a product"}`;
  return "Measure";
}

/**
 * "Unconfirmed values", the last section of Needs a bridge (issue 356): every
 * density and measure saved as unconfirmed, values no food could have first,
 * each with Confirm, Edit and Delete in place. The inbox row links to
 * #unconfirmed.
 */
export function UnconfirmedSection() {
  const list = useUnconfirmed();
  const location = useLocation();
  const section = useRef<HTMLElement>(null);
  const [shown, setShown] = useState(PAGE);

  useEffect(() => {
    if (location.hash === "#unconfirmed" && list.data)
      section.current?.scrollIntoView({ block: "start" });
  }, [location.hash, list.data]);

  if (list.isPending) return null;
  if (list.isError) return <Alert tone="error">{errorMessage(list.error)}</Alert>;
  const items = list.data.items;
  if (items.length === 0) return null;
  const visible = items.slice(0, shown);
  const more = items.length - visible.length;
  const odd = items.filter((v) => v.problem).length;
  // An ingredient's plausible density and measures, which Confirm all takes together.
  const together = new Map<string, number>();
  for (const v of items)
    if (v.kind !== "product_density" && !v.problem)
      together.set(v.ingredient_id, (together.get(v.ingredient_id) ?? 0) + 1);
  const firsts = new Set<string>();

  return (
    <section
      id="unconfirmed"
      ref={section}
      aria-labelledby="unconfirmed-heading"
      className="mt-8 scroll-mt-4"
    >
      <h2 id="unconfirmed-heading" className={`${sectionHeading} mb-1`}>
        Unconfirmed values
      </h2>
      <p className="mb-3 text-sm text-neutral-700 dark:text-neutral-300">
        {items.length} {items.length === 1 ? "value was" : "values were"} saved without a check.
        Confirm each one against a label, or fix it. An ingredient&apos;s values can be confirmed
        together.
        {odd > 0
          ? ` ${odd} look${odd === 1 ? "s" : ""} wrong and ${odd === 1 ? "is" : "are"} listed first.`
          : ""}
      </p>
      <Card>
        <ul
          className="divide-y divide-neutral-200 dark:divide-neutral-800"
          aria-label="Unconfirmed values"
        >
          {visible.map((v) => {
            const first =
              !v.problem && v.kind !== "product_density" && !firsts.has(v.ingredient_id);
            if (first) firsts.add(v.ingredient_id);
            const count = together.get(v.ingredient_id) ?? 0;
            return (
              <ValueRow
                key={`${v.kind}-${v.id}`}
                value={v}
                confirmAll={first && count > 1 ? count : 0}
              />
            );
          })}
        </ul>
        {more > 0 ? (
          <div className="mt-3">
            <Button variant="secondary" onClick={() => setShown(shown + PAGE)}>
              Show {Math.min(more, PAGE)} more ({more} not shown)
            </Button>
          </div>
        ) : null}
      </Card>
    </section>
  );
}

/** "Confirm all 4 for honey": the ingredient's plausible values in one click. */
function ConfirmAll({ value, count }: { value: UnconfirmedValue; count: number }) {
  const confirm = useConfirmIngredient();
  const notice = useNotice();
  return (
    <>
      <Button
        variant="secondary"
        className="self-start"
        disabled={confirm.isPending}
        onClick={() =>
          confirm.mutate(value.ingredient_id, {
            onSuccess: (r) =>
              notice.show({
                tone: "success",
                message: `Confirmed ${r.confirmed} ${r.confirmed === 1 ? "value" : "values"} for ${value.ingredient_name}.`,
              }),
          })
        }
      >
        Confirm all {count} for {value.ingredient_name}
      </Button>
      {confirm.isError ? <Alert tone="error">{errorMessage(confirm.error)}</Alert> : null}
    </>
  );
}

function ValueRow({ value, confirmAll }: { value: UnconfirmedValue; confirmAll: number }) {
  const id = useId();
  const action = useUnconfirmedAction();
  const notice = useNotice();
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(value.value);
  const what = `${whatText(value).toLowerCase()} ${valueText(value)} for ${value.ingredient_name}`;

  const done = (message: string) => notice.show({ tone: "success", message });
  const save = (e: SyntheticEvent) => {
    e.preventDefault();
    if (!(Number(draft) > 0)) return;
    action.mutate(
      { target: value, action: "edit", value: draft },
      {
        onSuccess: () =>
          done(
            `Saved and confirmed the ${whatText(value).toLowerCase()} for ${value.ingredient_name}.`,
          ),
      },
    );
  };

  return (
    <li className="flex flex-col gap-2 py-3" data-testid="unconfirmed-value">
      <p className="text-sm">
        <Link
          to={`/catalog/ingredients/${value.ingredient_id}`}
          className={`rounded-sm font-medium underline-offset-2 hover:underline ${focusRing}`}
        >
          {value.ingredient_name}
        </Link>
        <span className="text-neutral-600 dark:text-neutral-400">
          {" "}
          · {whatText(value)}: {valueText(value)}
          {value.kind === "measure" && value.g_per_ml ? ` (${trim(value.g_per_ml)} g/ml)` : ""}
          {value.source ? ` · ${sources[value.source] ?? value.source}` : ""}
        </span>
      </p>
      {value.problem ? (
        <p className="text-sm text-amber-800 dark:text-amber-300">{value.problem}</p>
      ) : null}
      {editing ? (
        <form onSubmit={save} className="flex flex-col gap-2 lg:flex-row lg:items-end">
          <Field
            id={`${id}-value`}
            label={
              value.kind === "measure"
                ? `${value.canonical_unit} in 1 ${value.label ?? ""}`
                : "Density (g/ml)"
            }
            inputMode="decimal"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
          />
          <div className="grid grid-cols-2 gap-2 lg:flex">
            <Button type="submit" disabled={action.isPending || !(Number(draft) > 0)}>
              Save and confirm
            </Button>
            <Button variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </form>
      ) : (
        <div className="grid grid-cols-3 gap-2 lg:flex">
          <Button
            disabled={action.isPending}
            aria-label={`Confirm ${what}`}
            onClick={() =>
              action.mutate(
                { target: value, action: "confirm" },
                {
                  onSuccess: () =>
                    done(
                      `Confirmed the ${whatText(value).toLowerCase()} for ${value.ingredient_name}.`,
                    ),
                },
              )
            }
          >
            Confirm
          </Button>
          <Button
            variant="secondary"
            disabled={action.isPending}
            aria-label={`Edit ${what}`}
            onClick={() => setEditing(true)}
          >
            Edit
          </Button>
          <Button
            variant="ghost"
            disabled={action.isPending}
            aria-label={`Delete ${what}`}
            onClick={() =>
              action.mutate(
                { target: value, action: "delete" },
                {
                  onSuccess: () =>
                    done(
                      `Deleted the ${whatText(value).toLowerCase()} for ${value.ingredient_name}.`,
                    ),
                },
              )
            }
          >
            Delete
          </Button>
        </div>
      )}
      {confirmAll > 0 ? <ConfirmAll value={value} count={confirmAll} /> : null}
      {action.isError ? <Alert tone="error">{errorMessage(action.error)}</Alert> : null}
    </li>
  );
}
