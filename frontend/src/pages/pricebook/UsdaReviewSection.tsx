import { useEffect, useRef, useState } from "react";
import { useLocation } from "react-router";
import { errorMessage, isApiError } from "../../api/client";
import {
  useUsdaDecision,
  useUsdaReview,
  type DensityExists,
  type UsdaReviewGroup,
} from "../../api/usdaReview";
import { useNotice } from "../../components/Notice";
import { Alert, Button, Card, focusRing, sectionHeading } from "../../components/ui";
import { UsdaChangeLink } from "./UsdaChangeLink";

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/** Rows shown at first, and added by each "Show more" (issue 359). */
const PAGE = 20;

/**
 * "USDA suggestions", the first section of Needs a bridge (10, 1G; DV2, DV6,
 * DV8, DV18–DV21). Grouped rows, never cards; no accept-all; a decided
 * ingredient leaves; values are saved as unconfirmed. Each row starts with the
 * backend's suggested density and measures chosen, and the list shows 20 rows
 * at a time (issue 359).
 */
export function UsdaReviewSection() {
  const review = useUsdaReview();
  const [shown, setShown] = useState(PAGE);
  const location = useLocation();
  const section = useRef<HTMLElement>(null);
  const loaded = review.data?.loaded;

  // The inbox row links to #usda; scroll there once the section has content.
  useEffect(() => {
    if (location.hash === "#usda" && review.data)
      section.current?.scrollIntoView({ block: "start" });
  }, [location.hash, review.data]);

  if (review.isPending) {
    return (
      <p role="status" className="mb-6 text-sm text-neutral-600 dark:text-neutral-400">
        Loading…
      </p>
    );
  }
  if (review.isError) return <Alert tone="error">{errorMessage(review.error)}</Alert>;
  const groups = review.data.groups;
  if (loaded && groups.length === 0) return null;
  const visible = groups.slice(0, shown);
  const more = groups.length - visible.length;

  return (
    <section id="usda" ref={section} aria-labelledby="usda-heading" className="mb-8 scroll-mt-4">
      <h2 id="usda-heading" className={`${sectionHeading} mb-3`}>
        USDA suggestions
      </h2>
      {!loaded ? (
        <p className="text-sm text-neutral-700 dark:text-neutral-300">
          USDA data isn&apos;t loaded yet. Whoever runs this Kitchen ERP can load it with{" "}
          <code>kerp import usda</code>.
        </p>
      ) : (
        <Card>
          <p className="mb-2 text-sm text-neutral-700 dark:text-neutral-300">
            {plural(groups.length, "ingredient")} with values to review
            {more > 0 ? ` · showing ${visible.length}` : ""}
          </p>
          <ul
            className="divide-y divide-neutral-200 dark:divide-neutral-800"
            aria-label="USDA suggestions"
          >
            {visible.map((g) => (
              // A new USDA food is a new row: its choices start over.
              <GroupRow key={`${g.ingredient_id}-${g.fdc_id}`} group={g} />
            ))}
          </ul>
          {more > 0 ? (
            <div className="mt-3">
              <Button variant="secondary" onClick={() => setShown(shown + PAGE)}>
                Show {Math.min(more, PAGE)} more of {more}
              </Button>
            </div>
          ) : null}
          {review.data.release_date ? (
            <p className="mt-3 text-xs text-neutral-600 dark:text-neutral-400">
              Data: USDA FoodData Central, release {review.data.release_date}
            </p>
          ) : null}
        </Card>
      )}
    </section>
  );
}

