import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { errorMessage, isApiError } from "../../api/client";
import {
  useLinkAction,
  useLinkPage,
  useMerge,
  useMergePreview,
  useStandardEntries,
  type LinkRow,
  type MergeNeeded,
  type MergeTarget,
  type StandardEntry,
} from "../../api/ingredientLink";
import { CategoryChip } from "../../components/CategoryChip";
import { Combobox } from "../../components/catalog/Combobox";
import { useNotice } from "../../components/Notice";
import { Alert, Button, Card, EmptyState, Field, PageHeader, alertTones, focusRing, secondaryLinkClass } from "../../components/ui";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { usePageTitle } from "../../lib/usePageTitle";

type Tally = { linked: number; renamed: number; merged: number; skipped: number };
type Decision = keyof Tally;

const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;

/**
 * Link ingredients to the standard list (03 and 10, 1G; DV1, DV3–DV5, DV10–DV17).
 * One row per ingredient still to review. A decided row leaves, focus moves to
 * the next row's first action, and a polite live region says what happened.
 */
export function IngredientLinkPage() {
  usePageTitle("Link ingredients");
  const page = useLinkPage();
  const notice = useNotice();
  const [announcement, setAnnouncement] = useState("");
  const [tally, setTally] = useState<Tally>({ linked: 0, renamed: 0, merged: 0, skipped: 0 });
  // The row whose first action takes focus once the list refreshes ("" = the empty state).
  const focusNext = useRef<string | null>(null);
  const toReview = page.data?.to_review ?? [];
  const skipped = page.data?.skipped ?? [];

  // Focus the next row's first action once the list has refreshed without the decided row.
  useEffect(() => {
    if (focusNext.current === null || page.isFetching) return;
    const target = document.getElementById(focusNext.current === "" ? "link-empty" : `link-${focusNext.current}-first`);
    if (!target) return;
    target.focus();
    focusNext.current = null;
  }, [page.isFetching, page.data]);

  const decided = (row: LinkRow, kind: Decision, message: string) => {
    const index = toReview.findIndex((r) => r.id === row.id);
    const rest = toReview.filter((r) => r.id !== row.id);
    const next = rest[Math.min(index, rest.length - 1)];
    const nextTally = { ...tally, [kind]: tally[kind] + 1 };
    setTally(nextTally);
    setAnnouncement(`${message} ${rest.length === 0 ? "None left." : `${rest.length} left.`}`);
    focusNext.current = next ? next.id : "";
    if (rest.length === 0) {
      // Finish (DV5): what this visit decided.
      const reviewed = nextTally.linked + nextTally.renamed + nextTally.merged + nextTally.skipped;
      const parts = [
        `${nextTally.linked} linked`,
        nextTally.renamed ? `${nextTally.renamed} renamed` : null,
        `${nextTally.merged} merged`,
        `${nextTally.skipped} skipped`,
      ].filter(Boolean);
      notice.show({ tone: "success", message: `${reviewed} reviewed: ${parts.join(", ")}.` });
    }
  };

  return (
    <>
      <PageHeader title="Link ingredients to the standard list" description="Give each ingredient its standard name so spellings and USDA data line up." />
      <p aria-live="polite" className="sr-only" data-testid="link-announcement">
        {announcement}
      </p>
      {page.isPending ? (
        <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
          Loading…
        </p>
      ) : page.isError ? (
        <Alert tone="error">{errorMessage(page.error)}</Alert>
      ) : (
        <>
          <p className="sticky top-[env(safe-area-inset-top,0px)] z-10 -mx-1 mb-3 bg-neutral-50 px-1 py-2 text-sm text-neutral-700 lg:static dark:bg-neutral-950 dark:text-neutral-300" data-testid="link-counts">
            {toReview.length} to review · {skipped.length} skipped
          </p>
          {toReview.length === 0 ? (
            <EmptyState
              title="Every ingredient is linked or skipped"
              action={
                <Link id="link-empty" to="/" className={secondaryLinkClass}>
                  Back to Home
                </Link>
              }
            >
              Nothing is waiting for a standard name.
            </EmptyState>
          ) : (
            <Card>
              <ul className="divide-y divide-neutral-200 dark:divide-neutral-800" aria-label="Ingredients to review">
                {toReview.map((row) => (
                  <LinkRowView key={row.id} row={row} onDecided={decided} />
                ))}
              </ul>
            </Card>
          )}
          {skipped.length > 0 ? <SkippedFold rows={skipped} /> : null}
        </>
      )}
    </>
  );
}

