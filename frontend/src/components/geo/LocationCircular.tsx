import { useState, type SyntheticEvent } from "react";
import {
  useCheckCircular,
  useCircular,
  usePatchCircular,
  useSaveCircular,
  type Circular,
  type CircularSource,
  type FlyerRun,
  type Mechanism,
} from "../../api/circulars";
import { errorMessage } from "../../api/client";
import { formatCalendarDate, formatDate } from "../../lib/format";
import { hintClass, SelectField, TextAreaField } from "../catalog/fields";
import { Alert, Button, Field, Switch } from "../ui";

const MECHANISMS: { value: Mechanism; label: string }[] = [
  { value: "hosted_circular", label: "Hosted circular" },
  { value: "storefront", label: "Storefront" },
  { value: "itemized", label: "Itemized list" },
  { value: "image", label: "Page images" },
  { value: "region", label: "Regional edition" },
];

const WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];

const smallAction = "min-h-11 lg:min-h-8 px-2 text-xs";

function mechanismLabel(m: Mechanism): string {
  return MECHANISMS.find((x) => x.value === m)?.label ?? m;
}

function storeIds(ref: Record<string, string>): string {
  return Object.entries(ref)
    .map(([k, v]) => `${k} ${v}`)
    .join(", ");
}

/** "Week of Nov 4, 2026: 312 offers · 23 relevant", or why it wasn't read. */
function describeRun(run: FlyerRun): string {
  const when = run.ad_week_from
    ? `Week of ${formatCalendarDate(run.ad_week_from)}`
    : formatDate(run.fetched_at);
  switch (run.status) {
    case "ok":
      return `${when}: ${run.offered_count} ${run.offered_count === 1 ? "offer" : "offers"} · ${run.relevant_count} relevant`;
    case "empty":
      return `${when}: no offers this week`;
    case "not_allowed":
      return `${when}: the store's site doesn't allow reading it`;
    case "failed":
      return `${when}: couldn't be read${run.reason ? `: ${run.reason.replace(/\.$/, "")}` : ""}`;
  }
}

/**
 * A vendor location's weekly circular (18, 2U-4; UI-9.5): what the helper reads,
 * Enabled and "Ask before using", its last four reads, and "Check now".
 */
export function LocationCircular({ id, name }: { id: string; name: string }) {
  const circular = useCircular(id);
  const [editing, setEditing] = useState(false);
  const source = circular.data?.source ?? null;

  return (
    <section
      aria-label={`Circular for ${name}`}
      className="mt-3 border-t border-neutral-200 pt-3 dark:border-neutral-800"
    >
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="text-sm font-medium">Circular</h3>
        {circular.data && !editing ? (
          <Button variant="ghost" className={smallAction} onClick={() => setEditing(true)}>
            {source ? "Edit circular" : "Set up circular"}
          </Button>
        ) : null}
      </div>
      {circular.isPending ? (
        <p role="status" className={hintClass}>
          Loading…
        </p>
      ) : circular.isError ? (
        <Alert tone="error">{errorMessage(circular.error)}</Alert>
      ) : editing ? (
        <CircularForm id={id} source={source} onDone={() => setEditing(false)} />
      ) : source === null ? (
        <p className={hintClass}>No weekly circular is read for this location.</p>
      ) : (
        <CircularDetails id={id} circular={{ ...circular.data, source }} />
      )}
    </section>
  );
}

function CircularDetails({
  id,
  circular,
}: {
  id: string;
  circular: Circular & { source: CircularSource };
}) {
  const patch = usePatchCircular(id);
  const check = useCheckCircular(id);
  const { source, runs, checking } = circular;
  const error = patch.error ?? check.error;
  const ids = Object.keys(source.store_ref).length > 0 ? ` · ${storeIds(source.store_ref)}` : "";
  return (
    <div className="flex flex-col gap-2">
      <p className={hintClass}>
        {mechanismLabel(source.mechanism)} · {source.adapter}
        {ids} · new each {WEEKDAYS[source.publish_weekday - 1]}
      </p>
      {source.paused_until && new Date(source.paused_until) > new Date() ? (
        <p className="text-xs text-amber-800 dark:text-amber-300">
          Paused until {formatDate(source.paused_until)}: two weeks in a row couldn't be read.
        </p>
      ) : null}
      <div className="flex flex-wrap gap-x-4">
        <Switch
          label="Enabled"
          checked={source.enabled}
          disabled={patch.isPending}
          onChange={(enabled) => patch.mutate({ enabled })}
        />
        <Switch
          label="Ask before using"
          checked={source.require_confirmation}
          disabled={patch.isPending}
          onChange={(require_confirmation) => patch.mutate({ require_confirmation })}
        />
      </div>
      {runs.length > 0 ? (
        <ul aria-label="Last reads" className="flex flex-col gap-0.5 text-xs">
          {runs.map((run) => (
            <li key={run.id}>{describeRun(run)}</li>
          ))}
        </ul>
      ) : (
        <p className={hintClass}>Not read yet.</p>
      )}
      {error ? <Alert tone="error">{errorMessage(error)}</Alert> : null}
      <div>
        <Button
          variant="secondary"
          className={smallAction}
          disabled={!source.enabled || checking || check.isPending}
          onClick={() => check.mutate()}
        >
          {checking ? "Waiting for the helper…" : "Check now"}
        </Button>
      </div>
    </div>
  );
}