function GroupRow({ group }: { group: UsdaReviewGroup }) {
  const decision = useUsdaDecision();
  const notice = useNotice();
  const [density, setDensity] = useState<string>(
    () => group.densities.find((d) => d.suggested)?.portion_id ?? "",
  );
  const [unticked, setUnticked] = useState<Set<string>>(
    () => new Set(group.measures.filter((m) => !m.suggested).map((m) => m.label)),
  );
  const [raced, setRaced] = useState<DensityExists | null>(null);
  const [changing, setChanging] = useState(false);
  const headingId = `usda-${group.ingredient_id}`;
  const ticked = group.measures.filter((m) => !unticked.has(m.label)).map((m) => m.label);
  const chosen = (density ? 1 : 0) + ticked.length;

  const saved = (count: number) =>
    notice.show({
      tone: "success",
      message: `Saved ${plural(count, "value")} for ${group.name} as unconfirmed. Confirm them on its page after checking a label.`,
      action: { label: `Open ${group.name}`, to: `/catalog/ingredients/${group.ingredient_id}` },
    });
  const accept = (opts: { withDensity: boolean; replace?: boolean }) =>
    decision.mutate(
      {
        ingredientId: group.ingredient_id,
        density_portion_id: opts.withDensity && density ? density : undefined,
        measures: ticked,
        replace_density: opts.replace ?? false,
      },
      {
        onSuccess: (r) => (r.saved > 0 ? saved(r.saved) : undefined),
        onError: (e) => {
          if (isApiError(e) && e.code === "density_exists" && e.details)
            setRaced(e.details as unknown as DensityExists);
        },
      },
    );

  return (
    <li className="flex flex-col gap-3 py-3" aria-labelledby={headingId} data-testid="usda-group">
      <p id={headingId} className="text-sm">
        <span className="font-medium">{group.name}</span>
        <span className="text-neutral-600 dark:text-neutral-400">
          {" "}
          · {group.canonical_unit}
          {group.usda_description ? ` · linked to “${group.usda_description}”` : ""}
        </span>
      </p>
      {changing ? (
        <UsdaChangeLink group={group} onClose={() => setChanging(false)} />
      ) : (
        <button
          type="button"
          onClick={() => setChanging(true)}
          className={`inline-flex min-h-11 items-center self-start rounded-sm text-sm text-blue-700 underline lg:min-h-9 dark:text-blue-300 ${focusRing}`}
        >
          Wrong food? Change the link
        </button>
      )}
      {group.densities.length > 0 ? (
        <fieldset className="flex flex-col gap-1 text-sm lg:flex-row lg:flex-wrap lg:gap-x-6">
          <legend className="mb-1 text-xs text-neutral-600 dark:text-neutral-400">Density</legend>
          <label className="flex min-h-11 items-center gap-2 lg:min-h-0">
            <input
              type="radio"
              name={`${headingId}-density`}
              checked={density === ""}
              onChange={() => setDensity("")}
              className={focusRing}
            />
            None
          </label>
          {group.densities.map((d) => (
            <label key={d.portion_id} className="flex min-h-11 items-center gap-2 lg:min-h-0">
              <input
                type="radio"
                name={`${headingId}-density`}
                checked={density === d.portion_id}
                onChange={() => setDensity(d.portion_id)}
                className={focusRing}
              />
              {d.portion_label} = {d.gram_weight} g → {d.density_g_per_ml} g/ml
            </label>
          ))}
          {group.density_spread_pct !== null ? (
            <p className="text-xs text-neutral-600 dark:text-neutral-400 lg:basis-full">
              These portions differ by {group.density_spread_pct}%. Check a label before confirming.
            </p>
          ) : null}
        </fieldset>
      ) : null}
      {group.measures.length > 0 ? (
        <fieldset className="flex flex-col gap-1 text-sm lg:flex-row lg:flex-wrap lg:gap-x-6">
          <legend className="mb-1 text-xs text-neutral-600 dark:text-neutral-400">Measures</legend>
          {group.measures.map((m) => (
            <label key={m.label} className="flex min-h-11 items-center gap-2 lg:min-h-0">
              <input
                type="checkbox"
                checked={!unticked.has(m.label)}
                onChange={(e) => {
                  const next = new Set(unticked);
                  if (e.target.checked) next.delete(m.label);
                  else next.add(m.label);
                  setUnticked(next);
                }}
                className={focusRing}
              />
              1 {m.label} = {m.canonical_qty} {group.canonical_unit}
            </label>
          ))}
        </fieldset>
      ) : null}
      {raced ? (
        <div
          role="alert"
          className="flex flex-col gap-2 rounded-md border border-neutral-300 bg-neutral-50 p-3 text-sm dark:border-neutral-700 dark:bg-neutral-900"
        >
          <p>
            {group.name} now has a density of {Number(raced.density_g_per_ml).toString()} g/ml
            {raced.density_confirmed ? "" : " (unconfirmed)"}.
          </p>
          <div className="grid grid-cols-2 gap-2 lg:flex">
            <Button
              variant="secondary"
              disabled={decision.isPending}
              onClick={() => {
                setRaced(null);
                accept({ withDensity: false });
              }}
            >
              Keep it
            </Button>
            <Button
              variant="secondary"
              disabled={decision.isPending}
              onClick={() => {
                setRaced(null);
                accept({ withDensity: true, replace: true });
              }}
            >
              Replace
            </Button>
          </div>
        </div>
      ) : null}
      {decision.isError &&
      !raced &&
      !(isApiError(decision.error) && decision.error.code === "density_exists") ? (
        <Alert tone="error">{errorMessage(decision.error)}</Alert>
      ) : null}
      {!raced ? (
        <div className="grid grid-cols-2 gap-2 lg:flex">
          <Button
            disabled={decision.isPending || chosen === 0}
            onClick={() => accept({ withDensity: true })}
          >
            Accept selected
          </Button>
          <Button
            variant="ghost"
            disabled={decision.isPending}
            onClick={() => decision.mutate({ ingredientId: group.ingredient_id, skip: true })}
          >
            Skip
          </Button>
        </div>
      ) : null}
    </li>
  );
}
