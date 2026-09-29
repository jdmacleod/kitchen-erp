import { useEffect, useId, useRef, useState } from "react";
import { errorMessage } from "../../api/client";
import { geoErrorMessage, priceScopeLabel, type PriceScope } from "../../api/geo";
import { useAcceptAll, useDecideSuggestion, useSuggestionSummary, useSuggestions, type Suggestion, type SuggestionField, type SuggestionValue } from "../../api/suggestions";
import { describeOpeningHours } from "../../lib/openingHours";
import { Disclosure } from "../catalog/fields";
import { Drawer } from "../Drawer";
import { useNotice } from "../Notice";
import { Alert, Button, focusRing } from "../ui";

const muted = "text-neutral-600 dark:text-neutral-400";

const FIELD: Record<SuggestionField, string> = {
  website: "Website",
  brand: "Brand",
  wikidata: "Wikidata",
  phone: "Phone",
  address: "Address",
  opening_hours: "Opening hours",
  osm: "OpenStreetMap link",
  name: "Name",
  price_scope: "Pricing",
};

/** A value the way the app shows it elsewhere, never raw (design D9). */
export function showValue(field: SuggestionField, value: SuggestionValue): string {
  if (value === null || value === "") return "empty";
  if (typeof value === "object") return `${value.type} ${value.id}`;
  if (field === "opening_hours") return describeOpeningHours(value);
  if (field === "price_scope") return priceScopeLabel[value as PriceScope] ?? value;
  return value;
}

const where = (s: Suggestion) => s.location_name ?? s.vendor_name;

interface Tally {
  accepted: number;
  kept: number;
  rejected: number;
}

/** "10 accepted, 2 kept yours, 1 rejected" for the Notice when review closes. */
export function tallyMessage(t: Tally): string | null {
  const parts = [
    t.accepted ? `${t.accepted} accepted` : null,
    t.kept ? `${t.kept} kept yours` : null,
    t.rejected ? `${t.rejected} rejected` : null,
  ].filter(Boolean);
  return parts.length ? `${parts.join(", ")}.` : null;
}

/**
 * Review what an outside tool proposed (spec 03 §1F; design D3, D8, D9, D13,
 * D15). One vendor at a time, largest first; a row leaves once decided and
 * focus moves to the next. A row whose field changed since it was proposed
 * shows all three values and makes keeping yours the obvious choice.
 */
