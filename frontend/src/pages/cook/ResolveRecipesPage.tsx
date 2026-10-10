import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router";
import { errorMessage } from "../../api/client";
import {
  decisionFromProposal,
  recipeKeys,
  resolvedSentence,
  tierLabel,
  useAskModel,
  useResolveQueue,
  type ResolveDecision,
  type ResolveName,
  type ResolveProposal,
  type ResolveQueue,
} from "../../api/recipes";
import { Badge } from "../../components/catalog/fields";
import { DecisionPicker, useNameDecision } from "../../components/cook/NameDecision";
import { useNotice } from "../../components/Notice";
import { Alert, Button, EmptyState, PageHeader, focusRing, secondaryLinkClass } from "../../components/ui";
import { usePageTitle } from "../../lib/usePageTitle";

const muted = "text-neutral-600 dark:text-neutral-400";
const MAX_PROPOSALS = 3;
const TITLES_SHOWN = 2;

type Tally = Record<ResolveDecision["action"], number>;
const noTally: Tally = { matched: 0, created: 0, ignored: 0 };

/** "14 resolved: 11 matched, 2 created, 1 ignored." (10, Finish). */
export function finishSentence(tally: Tally): string {
  const total = tally.matched + tally.created + tally.ignored;
  const parts = (["matched", "created", "ignored"] as const).filter((k) => tally[k] > 0).map((k) => `${tally[k]} ${k}`);
  return `${total} resolved: ${parts.join(", ")}.`;
}

/** "14 names · in 9 recipes". */
export function countSentence(names: number, recipes: number): string {
  return `${names} ${names === 1 ? "name" : "names"} · in ${recipes} ${recipes === 1 ? "recipe" : "recipes"}`;
}

function Breadcrumb() {
  return (
    <nav aria-label="Breadcrumb" className={`mb-2 text-sm ${muted}`}>
      <span>Cook</span> <span aria-hidden="true">/</span>{" "}
      <Link to="/cook/recipes" className={`inline-flex min-h-11 items-center rounded underline lg:min-h-0 ${focusRing}`}>
        Recipes
      </Link>
    </nav>
  );
}

/**
 * Resolve recipe names (`/cook/recipes/resolve`; 10, Resolve recipe names;
 * UI-7.16). Names most-used first with their recipes, up to three proposals
 * badged by tier, the ingredient picker and "Not an ingredient". One decision
 * applies to every recipe using the name: the row leaves, focus moves to the
 * next row, and the Notice (a polite live region) says what happened and how
 * many remain; the finish Notice gives the counts.
 */
export function ResolveRecipesPage() {
  usePageTitle("Resolve recipe names");
  const queue = useResolveQueue();
  const client = useQueryClient();
  const notice = useNotice();
  const [tally, setTally] = useState<Tally>(noTally);
  // Proposals "Ask the model" replaced, by name; they outlive a refetch of the queue.
  const [asked, setAsked] = useState<Record<string, ResolveProposal[]>>({});
  const [focusNext, setFocusNext] = useState<string | null>(null);
  const list = useRef<HTMLUListElement>(null);
  const finished = useRef(false);

  const items = useMemo(() => queue.data?.items ?? [], [queue.data]);

  // After a row leaves, focus goes to the next row's first proposal or its picker (UI-7.12).
  useEffect(() => {
    if (focusNext === null || !list.current) return;
    const row = list.current.querySelector<HTMLElement>(`[data-resolve-row="${CSS.escape(focusNext)}"]`);
    const target = row?.querySelector<HTMLElement>("[data-proposal], [role='combobox']");
    target?.focus();
    setFocusNext(null);
  }, [focusNext, items]);

  // The finish Notice, once, when the last name a decision removed leaves the list.
  const total = tally.matched + tally.created + tally.ignored;
  useEffect(() => {
    if (queue.isSuccess && items.length === 0 && total > 0 && !finished.current) {
      finished.current = true;
      notice.show({ tone: "success", message: finishSentence(tally) });
    }
  }, [queue.isSuccess, items.length, total, tally, notice]);

  const onResolved = (item: ResolveName, index: number, decision: ResolveDecision) => {
    const next = items[index + 1] ?? items[index - 1];
    // The row leaves at once; the refetch the mutation started settles anything else.
    client.setQueryData<ResolveQueue>(recipeKeys.resolve, (current) => {
      if (!current) return current;
      const remaining = current.items.filter((i) => i.name_norm !== item.name_norm);
      return { ...current, items: remaining, names: remaining.length, recipes: new Set(remaining.flatMap((i) => i.recipes.map((r) => r.id))).size };
    });
    setTally((t) => ({ ...t, [decision.action]: t[decision.action] + 1 }));
    if (decision.remaining > 0) {
      notice.show({ tone: "success", message: `${resolvedSentence(item.raw_names[0] ?? item.name_norm, decision)} ${decision.remaining} left.` });
      if (next) setFocusNext(next.name_norm);
    }
  };

  return (
    <>
      <Breadcrumb />
      <PageHeader title="Resolve recipe names" description="Say once which ingredient each name means. It's remembered for every recipe." />

      {queue.isPending ? (
        <p role="status" className={`text-sm ${muted}`}>
          Loading…
        </p>
      ) : queue.isError ? (
        <Alert tone="error">
          <span className="flex flex-wrap items-center justify-between gap-2">
            <span>Couldn&apos;t load the names: {errorMessage(queue.error)}</span>
            <Button variant="secondary" onClick={() => void queue.refetch()}>
              Try again
            </Button>
          </span>
        </Alert>
      ) : items.length === 0 ? (
        <EmptyState title="Every recipe name is resolved">
          <Link to="/cook/recipes" className={secondaryLinkClass}>
            Back to Recipes
          </Link>
        </EmptyState>
      ) : (
        <>
          <p
            className="sticky top-[env(safe-area-inset-top,0px)] z-10 -mx-1 mb-3 bg-neutral-50 px-1 py-2 text-sm text-neutral-700 lg:static dark:bg-neutral-950 dark:text-neutral-300"
            data-testid="resolve-counts"
          >
            {countSentence(queue.data.names, queue.data.recipes)}
          </p>
          <ul ref={list} aria-label="Recipe names to resolve" className="divide-y divide-neutral-200 dark:divide-neutral-800">
            {items.map((item, index) => (
              <ResolveRow
                key={item.name_norm}
                item={item}
                proposals={asked[item.name_norm] ?? item.proposals}
                modelConfigured={queue.data.model_configured}
                onAsked={(proposals) => setAsked((a) => ({ ...a, [item.name_norm]: proposals }))}
                onResolved={(decision) => onResolved(item, index, decision)}
              />
            ))}
          </ul>
        </>
      )}
    </>
  );
}

