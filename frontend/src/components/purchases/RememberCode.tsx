import { useState } from "react";
import { useParams } from "react-router";
import { productTitle } from "../../api/catalog";
import { errorMessage, isApiError } from "../../api/client";
import type { PurchaseLine } from "../../api/purchases";
import { useRememberCode, type RememberedCode } from "../../api/rememberCode";
import { Alert, Button } from "../ui";

/** Another wording that prints the same weighed-item code at this store (#448). */
export interface SharedWording {
  text: string;
  lines: number;
}

/** The other wordings, when remembering was refused because the code is shared. */
export function sharedWordings(e: unknown): SharedWording[] | null {
  if (!isApiError(e) || e.code !== "code_shared") return null;
  const found = e.details?.wordings;
  return Array.isArray(found) ? (found as SharedWording[]) : [];
}

/**
 * A weighed code other wording also prints may name the counter, not the item:
 * remembering it takes a second click, and only lines that read alike follow it.
 */
export function SharedCodeWarning({
  code,
  wordings,
  pending,
  onRemember,
  onCancel,
}: {
  code: string;
  wordings: SharedWording[];
  pending: boolean;
  onRemember: () => void;
  onCancel: () => void;
}) {
  return (
    <Alert tone="warn" className="flex w-full flex-col gap-2">
      <p>
        {code} also reads on{" "}
        {wordings.map((w, i) => (
          <span key={w.text}>
            {i > 0 ? ", " : ""}
            <span className="font-medium">{w.text}</span> ({w.lines}{" "}
            {w.lines === 1 ? "line" : "lines"})
          </span>
        ))}
        . It may name the counter, not the item. If you remember it, only lines that read like this
        one are identified by it; the others are offered it as a suggestion.
      </p>
      <span className="flex flex-wrap gap-2">
        <Button variant="secondary" disabled={pending} onClick={onRemember}>
          Remember anyway
        </Button>
        <Button variant="secondary" onClick={onCancel}>
          Don&apos;t remember
        </Button>
      </span>
    </Alert>
  );
}

/** What happened to waiting lines that print the code but read differently. */
export function heldNote(result: RememberedCode | undefined): string | null {
  const held = result?.held.length ?? 0;
  if (held === 0) return null;
  return held === 1
    ? "1 waiting line reads differently, so it stays in To identify with this as a suggestion."
    : `${held} waiting lines read differently, so they stay in To identify with this as a suggestion.`;
}

/** "Remember {code} for {product}" (04, 2K): nothing is recorded without this click. */
export function RememberCode({ line }: { line: PurchaseLine }) {
  const { id = "" } = useParams<{ id: string }>();
  const remember = useRememberCode(id);
  const [dismissed, setDismissed] = useState(false);
  // The offer goes once the code is known, but lines it left waiting are still worth a word.
  const held = heldNote(remember.data);
  if (held) return <span className="mt-1 block text-xs">{held}</span>;
  if (dismissed || !line.code_offer || !line.product) return null;
  const code = line.code_offer.value;
  const shared = sharedWordings(remember.error);
  const send = (sharedOk: boolean) => remember.mutate({ lineId: line.id, sharedOk });
  return (
    <span className="mt-1 flex flex-wrap items-center gap-2 text-xs">
      {shared ? (
        <SharedCodeWarning
          code={code}
          wordings={shared}
          pending={remember.isPending}
          onRemember={() => send(true)}
          onCancel={() => setDismissed(true)}
        />
      ) : (
        <Button
          variant="secondary"
          className="min-h-11 px-2 text-xs lg:min-h-8"
          disabled={remember.isPending}
          onClick={() => send(false)}
        >
          Remember {code} for {productTitle(line.product)}
        </Button>
      )}
      {remember.isError && !shared ? (
        <span className="text-red-800 dark:text-red-300">{errorMessage(remember.error)}</span>
      ) : null}
    </span>
  );
}
