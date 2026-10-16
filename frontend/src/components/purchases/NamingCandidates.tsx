import { formatPack, productTitle } from "../../api/catalog";
import type { NamingCandidate } from "../../api/naming";
import { hintClass } from "../catalog/fields";
import { Button } from "../ui";

/**
 * Products the row's ingredient already has, closest to its name first (issue 386).
 * Choosing one identifies the lines as that product instead of creating another.
 */
export function NamingCandidates({
  candidates,
  disabled,
  onUse,
}: {
  candidates: NamingCandidate[];
  disabled: boolean;
  onUse: (product: NamingCandidate) => void;
}) {
  if (candidates.length === 0) return null;
  return (
    <div className="mt-2 flex flex-wrap items-center gap-2 text-sm">
      <span className={hintClass}>Maybe one you have:</span>
      {candidates.map((c) => {
        const pack = formatPack(c.pack_qty, c.pack_unit);
        return (
          <Button key={c.id} variant="secondary" disabled={disabled} onClick={() => onUse(c)}>
            Use {productTitle(c)}
            {pack ? ` · ${pack}` : ""}
          </Button>
        );
      })}
    </div>
  );
}
