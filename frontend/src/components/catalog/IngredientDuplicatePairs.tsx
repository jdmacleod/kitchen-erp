import { useState } from "react";
import { Link } from "react-router";
import { errorMessage } from "../../api/client";
import {
  useIngredientDuplicates,
  useMarkIngredientsDistinct,
  type IngredientDuplicatePair,
  type IngredientDuplicateSide,
} from "../../api/ingredientDuplicates";
import { useNotice } from "../Notice";
import { Alert, Button, Card, focusRing } from "../ui";
import { Badge } from "./fields";
import { IngredientMergePanel } from "./IngredientMergePanel";

const muted = "text-neutral-600 dark:text-neutral-400";
const products = (n: number) => `${n} ${n === 1 ? "product" : "products"}`;

/**
 * Possible duplicate ingredients at the top of the link page (issue 346): each
 * pair with why it was offered, "Keep this one" on either side, which opens the
 * merge with the suggested survivor chosen, and "Not the same", which is
 * remembered. Renders nothing when there are none.
 */
export function IngredientDuplicatePairs() {
  const duplicates = useIngredientDuplicates();
  const items = duplicates.data ?? [];
  if (duplicates.isPending || (duplicates.isSuccess && items.length === 0)) return null;
  return (
    <section id="duplicates" aria-labelledby="ingredient-duplicates-heading" className="mb-6">
      <h2 id="ingredient-duplicates-heading" className="mb-1 text-lg font-medium">
        Possible duplicate ingredients
      </h2>
      {duplicates.isError ? (
        <Alert tone="error">{errorMessage(duplicates.error)}</Alert>
      ) : (
        <>
          <p className={`mb-3 text-sm ${muted}`}>
            Each pair looks like one food entered twice. Keep one and merge the other into it, or
            say they are not the same.
          </p>
          <div className="flex flex-col gap-3">
            {items.map((pair) => (
              <PairCard key={`${pair.a.id}:${pair.b.id}`} pair={pair} />
            ))}
          </div>
        </>
      )}
    </section>
  );
}

function PairCard({ pair }: { pair: IngredientDuplicatePair }) {
  const notice = useNotice();
  const distinct = useMarkIngredientsDistinct();
  const [keep, setKeep] = useState<IngredientDuplicateSide | null>(null);
  const title = `${pair.a.name} and ${pair.b.name}`;

  if (keep) {
    return (
      <IngredientMergePanel
        first={{ id: pair.a.id, name: pair.a.name, products: pair.a.products }}
        second={{ id: pair.b.id, name: pair.b.name, products: pair.b.products }}
        defaultSurvivor={keep.id}
        // The kept ingredient keeps its own name; the other becomes a spelling of it.
        targetFor={(survivor) => ({ target: { name: survivor.name }, name: survivor.name })}
        onCancel={() => setKeep(null)}
        onMerged={({ survivor, loser }) =>
          notice.show({
            tone: "success",
            message: `Merged ${loser.name} into ${survivor.name}.`,
            action: { label: "Open it", to: `/catalog/ingredients/${survivor.id}` },
          })
        }
      />
    );
  }
  return (
    <Card>
      <div role="group" aria-label={title} className="flex flex-col gap-3">
        <p className={`text-xs ${muted}`} data-testid="duplicate-reasons">
          {pair.reasons.join(" · ")}
        </p>
        <div className="grid gap-3 sm:grid-cols-2">
          {[pair.a, pair.b].map((side) => (
            <div key={side.id} className="flex min-w-0 flex-col gap-2">
              <p className="flex flex-wrap items-center gap-2">
                <Link
                  to={`/catalog/ingredients/${side.id}`}
                  className={`font-medium underline ${focusRing}`}
                >
                  {side.name}
                </Link>
                {side.standard ? <Badge>standard name</Badge> : null}
                {side.id === pair.keep_id ? <Badge>suggested</Badge> : null}
              </p>
              <p className={`text-sm ${muted}`}>{products(side.products)}</p>
              <Button
                variant={side.id === pair.keep_id ? "primary" : "secondary"}
                onClick={() => setKeep(side)}
                aria-label={`Keep ${side.name}`}
              >
                Keep this one
              </Button>
            </div>
          ))}
        </div>
        {distinct.isError ? <Alert tone="error">{errorMessage(distinct.error)}</Alert> : null}
        <div>
          <Button
            variant="ghost"
            disabled={distinct.isPending}
            onClick={() => distinct.mutate({ a: pair.a.id, b: pair.b.id })}
            aria-label={`${title} are not the same`}
          >
            Not the same
          </Button>
        </div>
      </div>
    </Card>
  );
}