type Mode = { kind: "idle" } | { kind: "rename" } | { kind: "choose" } | { kind: "merge"; target: MergeTarget; needed: MergeNeeded };

function LinkRowView({ row, onDecided }: { row: LinkRow; onDecided: (row: LinkRow, kind: Decision, message: string) => void }) {
  const action = useLinkAction();
  const [mode, setMode] = useState<Mode>({ kind: "idle" });
  const [chosen, setChosen] = useState<StandardEntry | null>(null);
  const [newName, setNewName] = useState(row.name);
  const [error, setError] = useState<string | null>(null);
  const suggestion = chosen ?? row.suggestion;
  const headingId = `link-${row.id}-name`;

  const failed = (e: unknown, target: MergeTarget) => {
    if (isApiError(e) && e.code === "merge_needed" && e.details) {
      setError(null);
      setMode({ kind: "merge", target, needed: e.details as unknown as MergeNeeded });
      return;
    }
    setError(errorMessage(e));
  };
  const link = () => {
    if (!suggestion) return;
    setError(null);
    action.mutate(
      { id: row.id, action: "link", standard_key: suggestion.key },
      { onSuccess: () => onDecided(row, "linked", `Linked ${row.name} to ${suggestion.name}.`), onError: (e) => failed(e, { standard_key: suggestion.key }) },
    );
  };
  const rename = () => {
    const name = newName.trim();
    if (!name) return setError("A name needs at least one letter or digit.");
    setError(null);
    action.mutate(
      { id: row.id, action: "rename", name },
      { onSuccess: () => onDecided(row, "renamed", `Renamed ${row.name} to ${name}.`), onError: (e) => failed(e, { name }) },
    );
  };
  const skip = () =>
    action.mutate({ id: row.id, action: "skip" }, { onSuccess: () => onDecided(row, "skipped", `Skipped ${row.name}.`), onError: (e) => setError(errorMessage(e)) });

  // A chosen entry has no USDA line; the suggestion carries one when USDA data is loaded.
  const usda = chosen ? null : (row.suggestion?.usda_description ?? null);
  const kept = suggestion && suggestion.name.toLowerCase() !== row.name.toLowerCase() ? `; “${row.name}” kept as another spelling` : "";

  return (
    <li className="flex flex-col gap-2 py-3" aria-labelledby={headingId} data-testid="link-row">
      <div className="flex flex-col gap-1 lg:flex-row lg:items-baseline lg:justify-between lg:gap-4">
        <p id={headingId} className="text-sm">
          <span className="font-medium">{row.name}</span>
          <span className="text-neutral-600 dark:text-neutral-400">
            {" "}
            · {row.canonical_unit} · {plural(row.products, "product")}
          </span>
        </p>
        <p className="text-sm text-neutral-700 dark:text-neutral-300">
          {suggestion ? (
            <>
              → <span className="font-medium">{suggestion.name}</span> <CategoryChip category={suggestion.category} categoryKey={suggestion.category_key} />
              {kept}
            </>
          ) : (
            "No standard name fits"
          )}
        </p>
      </div>
      {usda ? <p className="text-xs text-neutral-600 dark:text-neutral-400">USDA: {usda}</p> : null}
      {row.conflict && !chosen && mode.kind === "idle" ? (
        <p className="text-xs text-neutral-600 dark:text-neutral-400">{row.conflict.name} already exists, so linking merges the two.</p>
      ) : null}
      {error ? <Alert tone="error">{error}</Alert> : null}

      {mode.kind === "rename" ? (
        <div className="flex flex-col gap-2 lg:flex-row lg:items-end">
          <Field id={`link-${row.id}-rename`} label={`New name for ${row.name}`} value={newName} autoFocus onChange={(e) => setNewName(e.target.value)} className="lg:w-72" />
          <div className="grid grid-cols-2 gap-2 lg:flex">
            <Button onClick={rename} disabled={action.isPending}>
              Save
            </Button>
            <Button variant="secondary" onClick={() => setMode({ kind: "idle" })}>
              Cancel
            </Button>
          </div>
        </div>
      ) : mode.kind === "choose" ? (
        <ChooseStandard
          id={`link-${row.id}-choose`}
          onChosen={(e) => {
            setChosen(e);
            setMode({ kind: "idle" });
          }}
          onCancel={() => setMode({ kind: "idle" })}
        />
      ) : mode.kind === "merge" ? (
        <MergePanel
          row={row}
          target={mode.target}
          needed={mode.needed}
          onCancel={() => setMode({ kind: "idle" })}
          onMerged={(into) => onDecided(row, "merged", `Merged ${row.name} into ${into}.`)}
        />
      ) : (
        <div className="grid grid-cols-3 gap-2 lg:flex lg:flex-wrap lg:items-center">
          <Button id={`link-${row.id}-first`} onClick={suggestion ? link : () => setMode({ kind: "choose" })} disabled={action.isPending}>
            {suggestion ? "Link" : "Choose…"}
          </Button>
          <Button variant="secondary" onClick={() => setMode({ kind: "rename" })} disabled={action.isPending}>
            Rename…
          </Button>
          <Button variant="ghost" onClick={skip} disabled={action.isPending}>
            Skip
          </Button>
          {suggestion ? (
            <button type="button" onClick={() => setMode({ kind: "choose" })} className={`col-span-3 min-h-11 rounded text-left text-sm underline lg:min-h-0 ${focusRing}`}>
              Choose another standard name
            </button>
          ) : null}
        </div>
      )}
    </li>
  );
}

