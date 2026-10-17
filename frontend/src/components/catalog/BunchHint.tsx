import { Button } from "../ui";

/** Whether a name says the food is sold by the bunch: "Cilantro bunch". */
export function saysBunch(name: string): boolean {
  return /\bbunch(?:es)?\b/i.test(name);
}

/**
 * Offers the pack "1 bunch" for produce whose name says it is sold by the bunch and
 * whose pack is empty (issue 371). With the pack in bunches its prices compare per
 * bunch, and by weight once one bunch is weighed; the name needn't say "bunch".
 */
export function BunchHint({
  name,
  produce,
  hasPack,
  onUse,
}: {
  name: string;
  produce: boolean;
  hasPack: boolean;
  onUse: () => void;
}) {
  if (!produce || hasPack || !saysBunch(name)) return null;
  return (
    <p className="flex flex-wrap items-center gap-2 text-xs" data-testid="bunch-hint">
      <span>
        Sold by the bunch? The pack can say <span className="font-medium">1 bunch</span>.
      </span>
      <Button variant="ghost" className="min-h-11 lg:min-h-8 px-2 text-xs" onClick={onUse}>
        Use 1 bunch
      </Button>
    </p>
  );
}