/** "merchant: larkspur" per line, to the object the helper is sent; null if a line isn't. */
function parseStoreIds(text: string): Record<string, string> | null {
  const ref: Record<string, string> = {};
  for (const line of text.split("\n")) {
    if (!line.trim()) continue;
    const at = line.indexOf(":");
    const key = line.slice(0, at).trim();
    const value = line.slice(at + 1).trim();
    if (at < 0 || !key || !value) return null;
    ref[key] = value;
  }
  return ref;
}

function CircularForm({
  id,
  source,
  onDone,
}: {
  id: string;
  source: CircularSource | null;
  onDone: () => void;
}) {
  const save = useSaveCircular(id);
  const [form, setForm] = useState({
    mechanism: source?.mechanism ?? "hosted_circular",
    adapter: source?.adapter ?? "",
    store_ref: Object.entries(source?.store_ref ?? {})
      .map(([k, v]) => `${k}: ${v}`)
      .join("\n"),
    publish_weekday: String(source?.publish_weekday ?? 3),
  });
  const [invalid, setInvalid] = useState<string | null>(null);
  const prefix = `circular-${id}`;

  const onSubmit = (event: SyntheticEvent<HTMLFormElement>) => {
    event.preventDefault();
    const storeRef = parseStoreIds(form.store_ref);
    if (!form.adapter.trim()) {
      setInvalid("An adapter is required.");
      return;
    }
    if (storeRef === null) {
      setInvalid('Write each store id as "name: value", one per line.');
      return;
    }
    setInvalid(null);
    save.mutate(
      {
        mechanism: form.mechanism,
        adapter: form.adapter.trim(),
        store_ref: storeRef,
        publish_weekday: Number(form.publish_weekday),
        // A new circular starts off; an edit keeps both switches as they were.
        enabled: source?.enabled ?? false,
        ...(source ? { require_confirmation: source.require_confirmation } : {}),
      },
      { onSuccess: onDone },
    );
  };

  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-3" aria-label="Circular" noValidate>
      {invalid ? <Alert tone="error">{invalid}</Alert> : null}
      {save.isError ? <Alert tone="error">{errorMessage(save.error)}</Alert> : null}
      <div className="grid gap-3 sm:grid-cols-2">
        <SelectField
          id={`${prefix}-mechanism`}
          label="How it's published"
          value={form.mechanism}
          onChange={(e) => setForm((f) => ({ ...f, mechanism: e.target.value as Mechanism }))}
        >
          {MECHANISMS.map((m) => (
            <option key={m.value} value={m.value}>
              {m.label}
            </option>
          ))}
        </SelectField>
        <Field
          id={`${prefix}-adapter`}
          label="Adapter"
          autoComplete="off"
          value={form.adapter}
          onChange={(e) => setForm((f) => ({ ...f, adapter: e.target.value }))}
          hint="The products helper's name for its reader."
        />
        <SelectField
          id={`${prefix}-weekday`}
          label="New each"
          value={form.publish_weekday}
          onChange={(e) => setForm((f) => ({ ...f, publish_weekday: e.target.value }))}
        >
          {WEEKDAYS.map((d, i) => (
            <option key={d} value={String(i + 1)}>
              {d}
            </option>
          ))}
        </SelectField>
        <TextAreaField
          id={`${prefix}-store-ref`}
          label="Store ids"
          value={form.store_ref}
          onChange={(e) => setForm((f) => ({ ...f, store_ref: e.target.value }))}
          hint={'One per line, as "name: value". They stay in this household\'s data.'}
        />
      </div>
      <div className="flex gap-2">
        <Button type="submit" disabled={save.isPending}>
          {source ? "Save circular" : "Set up circular"}
        </Button>
        <Button variant="secondary" onClick={onDone}>
          Cancel
        </Button>
      </div>
    </form>
  );
}