export function SuggestionReviewDrawer({ initialVendorId = null, onClose }: { initialVendorId?: string | null; onClose: () => void }) {
  const pickerId = useId();
  const notice = useNotice();
  const summary = useSuggestionSummary();
  const [chosen, setChosen] = useState<string | null>(initialVendorId);
  const vendors = summary.data?.vendors ?? [];
  // A vendor whose last row was decided drops out of the summary; the next one opens.
  const vendorId = chosen && vendors.some((v) => v.vendor_id === chosen) ? chosen : (vendors[0]?.vendor_id ?? null);
  const list = useSuggestions(vendorId);
  const decide = useDecideSuggestion();
  const acceptAll = useAcceptAll();
  const [tally, setTally] = useState<Tally>({ accepted: 0, kept: 0, rejected: 0 });
  const [said, setSaid] = useState("");
  const body = useRef<HTMLDivElement>(null);
  const [focusAt, setFocusAt] = useState<number | null>(null);

  const rows = list.data ?? [];
  const vendorName = vendors.find((v) => v.vendor_id === vendorId)?.name ?? rows[0]?.vendor_name ?? "";
  const tools = [...new Set(rows.map((r) => `${r.tool} ${r.tool_version}`))];
  const fresh = rows.filter((r) => !r.stale);

  // After a row leaves, focus the row now in its place, or the heading when none are left.
  useEffect(() => {
    if (focusAt === null || list.isFetching) return;
    const buttons = [...(body.current?.querySelectorAll<HTMLElement>("[data-row] button:not([disabled])") ?? [])];
    const rowsNow = [...(body.current?.querySelectorAll<HTMLElement>("[data-row]") ?? [])];
    const target = rowsNow[Math.min(focusAt, rowsNow.length - 1)];
    const first = target?.querySelector<HTMLElement>("button:not([disabled])") ?? buttons[0];
    (first ?? body.current?.querySelector<HTMLElement>("h3, select"))?.focus();
    // Focus is the effect's whole job; clearing the request is part of it.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setFocusAt(null);
  }, [focusAt, list.isFetching, rows.length]);

  const close = () => {
    const message = tallyMessage(tally);
    if (message) notice.show({ tone: "success", message });
    onClose();
  };

  const act = (s: Suggestion, index: number, action: "accept" | "reject", override = false, kept = false) => {
    decide.mutate(
      { id: s.id, action, override },
      {
        onSuccess: (d) => {
          const label = `${FIELD[s.field].toLowerCase()} for ${where(s)}`;
          if (d.outcome === "stale") {
            // It changed after the list was fetched: the row stays, now showing all three values.
            setSaid(`The ${label} changed since it was proposed.`);
            return;
          }
          setFocusAt(index);
          if (d.outcome === "accepted") {
            setTally((t) => ({ ...t, accepted: t.accepted + 1 }));
            setSaid(`Accepted ${label}.`);
          } else if (kept) {
            setTally((t) => ({ ...t, kept: t.kept + 1 }));
            setSaid(`Kept your ${label}.`);
          } else {
            setTally((t) => ({ ...t, rejected: t.rejected + 1 }));
            setSaid(`Rejected ${label}.`);
          }
        },
      },
    );
  };

  // Grouped by location, the vendor's own facts first (D13: headings and rows, no cards).
  const groups = new Map<string, Suggestion[]>();
  for (const r of rows) {
    const key = r.location_name ?? "";
    groups.set(key, [...(groups.get(key) ?? []), r]);
  }
  let index = -1;

  return (
    <Drawer title="Vendor suggestions" thing="review" dirty={false} onClose={close} formId="" primaryLabel="" actions={false}>
      <div ref={body} className="flex flex-col gap-4">
        <p aria-live="polite" className="sr-only">
          {said}
        </p>
        {summary.isPending ? (
          <p role="status" className={`text-sm ${muted}`}>
            Loading…
          </p>
        ) : summary.isError ? (
          <Alert tone="error">{errorMessage(summary.error)}</Alert>
        ) : vendors.length === 0 ? (
          <p className="text-sm">Nothing waits for review.</p>
        ) : (
          <>
            <div className="flex flex-col gap-1">
              <label htmlFor={pickerId} className="text-sm font-medium">
                Vendor
              </label>
              <select
                id={pickerId}
                value={vendorId ?? ""}
                onChange={(e) => setChosen(e.target.value)}
                className={`min-h-11 rounded-md border border-neutral-300 bg-white px-2 text-sm lg:min-h-10 dark:border-neutral-700 dark:bg-neutral-900 ${focusRing}`}
              >
                {vendors.map((v) => (
                  <option key={v.vendor_id} value={v.vendor_id}>
                    {v.name} · {v.count}
                  </option>
                ))}
              </select>
              {tools.length ? <p className={`text-xs ${muted}`}>From {tools.join(", ")}. Nothing changes until you accept it.</p> : null}
            </div>
            {decide.isError ? <Alert tone="error">{geoErrorMessage(decide.error)}</Alert> : null}
            {acceptAll.isError ? <Alert tone="error">{geoErrorMessage(acceptAll.error)}</Alert> : null}
            {list.isPending ? (
              <p role="status" className={`text-sm ${muted}`}>
                Loading…
              </p>
            ) : list.isError ? (
              <Alert tone="error">{errorMessage(list.error)}</Alert>
            ) : (
              [...groups.entries()].map(([location, items]) => (
                <section key={location || "vendor"} aria-label={location || vendorName}>
                  <h3 className="text-sm font-semibold">{location || vendorName}</h3>
                  <ul className="divide-y divide-neutral-200 dark:divide-neutral-800">
                    {items.map((s) => {
                      index += 1;
                      const i = index;
                      const label = `${FIELD[s.field].toLowerCase()} for ${where(s)}`;
                      const busy = decide.isPending || acceptAll.isPending;
                      return (
                        <li key={s.id} data-row className="flex flex-col gap-2 py-3 text-sm">
                          <p className="font-medium">{FIELD[s.field]}</p>
                          {s.stale ? (
                            <>
                              <p className="text-xs font-semibold text-amber-800 dark:text-amber-300">Changed since proposed</p>
                              <p className="flex flex-col gap-0.5 text-sm sm:flex-row sm:flex-wrap sm:gap-x-2">
                                <span>Was {showValue(s.field, s.expected)}</span>
                                <span>Now {showValue(s.field, s.current)}</span>
                                <span>Proposed {showValue(s.field, s.proposed)}</span>
                              </p>
                            </>
                          ) : (
                            <p>
                              <span className="font-semibold">{showValue(s.field, s.proposed)}</span>
                              {s.current !== null ? <span className={`block text-xs ${muted}`}>Replaces {showValue(s.field, s.current)}</span> : null}
                            </p>
                          )}
                          <div className={`flex flex-wrap items-center gap-x-2 text-xs ${muted}`}>
                            <a href={s.source_url} target="_blank" rel="noopener noreferrer" className="underline">
                              {s.source_domain} ↗
                            </a>
                            {s.evidence ? (
                              <Disclosure summary="Why" className="text-xs">
                                {/* Plain text only: tool output is never markup (UI-3.15). */}
                                <p className="whitespace-pre-wrap">{s.evidence}</p>
                              </Disclosure>
                            ) : null}
                          </div>
                          <div className="flex flex-wrap gap-2">
                            {s.stale ? (
                              <>
                                <Button disabled={busy} onClick={() => act(s, i, "reject", false, true)} aria-label={`Keep my ${label}`}>
                                  Keep mine
                                </Button>
                                <Button variant="ghost" disabled={busy} onClick={() => act(s, i, "accept", true)} aria-label={`Replace my ${label} with the proposed one`}>
                                  Replace with proposed
                                </Button>
                              </>
                            ) : (
                              <>
                                <Button variant="secondary" disabled={busy} onClick={() => act(s, i, "accept")} aria-label={`Accept ${label}`}>
                                  Accept
                                </Button>
                                <Button variant="ghost" disabled={busy} onClick={() => act(s, i, "reject")} aria-label={`Reject ${label}`}>
                                  Reject
                                </Button>
                              </>
                            )}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                </section>
              ))
            )}
            {vendorId && fresh.length > 1 ? (
              <div className="flex flex-wrap items-center gap-2 border-t border-neutral-200 pt-3 dark:border-neutral-800">
                <Button
                  variant="secondary"
                  disabled={decide.isPending || acceptAll.isPending}
                  onClick={() =>
                    acceptAll.mutate(vendorId, {
                      onSuccess: (r) => {
                        setFocusAt(0);
                        setTally((t) => ({ ...t, accepted: t.accepted + r.accepted }));
                        setSaid(`Accepted ${r.accepted} for ${vendorName}.`);
                      },
                    })
                  }
                >
                  Accept all for {vendorName}
                </Button>
                {rows.length > fresh.length ? <span className={`text-xs ${muted}`}>Changed rows are left for you.</span> : null}
              </div>
            ) : null}
          </>
        )}
      </div>
    </Drawer>
  );
}
