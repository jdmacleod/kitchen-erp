import { hintClass } from "../catalog/fields";
import { Button } from "../ui";

/** The model's name for a row whose wording already gave one; using it is a choice (issue 387). */
export function ModelOffer({
  name,
  disabled,
  onUse,
}: {
  name: string | null;
  disabled: boolean;
  onUse: (name: string) => void;
}) {
  if (name === null) return null;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <span className={hintClass}>The model suggests</span>
      <Button variant="ghost" className="px-2" disabled={disabled} onClick={() => onUse(name)}>
        Use “{name}”
      </Button>
    </div>
  );
}