/** A search of the standard list only, for a wrong or missing suggestion (DV4). */
function ChooseStandard({ id, onChosen, onCancel }: { id: string; onChosen: (e: StandardEntry) => void; onCancel: () => void }) {
  const [text, setText] = useState("");
  const debounced = useDebouncedValue(text, 200);
  const found = useStandardEntries(debounced);
  const items = text.trim() ? (found.data ?? []) : [];
  return (
    <div className="flex flex-col gap-2 lg:flex-row lg:items-end">
      <div className="lg:w-80">
        <Combobox<StandardEntry>
          id={id}
          label="Standard name"
          placeholder="Search the standard list"
          listLabel="Standard names"
          inputValue={text}
          onInputChange={setText}
          items={items}
          autoFocus
          getKey={(e) => e.key}
          status={text.trim() && !found.isFetching && items.length === 0 ? "No standard name matches." : undefined}
          onSelect={onChosen}
          renderItem={(e) => (
            <span className="flex items-center gap-2">
              <span className="font-medium">{e.name}</span>
              <CategoryChip category={e.category} categoryKey={e.category_key} />
            </span>
          )}
        />
      </div>
      <Button variant="secondary" onClick={onCancel}>
        Cancel
      </Button>
    </div>
  );
}

/**
 * The inline tomato panel (DV10, DV15): what moves, the unit change and how many
 * prices will need a bridge, the survivor (defaulting to more products), the
 * measures to copy, and focus starting on Cancel.
 */
