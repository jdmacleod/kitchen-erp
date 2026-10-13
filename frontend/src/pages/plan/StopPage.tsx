import { useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { Link, useParams } from "react-router";
import { errorMessage, isApiError, newIdempotencyKey } from "../../api/client";
import {
  chosenVariant,
  useGetHere,
  useStop,
  useStopDone,
  useTrip,
  type CheaperElsewhereItem,
  type StopDone,
  type StopScreen,
  type StopScreenItem,
} from "../../api/trips";
import { useNotice } from "../../components/Notice";
import { CheaperRow, StopItemRow } from "../../components/plan/StopRows";
import {
  Alert,
  Button,
  focusRing,
  PageHeader,
  primaryLinkClass,
  secondaryLinkClass,
  sectionHeading,
} from "../../components/ui";
import { add, formatMoney } from "../../lib/decimal";
import { formatDateTime } from "../../lib/format";
import { usePageTitle } from "../../lib/usePageTitle";

const muted = "text-neutral-600 dark:text-neutral-400";

const stopPath = (planId: string, seq: number) => `/plan/trips/${planId}/stop/${seq}`;

// --- the ticks, held on this device until "Done here" records them --------------------

const storageKey = (planId: string, seq: number) => `kerp.stop.${planId}.${seq}`;

function readTicks(planId: string, seq: number): string[] {
  try {
    const raw = window.sessionStorage.getItem(storageKey(planId, seq));
    const parsed: unknown = raw ? JSON.parse(raw) : [];
    return Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : [];
  } catch {
    return [];
  }
}

function writeTicks(planId: string, seq: number, ids: readonly string[]): void {
  try {
    if (ids.length === 0) window.sessionStorage.removeItem(storageKey(planId, seq));
    else window.sessionStorage.setItem(storageKey(planId, seq), JSON.stringify(ids));
  } catch {
    // Private mode or storage disabled: the ticks live only as long as the screen.
  }
}

/** The sum of the checked items at their edited or planned cost. */
export function basketTotal(
  items: readonly StopScreenItem[],
  checked: ReadonlySet<string>,
): string {
  return items.filter((i) => checked.has(i.id)).reduce((sum, i) => add(sum, i.effective_cost), "0");
}

function BackToTrip({ planId }: { planId: string }) {
  return (
    <nav aria-label="Breadcrumb" className={`mb-2 text-sm ${muted}`}>
      <Link
        to={`/plan/trips/${planId}`}
        className={`inline-flex min-h-11 items-center rounded-sm underline lg:min-h-0 ${focusRing}`}
      >
        <span aria-hidden="true">←&nbsp;</span>Back to the trip
      </Link>
    </nav>
  );
}

/**
 * At the store (`/plan/trips/:id/stop/:seq`; 10, At the store; UI-4.8,
 * UI-8.17–8.20): one stop of the chosen plan. A phone screen first; at 1024px
 * and wider the same layout in one centred column.
 */
export function StopPage() {
  const { id = "", seq: seqText = "" } = useParams();
  const seq = Number.parseInt(seqText, 10);
  const stop = useStop(id, seq);
  usePageTitle(stop.data ? `Stop ${stop.data.seq} of ${stop.data.stop_count}` : "At the store");

  if (!Number.isInteger(seq) || seq < 1) {
    return (
      <Column>
        <BackToTrip planId={id} />
        <PageHeader title="At the store" />
        <Alert tone="error">There is no such stop on this trip.</Alert>
      </Column>
    );
  }
  if (stop.isPending) {
    return (
      <Column>
        <BackToTrip planId={id} />
        <PageHeader title={`Stop ${seq}`} />
        <p role="status" className={`text-sm ${muted}`}>
          Loading…
        </p>
      </Column>
    );
  }
  if (stop.isError) {
    const e = stop.error;
    const notChosen = isApiError(e) && e.code === "plan_not_chosen";
    const gone = isApiError(e) && e.status === 404;
    return (
      <Column>
        <BackToTrip planId={id} />
        <PageHeader title={`Stop ${seq}`} />
        {notChosen ? (
          <Alert tone="info">
            <span
              className="flex flex-wrap items-center justify-between gap-2"
              data-testid="plan-not-chosen"
            >
              <span>
                Choose a plan first. The stops are worked through once one of the three plans is
                chosen.
              </span>
              <Link to={`/plan/trips/${id}`} className={secondaryLinkClass}>
                Open the trip
              </Link>
            </span>
          </Alert>
        ) : (
          <Alert tone="error">
            <span className="flex flex-wrap items-center justify-between gap-2">
              <span>
                {gone
                  ? "There is no such stop on this trip."
                  : `Couldn't load the stop: ${errorMessage(e)}`}
              </span>
              {gone ? null : (
                <Button variant="secondary" onClick={() => void stop.refetch()}>
                  Try again
                </Button>
              )}
            </span>
          </Alert>
        )}
      </Column>
    );
  }
  return (
    <StopView
      key={`${stop.data.plan_id}:${stop.data.seq}`}
      stop={stop.data}
      refetch={() => void stop.refetch()}
    />
  );
}

function Column({ children }: { children: ReactNode }) {
  return <div className="mx-auto w-full max-w-xl">{children}</div>;
}

function StopView({ stop, refetch }: { stop: StopScreen; refetch: () => void }) {
  const planId = stop.plan_id;
  const notice = useNotice();
  const trip = useTrip(planId);
  const getHere = useGetHere();
  const done = stop.done_at !== null;
  const ended = !done && (stop.plan_status === "done" || stop.plan_status === "abandoned");
  const readOnly = done || ended;

  const [ticks, setTicks] = useState<string[]>(() => readTicks(planId, stop.seq));
  const [announce, setAnnounce] = useState("");
  const [notHere, setNotHere] = useState<Set<string>>(() => new Set());
  const [moveErrors, setMoveErrors] = useState<Record<string, string>>({});
  // One key per move, kept until it lands, so a retried "Get here" replays.
  const moveKeys = useRef(new Map<string, string>());

  // Only ids still on this stop count; a done stop shows what the list recorded.
  const itemIds = useMemo(() => new Set(stop.items.map((i) => i.id)), [stop.items]);
  const checked = useMemo(
    () =>
      done
        ? new Set(stop.items.filter((i) => i.checked_at !== null).map((i) => i.id))
        : new Set(ticks.filter((t) => itemIds.has(t))),
    [done, stop.items, ticks, itemIds],
  );
  useEffect(() => {
    if (!readOnly) writeTicks(planId, stop.seq, [...checked]);
  }, [checked, planId, stop.seq, readOnly]);

  const toGet = stop.items.filter((i) => !checked.has(i.id));
  const inBasket = stop.items.filter((i) => checked.has(i.id));
  const total = basketTotal(stop.items, checked);

  const nextStopName = useMemo(() => {
    if (stop.next_seq === null || !trip.data) return null;
    return (
      chosenVariant(trip.data)?.stops.find((s) => s.seq === stop.next_seq)?.vendor_location_name ??
      null
    );
  }, [stop.next_seq, trip.data]);

  const onToggle = (item: StopScreenItem) => {
    const was = checked.has(item.id);
    const next = was
      ? ticks.filter((t) => t !== item.id)
      : [...ticks.filter((t) => itemIds.has(t)), item.id];
    setTicks(next);
    const left = stop.items.length - next.filter((t) => itemIds.has(t)).length;
    setAnnounce(
      was ? `Unchecked ${item.label}. ${left} to get.` : `Checked ${item.label}. ${left} to get.`,
    );
  };

  const onConflict = (e: unknown) => {
    refetch();
    if (isApiError(e) && e.code === "stop_done")
      notice.show({ tone: "info", message: "This stop was already recorded; showing it." });
  };

  const onGetHere = (item: CheaperElsewhereItem) => {
    let key = moveKeys.current.get(item.id);
    if (!key) {
      key = newIdempotencyKey();
      moveKeys.current.set(item.id, key);
    }
    setMoveErrors((m) => {
      const rest = { ...m };
      delete rest[item.id];
      return rest;
    });
    getHere.mutate(
      { id: planId, seq: stop.seq, itemId: item.id, key },
      {
        onSuccess: () => {
          moveKeys.current.delete(item.id);
          notice.show({
            tone: "success",
            message: `Moved ${item.label} here from stop ${item.at_seq}.`,
            timeoutMs: 3000,
          });
        },
        onError: (e) => {
          if (isApiError(e) && e.code === "not_offered_here") {
            moveKeys.current.delete(item.id);
            setNotHere((s) => new Set(s).add(item.id));
          } else if (isApiError(e) && e.status === 409) {
            moveKeys.current.delete(item.id);
            onConflict(e);
          } else setMoveErrors((m) => ({ ...m, [item.id]: errorMessage(e) }));
        },
      },
    );
  };

  // "Next stop: {store}" while stops remain, "Back to the trip" after the last (UI-8.18).
  const nextActionFor = (nextSeq: number | null) =>
    nextSeq !== null
      ? {
          label: nextStopName ? `Next stop: ${nextStopName}` : "Next stop",
          to: stopPath(planId, nextSeq),
        }
      : { label: "Back to the trip", to: `/plan/trips/${planId}` };
  const nextAction = nextActionFor(stop.next_seq);

  const onRecorded = (result: StopDone, recordedTotal: string) => {
    writeTicks(planId, stop.seq, []);
    setTicks([]);
    const lines = result.lines_recorded;
    const message =
      result.purchase_id === null
        ? `Moved on from ${stop.location.name}; nothing bought.`
        : `Recorded ${lines} ${lines === 1 ? "line" : "lines"} at ${stop.location.name} · ${formatMoney(recordedTotal)}.`;
    notice.show({
      tone: "success",
      message,
      action: nextActionFor(result.next_seq),
      focusAction: true,
    });
  };

  // Later stops' items, one section per stop.
  const cheaperBySeq = useMemo(() => {
    const groups = new Map<number, CheaperElsewhereItem[]>();
    for (const c of stop.cheaper_elsewhere)
      groups.set(c.at_seq, [...(groups.get(c.at_seq) ?? []), c]);
    return [...groups.entries()].sort((a, b) => a[0] - b[0]);
  }, [stop.cheaper_elsewhere]);

  return (
    <Column>
      <div className={readOnly ? "" : "pb-40 lg:pb-0"}>
        <BackToTrip planId={planId} />
        <PageHeader
          title={`Stop ${stop.seq} of ${stop.stop_count} · ${stop.home_base_name} list`}
          description={
            <span className="text-base font-medium text-neutral-900 dark:text-neutral-100">
              {stop.location.name}
            </span>
          }
        />
        <div className="-mt-5 mb-4 flex flex-col gap-1 text-sm">
          {stop.location.address ? <p className={muted}>{stop.location.address}</p> : null}
          {done ? (
            <p data-testid="stop-done" className="flex flex-wrap items-center gap-x-2 gap-y-1">
              <span>
                Done {formatDateTime(stop.done_at)} · {stop.lines_recorded ?? 0}{" "}
                {stop.lines_recorded === 1 ? "line" : "lines"} recorded
                {stop.actual_cost !== null ? ` · ${formatMoney(stop.actual_cost)}` : ""}
              </span>
              {stop.purchase_id ? (
                <Link
                  to={`/shop/purchases/${stop.purchase_id}`}
                  className={`inline-flex min-h-11 items-center rounded-sm underline lg:min-h-0 ${focusRing}`}
                >
                  Open the purchase
                </Link>
              ) : null}
            </p>
          ) : (
            <p
              data-testid="stop-progress"
              className="flex flex-wrap items-baseline justify-between gap-x-4 gap-y-1 rounded-md bg-neutral-100 px-3 py-2 tabular-nums dark:bg-neutral-900"
            >
              <span>
                <span className="font-medium">
                  {inBasket.length} of {stop.items.length}
                </span>{" "}
                in the basket{inBasket.length > 0 ? ` · ${formatMoney(total)}` : ""}
              </span>
              <span data-testid="estimated-here">
                About {formatMoney(stop.estimated_here)} here
              </span>
            </p>
          )}
          {ended ? (
            <Alert tone="info">
              This trip is {stop.plan_status === "done" ? "done" : "abandoned"}; nothing more is
              recorded here.
            </Alert>
          ) : null}
        </div>

        <p className="sr-only" aria-live="polite" aria-atomic="true">
          {announce}
        </p>

        <section aria-labelledby="get-here-heading" className="mb-6">
          <h2 id="get-here-heading" className={`${sectionHeading} mb-1`}>
            Get here
          </h2>
          {stop.items.length === 0 ? (
            <p className={`text-sm ${muted}`}>Nothing is planned at this stop.</p>
          ) : (
            <>
              <ul aria-label="To get here" className="flex flex-col">
                {toGet.map((item) => (
                  <StopItemRow
                    key={item.id}
                    item={item}
                    planId={planId}
                    seq={stop.seq}
                    checked={false}
                    readOnly={readOnly}
                    onToggle={onToggle}
                    onConflict={onConflict}
                  />
                ))}
              </ul>
              {inBasket.length > 0 ? (
                <ul
                  aria-label="In the basket"
                  className="mt-1 flex flex-col border-t border-neutral-300 pt-1 dark:border-neutral-700"
                >
                  {inBasket.map((item) => (
                    <StopItemRow
                      key={item.id}
                      item={item}
                      planId={planId}
                      seq={stop.seq}
                      checked
                      readOnly={readOnly}
                      onToggle={onToggle}
                      onConflict={onConflict}
                    />
                  ))}
                </ul>
              ) : null}
            </>
          )}
        </section>

        {cheaperBySeq.map(([at, items]) => (
          <section
            key={at}
            aria-labelledby={`cheaper-${at}`}
            className="mb-6"
            data-testid="cheaper-section"
          >
            <h2 id={`cheaper-${at}`} className={sectionHeading}>
              Cheaper at stop {at}
            </h2>
            <p className={`mb-1 text-sm ${muted}`}>
              Planned for {items[0].at_location_name}, and this store sells them too.
            </p>
            <ul className="flex flex-col">
              {items.map((item) => (
                <CheaperRow
                  key={item.id}
                  item={item}
                  busy={getHere.isPending && getHere.variables?.itemId === item.id}
                  notHere={notHere.has(item.id)}
                  error={moveErrors[item.id] ?? null}
                  readOnly={readOnly}
                  onGetHere={onGetHere}
                />
              ))}
            </ul>
          </section>
        ))}

        {readOnly ? (
          <div className="mt-2 flex flex-wrap gap-2">
            <Link to={nextAction.to} className={primaryLinkClass}>
              {nextAction.label}
            </Link>
          </div>
        ) : (
          <DoneFooter
            stop={stop}
            checkedIds={[...checked]}
            total={total}
            onRecorded={onRecorded}
            onConflict={onConflict}
          />
        )}
      </div>
    </Column>
  );
}

interface DoneFooterProps {
  stop: StopScreen;
  checkedIds: string[];
  total: string;
  onRecorded: (result: StopDone, total: string) => void;
  onConflict: (e: unknown) => void;
}

/**
 * "Done here · record purchase", anchored in the thumb zone (UI-8.18). It
 * confirms inline, never in a browser dialog, with focus on "Not yet". A failed
 * record shows its alert in the panel and keeps the ticks; the key is held for
 * this set of ticks, so a retry after a lost answer replays rather than records twice.
 */
function DoneFooter({ stop, checkedIds, total, onRecorded, onConflict }: DoneFooterProps) {
  const record = useStopDone();
  const [asking, setAsking] = useState(false);
  const notYet = useRef<HTMLButtonElement>(null);
  const opener = useRef<HTMLButtonElement>(null);
  const held = useRef<{ signature: string; key: string } | null>(null);
  const n = checkedIds.length;
  const nothing = n === 0;

  useEffect(() => {
    if (asking) notYet.current?.focus();
  }, [asking]);

  const close = () => {
    setAsking(false);
    record.reset();
    requestAnimationFrame(() => opener.current?.focus());
  };

  const submit = () => {
    const ids = [...checkedIds].sort();
    const signature = ids.join(",");
    if (held.current?.signature !== signature)
      held.current = { signature, key: newIdempotencyKey() };
    record.mutate(
      { id: stop.plan_id, seq: stop.seq, checkedItemIds: ids, key: held.current.key },
      {
        onSuccess: (result) => {
          held.current = null;
          setAsking(false);
          onRecorded(result, total);
        },
        onError: (e) => {
          if (isApiError(e) && e.code === "stop_done") {
            held.current = null;
            setAsking(false);
            onConflict(e);
          }
        },
      },
    );
  };

  const failure = record.error;
  const ended = isApiError(failure) && failure.code === "plan_ended";

  return (
    <div
      data-testid="stop-footer"
      className="fixed inset-x-0 bottom-0 z-20 border-t border-neutral-200 bg-neutral-50 px-4 pt-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] lg:sticky lg:bottom-4 lg:mt-4 lg:rounded-lg lg:border lg:bg-white lg:pb-3 dark:border-neutral-800 dark:bg-neutral-950 lg:dark:bg-neutral-900"
    >
      <div className="mx-auto flex max-w-xl flex-col gap-2">
        {asking ? (
          <div
            role="group"
            aria-label="Confirm Done here"
            data-testid="done-confirm"
            className="flex flex-col gap-2 text-sm"
          >
            <p>
              {nothing
                ? "Move on without buying anything here?"
                : `Record ${n} ${n === 1 ? "item" : "items"} at ${stop.location.name} for ${formatMoney(total)}? Prices you edited but didn't buy are saved as shelf prices.`}
            </p>
            {failure && !(isApiError(failure) && failure.code === "stop_done") ? (
              <Alert tone="error">
                {ended ? (
                  <span className="flex flex-wrap items-center justify-between gap-2">
                    <span>
                      This trip has ended, so nothing more is recorded: {errorMessage(failure)}
                    </span>
                    <Link to={`/plan/trips/${stop.plan_id}`} className={secondaryLinkClass}>
                      Open the trip
                    </Link>
                  </span>
                ) : (
                  <>
                    Couldn&apos;t record this stop: {errorMessage(failure)} Your ticks are kept; try
                    again.
                  </>
                )}
              </Alert>
            ) : null}
            <div className="flex flex-wrap gap-2">
              <Button
                ref={notYet}
                variant="secondary"
                className="min-h-14 flex-1 lg:min-h-10"
                onClick={close}
                disabled={record.isPending}
              >
                Not yet
              </Button>
              <Button
                className="min-h-14 flex-1 lg:min-h-10"
                onClick={submit}
                disabled={record.isPending || ended}
              >
                {record.isPending ? "Recording…" : nothing ? "Move on" : "Record"}
              </Button>
            </div>
          </div>
        ) : (
          <Button
            ref={opener}
            className="min-h-14 w-full text-base lg:min-h-12"
            onClick={() => setAsking(true)}
          >
            {nothing ? "Done here · nothing bought" : "Done here · record purchase"}
          </Button>
        )}
      </div>
    </div>
  );
}
