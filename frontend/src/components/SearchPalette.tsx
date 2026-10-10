import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Link, useNavigate } from "react-router";
import { useHealth } from "../api/queries";
import { SEARCH_MAX, useSearch, type SearchResult, type SearchResults } from "../api/search";
import { useCurrentUser } from "../auth/context";
import { availableActions, commonActions, matchActions, type PaletteAction } from "../lib/paletteActions";
import { readRecents, rememberRecent } from "../lib/searchRecents";
import { useDebouncedValue } from "../lib/useDebouncedValue";
import { CategoryChip } from "./CategoryChip";
import { RecipeBadge } from "./cook/RecipeBadges";
import { Dialog } from "./Dialog";
import { Button, focusRing } from "./ui";

const GROUPS: { key: keyof SearchResults; label: string }[] = [
  { key: "ingredients", label: "Ingredients" },
  { key: "products", label: "Products" },
  { key: "vendors", label: "Vendors" },
  { key: "recipes", label: "Recipes" },
];

const muted = "text-sm text-neutral-600 dark:text-neutral-400";

type PaletteItem = SearchResult | PaletteAction;

/** Where an action or page lives, shown beside it. */
function section(route: string): string {
  if (route.startsWith("/cook")) return "Cook";
  if (route.startsWith("/shop")) return "Shop";
  if (route.startsWith("/catalog")) return "Catalog";
  if (route.startsWith("/settings")) return "Settings";
  return "Home";
}

/**
 * The search palette (docs/spec/09, Search; 10, Search palette). Opened by the
 * Search button or ⌘K / Ctrl+K. Mounted only while open, so each opening starts
 * from an empty field. Actions and pages (UI-2.9) are matched here, after the
 * search endpoint's groups, and only those whose section is built.
 */