function MergePanel({
  row,
  target,
  needed,
  onCancel,
  onMerged,
}: {
  row: LinkRow;
  target: MergeTarget;
  needed: MergeNeeded;
  onCancel: () => void;
  onMerged: (into: string) => void;
}) {
  const other = needed.other;
  const [survivorId, setSurvivorId] = useState(other.products > row.products ? other.id : row.products > other.products ? row.id : other.id);
  const loserId = survivorId === row.id ? other.id : row.id;
  const preview = useMergePreview(survivorId, loserId, target);
  const merge = useMerge();
  const [unticked, setUnticked] = useState<Set<string>>(new Set());
  const cancel = useRef<HTMLButtonElement>(null);
  useEffect(() => cancel.current?.focus(), []);
  const headingId = `merge-${row.id}-heading`;
  const loserName = survivorId === row.id ? other.name : row.name;
  const movingProducts = survivorId === row.id ? other.products : row.products;
  const p = preview.data;
  const copyable = p ? p.measures.filter((m) => m.copyable) : [];
  const stuck = p ? p.measures.filter((m) => !m.copyable) : [];

  return (
    <div role="group" aria-labelledby={headingId} className="flex flex-col gap-3 rounded-md border border-red-300 bg-red-50 p-3 dark:border-red-900 dark:bg-red-950">
      <h3 id={headingId} className="text-sm font-medium">
        Merge {loserName} into {needed.target_name}?
      </h3>
      <p className="text-sm">
        {plural(movingProducts, "product")} {movingProducts === 1 ? "moves" : "move"}. {loserName} becomes another spelling.
        {p && p.unit_from !== p.unit_to ? ` Units differ (${p.unit_from} → ${p.unit_to})${p.prices_needing_bridge > 0 ? ":" : "."}` : ""}
        {p && p.prices_needing_bridge > 0 ? ` ${plural(p.prices_needing_bridge, "price")} will need a bridge.` : ""}
      </p>
      <fieldset className="flex flex-col gap-1 text-sm">
        <legend className="mb-1">Keep:</legend>
        {[
          { id: other.id, name: other.name, products: other.products },
          { id: row.id, name: row.name, products: row.products },
        ].map((o) => (
          <label key={o.id} className="flex min-h-11 items-center gap-2 lg:min-h-0">
            <input type="radio" name={`${headingId}-keep`} checked={survivorId === o.id} onChange={() => setSurvivorId(o.id)} className={focusRing} />
            {o.name}, {plural(o.products, "product")}
          </label>
        ))}
      </fieldset>
      {copyable.length > 0 ? (
        <fieldset className="flex flex-col gap-1 text-sm">
          <legend className="mb-1">Copy measures:</legend>
          {copyable.map((m) => (
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
              1 {m.label} = {m.canonical_qty} {p?.unit_from}
            </label>
          ))}
        </fieldset>
      ) : null}
      {stuck.length > 0 ? <p className="text-xs">{stuck.map((m) => m.label).join(", ")} can't carry over: the units differ.</p> : null}
      {preview.isError ? <Alert tone="error">{errorMessage(preview.error)}</Alert> : null}
      {merge.isError ? (
        <div role="alert" className={`rounded-md border px-3 py-2 text-sm ${alertTones.error}`}>
          {errorMessage(merge.error)}
        </div>
      ) : null}
      <p className="text-sm">This can't be undone here.</p>
      <div className="grid grid-cols-2 gap-2 lg:flex">
        <Button
          variant="danger"
          disabled={merge.isPending || !p}
          onClick={() =>
            merge.mutate(
              { survivor_id: survivorId, loser_id: loserId, target, copy_measures: copyable.filter((m) => !unticked.has(m.label)).map((m) => m.label) },
              { onSuccess: (done) => onMerged(done.target_name) },
            )
          }
        >
          {merge.isPending ? "Merging…" : "Merge"}
        </Button>
        <Button ref={cancel} variant="secondary" onClick={onCancel} disabled={merge.isPending}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function SkippedFold({ rows }: { rows: LinkRow[] }) {
  const action = useLinkAction();
  return (
    <details className="mt-4 rounded-2xl border border-neutral-200 bg-white px-4 py-2 dark:border-neutral-800 dark:bg-neutral-900">
      <summary className={`min-h-11 cursor-pointer py-2 text-sm font-medium lg:min-h-0 ${focusRing}`}>Skipped ({rows.length})</summary>
      <ul className="divide-y divide-neutral-200 dark:divide-neutral-800">
        {rows.map((row) => (
          <li key={row.id} className="flex items-center justify-between gap-2 py-2 text-sm">
            <span>
              <span className="font-medium">{row.name}</span>
              <span className="text-neutral-600 dark:text-neutral-400"> · {plural(row.products, "product")}</span>
            </span>
            <Button variant="secondary" onClick={() => action.mutate({ id: row.id, action: "reopen" })} disabled={action.isPending}>
              Reopen
            </Button>
          </li>
        ))}
      </ul>
    </details>
  );
}
