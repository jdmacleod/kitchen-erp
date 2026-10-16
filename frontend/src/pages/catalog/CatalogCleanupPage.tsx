import { useState } from "react";
import { Link } from "react-router";
import {
  useApplyCleanup,
  useCleanup,
  useSkipCleanup,
  type CleanupKind,
  type CleanupSuggestion,
} from "../../api/catalogCleanup";
import { errorMessage } from "../../api/client";
import { Badge, inputClass } from "../../components/catalog/fields";
import { CATEGORY_KEYS } from "../../components/CategoryChip";
import { useNotice } from "../../components/Notice";
import { Alert, Button, Card, EmptyState, PageHeader, focusRing } from "../../components/ui";
import { usePageTitle } from "../../lib/usePageTitle";

const muted = "text-neutral-600 dark:text-neutral-400";
const PAGE = 25;

/** The groups, in the order a person should work through them. */
const GROUPS: { kind: CleanupKind; title: string; hint: string }[] = [
  {
    kind: "ingredient_merge",
    title: "Duplicate ingredients",
    hint: "One food entered twice: the other's products, prices and spellings move to the one kept.",
  },
  {
    kind: "usda_link",
    title: "USDA links",
    hint: "USDA foods that name another food: relinked to one that fits, or unlinked. A relink to a raw or dry Foundation record is sure; any other is a guess.",
  },
  {
    kind: "category",
    title: "Categories",
    hint: "Ingredients without a category, or filed where the name says otherwise: a frozen food, a fish under meat. Change the category if the guess is wrong.",
  },
  {
    kind: "ingredient_name",
    title: "Ingredient names",
    hint: "Names the naming rules write differently: lowercase, cook's word order, no brand.",
  },
  {
    kind: "product_name",
    title: "Product names",
    hint: "Names that repeat the brand or the pack, which have fields of their own.",
  },
  {
    kind: "product_brand",
    title: "Brands and kinds",
    hint: "A brand that only says none or describes the food, or a brand on a product sold loose. Loose produce has no brand.",
  },
];

const label = (key: string) => key[0].toUpperCase() + key.slice(1);

const subjectRoute = (s: CleanupSuggestion) =>
  s.kind.startsWith("product")
    ? `/catalog/products/${s.subject_id}`
    : `/catalog/ingredients/${s.subject_id}`;

/**
 * Tidy the catalog (issue 349): what the catalog's checks would have prevented,
 * proposed in groups. Nothing changes without an accept; a skip is remembered.
 * Reached from its Needs you row, not from the navigation.
 */
export function CatalogCleanupPage() {
  usePageTitle("Tidy the catalog");
  const cleanup = useCleanup();
  const items = cleanup.data ?? [];
  return (
    <>
      <PageHeader
        title="Tidy the catalog"
        description="Fixes the catalog's checks found, in groups. Nothing changes until you accept it."
      />
      {cleanup.isPending ? (
        <p role="status" className={`text-sm ${muted}`}>
          Looking through the catalog…
        </p>
      ) : cleanup.isError ? (
        <Alert tone="error">{errorMessage(cleanup.error)}</Alert>
      ) : items.length === 0 ? (
        <EmptyState
          title="Nothing to tidy"
          action={
            <Link to="/" className={`underline ${focusRing}`}>
              Back to Home
            </Link>
          }
        >
          Every check the catalog runs is satisfied, or its suggestion was skipped.
        </EmptyState>
      ) : (
        <div className="flex flex-col gap-6">
          {GROUPS.map((g) => {
            const rows = items.filter((s) => s.kind === g.kind);
            return rows.length ? <Group key={g.kind} {...g} rows={rows} /> : null;
          })}
        </div>
      )}
    </>
  );
}

