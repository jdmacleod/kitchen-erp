import { useId, useState } from "react";
import { errorMessage } from "../../api/client";
import { STORAGE_PLACES, useLineKeeping, type LineKeepingInput, type PurchaseLine, type StoragePlace } from "../../api/purchases";
import { formatCalendarDate } from "../../lib/format";
import { Disclosure, SelectField, hintClass } from "../catalog/fields";
import { Alert, Button, Field } from "../ui";

const placePhrase = { room: "at room temperature", fridge: "in the fridge", freezer: "in the freezer" } as const;

const sourceLabel = { inferred: "inferred", printed: "printed", person: "your date" } as const;

/** "Jun 4, 2026", or for a line in the freezer "Frozen: best quality by Jun 4, 2026" (2Q). */
export function bestByText(line: PurchaseLine): string | null {
  if (!line.best_by) return null;
  const when = formatCalendarDate(line.best_by);
  return line.stored_in === "freezer" ? `Frozen: best quality by ${when}` : when;
}

type DateKind = "use_by" | "sell_by" | "set";

const dateKinds: { value: DateKind; label: string }[] = [
  { value: "use_by", label: "Use-by date on the label" },
  { value: "sell_by", label: "Sell-by date on the label" },
  { value: "set", label: "My own date" },
];

/**
 * A line's best-by date and its source, with a form to move the line to another
 * place or give the date on its label. A use-by date replaces the inferred one;
 * a sell-by date does not, following USDA FSIS.
 */
export function BestBy({ purchaseId, line, editable = true, inReview = false }: { purchaseId: string; line: PurchaseLine; editable?: boolean; inReview?: boolean }) {
  const text = bestByText(line);
  if (line.line_kind !== "item" || !line.product) return null;
  return (
    <div className="flex flex-col gap-1">
      {inReview ? <span className={hintClass}>Best by</span> : null}
      {inReview && !text && !line.best_by_source ? (
        <span className={hintClass}>Worked out from the keep time when committed</span>
      ) : text ? (
        <span className="whitespace-nowrap">
          {text}
          {line.best_by_source ? <span className={`ml-1 ${hintClass}`}>· {sourceLabel[line.best_by_source]}</span> : null}
        </span>
      ) : (
        <span className={hintClass}>{line.best_by_source === "person" ? "No date (cleared)" : line.stored_in ? `No date: no keep time ${placePhrase[line.stored_in]}` : "No date"}</span>
      )}
      {editable ? <BestByForm purchaseId={purchaseId} line={line} /> : null}
    </div>
  );
}

function BestByForm({ purchaseId, line }: { purchaseId: string; line: PurchaseLine }) {
  const id = useId();
  const keeping = useLineKeeping(purchaseId);
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState<StoragePlace>(line.stored_in ?? "fridge");
  const [kind, setKind] = useState<DateKind>("use_by");
  const [date, setDate] = useState("");
  const [note, setNote] = useState<string | null>(null);

  function send(input: LineKeepingInput, after?: string) {
    setNote(null);
    keeping.mutate(
      { lineId: line.id, ...input },
      {
        onSuccess: () => {
          setNote(after ?? null);
          if (!after) setOpen(false);
        },
      },
    );
  }

  function save() {
    const input: LineKeepingInput = {};
    if (place !== line.stored_in) input.stored_in = place;
    if (date) {
      input.date = kind;
      input.best_by = date;
    }
    if (!input.stored_in && !input.date) {
      setOpen(false);
      return;
    }
    send(input, input.date === "sell_by" ? "A sell-by date is not a best-by date, so the inferred date stands." : undefined);
  }

  return (
    <Disclosure summary="Change" open={open} onOpenChange={setOpen} className="text-sm">
      <SelectField id={`${id}-place`} label="Kept in" value={place} onChange={(e) => setPlace(e.target.value as StoragePlace)}>
        {STORAGE_PLACES.map((p) => (
          <option key={p.value} value={p.value}>
            {p.label}
          </option>
        ))}
      </SelectField>
      <SelectField id={`${id}-kind`} label="Date" value={kind} onChange={(e) => setKind(e.target.value as DateKind)}>
        {dateKinds.map((k) => (
          <option key={k.value} value={k.value}>
            {k.label}
          </option>
        ))}
      </SelectField>
      <Field id={`${id}-date`} label="Best by" type="date" value={date} onChange={(e) => setDate(e.target.value)} hint="Leave empty to keep the current date." />
      {keeping.error ? <Alert tone="error">{errorMessage(keeping.error)}</Alert> : null}
      {note ? <p className={hintClass}>{note}</p> : null}
      <div className="flex flex-wrap gap-2">
        <Button onClick={save} disabled={keeping.isPending}>
          {keeping.isPending ? "Saving…" : "Save"}
        </Button>
        {line.best_by_source && line.best_by_source !== "inferred" ? (
          <Button variant="secondary" onClick={() => send({ date: "infer" })} disabled={keeping.isPending}>
            Use the inferred date
          </Button>
        ) : null}
        {line.best_by ? (
          <Button variant="secondary" onClick={() => send({ date: "clear" })} disabled={keeping.isPending}>
            No date
          </Button>
        ) : null}
      </div>
    </Disclosure>
  );
}
