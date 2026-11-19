// Switching an ingredient to its standard entry's unit (issue 527), offered on the
// link page and on Needs a bridge; Tidy the catalog uses the same confirm panel.

import { useEffect, useRef, useState } from "react";
import { useSwitchUnit, useUnitSwitch, type CleanupSuggestion } from "../../api/catalogCleanup";
import { errorMessage, isApiError } from "../../api/client";
import { useNotice } from "../Notice";
import { Button } from "../ui";
import { Consequences, resultSentence } from "./CleanupConsequences";

/**
 * The confirm under a guessed switch: what it changes, then a red "Switch and drop N
 * measures" (or a blue "Switch to ml" when nothing drops) and Cancel. Focus moves to
 * its first button when it opens; the caller returns focus to its trigger on Cancel.
 */
export function UnitSwitchConfirm({
  suggestion: s,
  pending,
  error,
  onConfirm,
  onCancel,
}: {
  suggestion: CleanupSuggestion;
  pending: boolean;
  error: string | null;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  const first = useRef<HTMLButtonElement>(null);
  useEffect(() => first.current?.focus(), []);
  const drops = s.measures_dropped ?? 0;
  return (
    <div
      role="group"
      aria-label={`Switch ${s.subject} to ${s.after}`}
      className="flex flex-col gap-2 rounded-md border border-neutral-300 p-3 text-sm dark:border-neutral-700"
      data-testid="unit-switch-confirm"
    >
      <p>
        Switching {s.subject} to {s.after} changes these:
      </p>
      <Consequences items={s.consequences ?? []} />
      {error ? (
        <p role="alert" className="text-red-800 dark:text-red-300">
          {error}
        </p>
      ) : null}
      <div className="grid grid-cols-2 gap-2 lg:flex">
        <Button
          ref={first}
          variant={drops ? "dangerFill" : "primary"}
          disabled={pending}
          onClick={onConfirm}
        >
          {pending
            ? "Switching…"
            : drops
              ? `Switch and drop ${drops} ${drops === 1 ? "measure" : "measures"}`
              : `Switch to ${s.after}`}
        </Button>
        <Button variant="ghost" onClick={onCancel}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/** The server's words for a failed switch, with its code. */
export function switchError(e: unknown): string {
  return isApiError(e) ? `${e.message} (${e.code})` : errorMessage(e);
}

/**
 * "The standard list measures this in ml; this ingredient is in g." with the switch:
 * one click when it is sure, the confirm panel first when it is a guess. Renders
 * nothing when the ingredient's unit is its entry's.
 */
export function UnitSwitchOffer({
  ingredientId,
  label,
}: {
  ingredientId: string;
  /** The trigger's words; "Switch to ml" by default. */
  label?: string;
}) {
  const found = useUnitSwitch(ingredientId);
  const switchUnit = useSwitchUnit(ingredientId);
  const notice = useNotice();
  const [open, setOpen] = useState(false);
  const trigger = useRef<HTMLButtonElement>(null);
  const s = found.data;
  if (!s) return null;
  const apply = () =>
    switchUnit.mutate(s.fingerprint, {
      onSuccess: (result) => {
        setOpen(false);
        notice.show({ tone: "success", message: `${resultSentence(s, result)}.` });
      },
      onError: () => setOpen(true),
    });
  const error = switchUnit.isError ? switchError(switchUnit.error) : null;
  return (
    <div className="flex flex-col gap-2 text-sm" data-testid="unit-switch-offer">
      <p>
        The standard list measures this in {s.after}; this ingredient is in {s.before}.
      </p>
      <Button
        ref={trigger}
        variant="secondary"
        className="self-start"
        aria-expanded={s.confidence === "guess" ? open : undefined}
        disabled={switchUnit.isPending}
        onClick={() => (s.confidence === "guess" ? setOpen(!open) : apply())}
      >
        {switchUnit.isPending && !open ? "Switching…" : (label ?? `Switch to ${s.after}`)}
      </Button>
      {!open && error ? (
        <p role="alert" className="text-red-800 dark:text-red-300">
          {error}
        </p>
      ) : null}
      {open ? (
        <UnitSwitchConfirm
          suggestion={s}
          pending={switchUnit.isPending}
          error={error}
          onConfirm={apply}
          onCancel={() => {
            setOpen(false);
            trigger.current?.focus();
          }}
        />
      ) : null}
    </div>
  );
}