interface RowProps {
  item: ResolveName;
  proposals: ResolveProposal[];
  modelConfigured: boolean;
  onAsked: (proposals: ResolveProposal[]) => void;
  onResolved: (decision: ResolveDecision) => void;
}

function ResolveRow({ item, proposals, modelConfigured, onAsked, onResolved }: RowProps) {
  const rawName = item.raw_names[0] ?? item.name_norm;
  const { decide, pending, messages, choice, setChoice } = useNameDecision({ nameNorm: item.name_norm, rawName, onResolved });
  const ask = useAskModel();
  const [askedOnce, setAskedOnce] = useState(false);
  const otherSpellings = item.raw_names.slice(1);
  const titles = item.recipes.slice(0, TITLES_SHOWN);
  const more = item.recipes.length - titles.length;
  const shown = proposals.slice(0, MAX_PROPOSALS);
  const id = `resolve-${item.name_norm.replace(/[^a-z0-9]+/gi, "-")}`;

  return (
    <li className="flex flex-col gap-2 py-4" data-resolve-row={item.name_norm} data-testid="resolve-row">
      <div className="flex flex-wrap items-baseline gap-x-2">
        <span className="font-semibold">{rawName}</span>
        {item.name_norm !== rawName.toLowerCase() ? <span className={`text-xs ${muted}`}>({item.name_norm})</span> : null}
        {otherSpellings.length > 0 ? <span className={`text-xs ${muted}`}>also written {otherSpellings.map((s) => `‘${s}’`).join(", ")}</span> : null}
      </div>
      <p className={`text-sm ${muted}`}>
        in {item.recipes.length} {item.recipes.length === 1 ? "recipe" : "recipes"}:{" "}
        {titles.map((r, i) => (
          <span key={r.id}>
            {i > 0 ? ", " : ""}
            <Link to={`/cook/recipes/${r.id}`} className={`rounded underline ${focusRing}`}>
              {r.title}
            </Link>
          </span>
        ))}
        {more > 0 ? ` +${more} more` : ""}
      </p>
      <div className="flex flex-wrap items-center gap-2" role="group" aria-label={`Proposals for ${rawName}`}>
        {shown.map((p) => (
          <Button
            key={`${p.tier}:${p.ingredient_id ?? p.standard_key ?? p.fdc_id ?? p.name}`}
            variant="secondary"
            data-proposal
            disabled={pending}
            className={`gap-2 ${p.tier === "model" ? "border-amber-400 dark:border-amber-600" : ""}`}
            onClick={() => decide(decisionFromProposal(item.name_norm, p))}
          >
            {p.name} <Badge>{tierLabel(p)}</Badge>
          </Button>
        ))}
        {askedOnce && !ask.isPending && shown.length === 0 ? <span className={`text-sm ${muted}`}>No suggestion</span> : null}
        {modelConfigured ? (
          <Button
            variant="ghost"
            disabled={ask.isPending || pending}
            aria-busy={ask.isPending || undefined}
            onClick={() =>
              ask.mutate(item.name_norm, {
                onSuccess: (out) => {
                  setAskedOnce(true);
                  onAsked(out.proposals);
                },
              })
            }
          >
            {ask.isPending ? "Asking…" : "Ask the model"}
          </Button>
        ) : null}
      </div>
      {ask.isError ? <Alert tone="error">Couldn&apos;t ask the model: {errorMessage(ask.error)}</Alert> : null}
      <div className="flex flex-wrap items-end gap-2">
        <div className="min-w-0 flex-1 basis-56">
          <DecisionPicker id={id} rawName={rawName} decide={decide} pending={pending} choice={choice} setChoice={setChoice} />
        </div>
        <Button variant="ghost" disabled={pending} onClick={() => decide({ ignore: true })}>
          Not an ingredient
        </Button>
      </div>
      {messages}
    </li>
  );
}