export function SearchPalette({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const ids = useId();
  const input = useRef<HTMLInputElement>(null);
  const [q, setQ] = useState("");
  const debounced = useDebouncedValue(q, 150);
  const search = useSearch(debounced);
  const typed = q.trim().length > 0;
  // The results on screen answer the text in the field: the debounce has caught
  // up and the query is not still showing the previous search's answer.
  const current = typed && debounced.trim() === q.trim() && !search.isPlaceholderData;
  // Choosing a result navigates, so focus goes to the new page, not back here.
  const returnFocus = useRef(true);

  // Before typing, the last five results opened on this device (G15).
  const [recents] = useState(readRecents);

  // Actions for the sections that are built, as the navigation shows them.
  const admin = useCurrentUser().role === "admin";
  const features = useHealth().data?.features;
  const available = useMemo(() => availableActions(features, admin), [features, admin]);
  const actions = useMemo(() => (typed ? matchActions(q, available) : commonActions(available)), [typed, q, available]);

  // Groups in their fixed order, empty ones hidden; one flat list for the keys.
  // Search results are shown only when they answer the text in the field, so an
  // earlier search's results are never offered; actions answer at once.
  const groups = useMemo(() => {
    const entities: { key: string; label: string; items: PaletteItem[] }[] = typed
      ? current
        ? GROUPS.map((g) => ({ ...g, items: search.data?.[g.key] ?? [] })).filter((g) => g.items.length > 0)
        : []
      : recents.length > 0
        ? [{ key: "recent", label: "Recent", items: recents }]
        : [];
    const shown = actions.length > 0 ? [...entities, { key: "actions", label: "Actions", items: actions }] : entities;
    // Each group's offset into the flat list the arrow keys walk.
    return shown.map((g, i) => ({ ...g, start: shown.slice(0, i).reduce((n, prev) => n + prev.items.length, 0) }));
  }, [search.data, typed, current, recents, actions]);
  const flat = useMemo(() => groups.flatMap((g) => g.items), [groups]);
  const [active, setActive] = useState(0);
  const [shownFor, setShownFor] = useState(flat);
  if (shownFor !== flat) {
    // New results: the first one is selected again.
    setShownFor(flat);
    setActive(0);
  }

  const optionId = (i: number) => `${ids}-option-${i}`;
  useEffect(() => {
    document.getElementById(optionId(active))?.scrollIntoView?.({ block: "nearest" });
  });

  const open = (result: PaletteItem) => {
    // Recents are things, not actions (G15).
    if (result.kind !== "action") rememberRecent(result);
    returnFocus.current = false;
    onClose();
    navigate(result.route);
  };

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (flat.length === 0) return;
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActive((i) => (i + 1) % flat.length);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActive((i) => (i - 1 + flat.length) % flat.length);
    } else if (event.key === "Enter") {
      event.preventDefault();
      open(flat[Math.min(active, flat.length - 1)]);
    }
  };

  const showResults = flat.length > 0;
  const nothingFound = typed && current && !search.isError && flat.length === 0;

  return (
    <Dialog open onClose={onClose} labelledBy={`${ids}-title`} placement="screen" initialFocus={input} returnFocus={returnFocus}>
      <h2 id={`${ids}-title`} className="sr-only">
        Search
      </h2>
      <div className="flex items-center gap-3 border-b border-neutral-200 px-4 dark:border-neutral-800">
        <input
          ref={input}
          // Plain text: type="search" draws the browser's own clear button, in a
          // colour outside the palette, right beside the Esc hint.
          type="text"
          enterKeyHint="search"
          autoComplete="off"
          role="combobox"
          aria-label="Search ingredients, products, vendors, recipes and actions"
          aria-expanded={showResults}
          aria-controls={`${ids}-listbox`}
          aria-autocomplete="list"
          aria-activedescendant={showResults ? optionId(active) : undefined}
          maxLength={SEARCH_MAX}
          value={q}
          onChange={(e) => setQ(e.target.value)}
          onKeyDown={onKeyDown}
          placeholder="Search"
          className={`my-2 min-h-11 lg:min-h-10 w-full rounded-md bg-transparent px-1 text-base placeholder:text-neutral-500 ${focusRing}`}
        />
        {/* A phone has no Escape key: the full-screen palette closes here. */}
        <Button variant="ghost" className="shrink-0 lg:hidden" onClick={onClose}>
          Cancel
        </Button>
        <kbd className="hidden shrink-0 rounded border border-neutral-300 px-1.5 text-xs text-neutral-600 dark:border-neutral-700 dark:text-neutral-400 lg:inline">
          Esc
        </kbd>
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto p-2 lg:max-h-[60vh]">
        {!typed && recents.length === 0 ? (
          <p className={`px-2 py-3 ${muted}`}>Type a product, ingredient, vendor or barcode.</p>
        ) : typed && search.isError ? (
          <div role="alert" className="flex flex-wrap items-center justify-between gap-2 px-2 py-3 text-sm">
            <span>Search isn&apos;t working right now.</span>
            <Button variant="secondary" onClick={() => void search.refetch()}>
              Try again
            </Button>
          </div>
        ) : typed && !current ? (
          <p role="status" className={`px-2 py-3 ${muted}`}>
            Searching…
          </p>
        ) : nothingFound ? (
          <p role="status" className={`flex flex-wrap items-center gap-2 px-2 py-3 ${muted}`}>
            <span>No matches for &lsquo;{q.trim()}&rsquo;.</span>
            <Link to="/catalog/products" onClick={onClose} className={`rounded font-medium underline ${focusRing}`}>
              Add product
            </Link>
          </p>
        ) : null}
        {showResults ? (
          <div id={`${ids}-listbox`} role="listbox" aria-label="Search results">
            {groups.map((g) => (
              <div key={g.key} role="group" aria-labelledby={`${ids}-${g.key}`} className="py-1">
                <p id={`${ids}-${g.key}`} className="px-2 py-1 text-xs font-semibold text-neutral-600 dark:text-neutral-400">
                  {g.label}
                </p>
                {g.items.map((r, j) => {
                  const i = g.start + j;
                  const selected = i === active;
                  return (
                    <div
                      key={`${r.kind}-${r.id}`}
                      id={optionId(i)}
                      role="option"
                      aria-selected={selected}
                      onMouseMove={() => setActive(i)}
                      onClick={() => open(r)}
                      className={`flex min-h-11 cursor-pointer items-center justify-between gap-3 rounded-md px-2 py-1.5 ${
                        selected ? "bg-neutral-100 dark:bg-neutral-800" : ""
                      }`}
                    >
                      <span className="min-w-0 truncate font-medium">{r.label}</span>
                      {r.kind === "action" ? (
                        <span className={`shrink-0 ${muted}`}>{section(r.route)}</span>
                      ) : r.kind === "ingredient" ? (
                        <CategoryChip category={r.detail} categoryKey={r.category_key} />
                      ) : r.kind === "recipe" ? (
                        // The title with its badge in words (UI-7.17); the path is the hover title.
                        <span className="flex min-w-0 shrink-0 items-center gap-2">
                          {r.badge ? <RecipeBadge badge={r.badge} /> : null}
                        </span>
                      ) : r.detail ? (
                        <span className={`min-w-0 truncate ${muted}`}>{r.detail}</span>
                      ) : null}
                    </div>
                  );
                })}
              </div>
            ))}
          </div>
        ) : null}
      </div>

      <p className={`hidden border-t border-neutral-200 px-4 py-2 text-xs lg:block dark:border-neutral-800 ${muted}`}>
        ↑ ↓ to move · Enter to open · Esc to close
      </p>
    </Dialog>
  );
}