function Group({
  kind,
  title,
  hint,
  rows,
}: {
  kind: CleanupKind;
  title: string;
  hint: string;
  rows: CleanupSuggestion[];
}) {
  const [shown, setShown] = useState(PAGE);
  // Categories the person chose instead of the guess, by suggestion key.
  const [chosen, setChosen] = useState<Record<string, string>>({});
  const apply = useApplyCleanup();
  const skip = useSkipCleanup();
  const notice = useNotice();
  const visible = rows.slice(0, shown);
  const valueOf = (s: CleanupSuggestion) => chosen[s.key] ?? s.value;
  const canAccept = (s: CleanupSuggestion) => s.ready || (s.choosable && valueOf(s) !== "");
  // A guess is accepted one at a time, unless the person chose its value (issue 372).
  const isGuess = (s: CleanupSuggestion) =>
    s.confidence === "guess" && s.ready && !(s.key in chosen);
  const ready = visible.filter((s) => canAccept(s) && !isGuess(s)).map((s) => s.key);
  const guesses = rows.some(isGuess);
  const busy = apply.isPending || skip.isPending;
  const accept = (keys: string[]) =>
    apply.mutate(
      {
        keys,
        chosen: Object.fromEntries(keys.filter((k) => k in chosen).map((k) => [k, chosen[k]])),
      },
      {
        onSuccess: ({ applied, gone, held }) =>
          notice.show({
            tone: gone.length || held.length ? "info" : "success",
            message: `Applied ${applied} ${applied === 1 ? "fix" : "fixes"}.${
              gone.length ? ` ${gone.length} had changed and were left.` : ""
            }${held.length ? ` ${held.length} to check one at a time.` : ""}`,
          }),
      },
    );
  const headingId = `cleanup-${kind}`;
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id={headingId} className="text-lg font-medium">
          {title} <span className={`text-sm ${muted}`}>({rows.length})</span>
        </h2>
        {ready.length > 1 ? (
          <Button variant="secondary" disabled={busy} onClick={() => accept(ready)}>
            Accept these {ready.length}
          </Button>
        ) : null}
      </div>
      <p className={`text-sm ${muted}`}>{hint}</p>
      {guesses ? (
        <p className={`text-sm ${muted}`}>
          Rows marked “Check this” are guesses: accept them one at a time
          {kind === "category" ? ", or choose the category first" : ""}.
        </p>
      ) : null}
      {apply.isError ? <Alert tone="error">{errorMessage(apply.error)}</Alert> : null}
      {skip.isError ? <Alert tone="error">{errorMessage(skip.error)}</Alert> : null}
      <Card>
        <ul className="divide-y divide-neutral-200 dark:divide-neutral-800" aria-label={title}>
          {visible.map((s) => (
            <li
              key={s.key}
              className="flex flex-col gap-2 py-3 sm:flex-row sm:items-center sm:justify-between"
              data-testid="cleanup-row"
            >
              <Proposal
                suggestion={s}
                guess={isGuess(s)}
                value={valueOf(s)}
                disabled={busy}
                onChoose={(value) => setChosen({ ...chosen, [s.key]: value })}
              />
              <div className="flex shrink-0 gap-2">
                <Button
                  disabled={busy || !canAccept(s)}
                  onClick={() => accept([s.key])}
                  aria-label={`Accept: ${s.subject}`}
                >
                  Accept
                </Button>
                <Button
                  variant="ghost"
                  disabled={busy}
                  onClick={() => skip.mutate([s.key])}
                  aria-label={`Skip: ${s.subject}`}
                >
                  Skip
                </Button>
              </div>
            </li>
          ))}
        </ul>
      </Card>
      {rows.length > shown ? (
        <div>
          <Button variant="ghost" onClick={() => setShown(shown + PAGE)}>
            Show {Math.min(PAGE, rows.length - shown)} more
          </Button>
        </div>
      ) : null}
    </section>
  );
}

/**
 * What a suggestion changes, from what, to what, and why. A guess carries a squash
 * "Check this" badge (spec 08: squash is for anything the system is guessing).
 */
function Proposal({
  suggestion: s,
  guess,
  value,
  disabled,
  onChoose,
}: {
  suggestion: CleanupSuggestion;
  guess: boolean;
  value: string;
  disabled: boolean;
  onChoose: (value: string) => void;
}) {
  return (
    <div className="min-w-0">
      <p className="flex flex-wrap items-center gap-2">
        <Link to={subjectRoute(s)} className={`font-medium underline ${focusRing}`}>
          {s.subject}
        </Link>
        {guess ? <Badge tone="warn">Check this</Badge> : null}
      </p>
      {s.choosable ? (
        <CategoryChoice suggestion={s} value={value} disabled={disabled} onChange={onChoose} />
      ) : (
        <p className="text-sm">
          <span className={muted}>{s.before}</span> → <span>{s.after}</span>
        </p>
      )}
      <p className={`text-xs ${muted}`}>{s.reason}</p>
    </div>
  );
}

/** A category suggestion's value, prefilled with the guess and open to change. */
function CategoryChoice({
  suggestion: s,
  value,
  disabled,
  onChange,
}: {
  suggestion: CleanupSuggestion;
  value: string;
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const id = `cleanup-choice-${s.key}`;
  return (
    <p className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
      <span className={muted}>{s.before}</span> →
      <label className="sr-only" htmlFor={id}>
        Category for {s.subject}
      </label>
      <select
        id={id}
        value={value}
        disabled={disabled}
        onChange={(e) => onChange(e.target.value)}
        className={inputClass}
      >
        <option value="" disabled>
          Choose one
        </option>
        {CATEGORY_KEYS.map((key) => (
          <option key={key} value={key}>
            {label(key)}
          </option>
        ))}
      </select>
    </p>
  );
}
