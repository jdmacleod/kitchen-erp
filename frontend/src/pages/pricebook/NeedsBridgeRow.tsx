import type { ReactNode } from "react";
import { Link } from "react-router";
import { formatPack, productTitle } from "../../api/catalog";
import { bridgeFixLink, normStatusText, type NeedsBridgeItem } from "../../api/pricebook";
import { Badge } from "../../components/catalog/fields";
import { useNotice } from "../../components/Notice";
import { MlSwitchPanel } from "../../components/pricebook/MlSwitchPanel";
import { QuickPackEditor } from "../../components/pricebook/QuickPackEditor";
import { WeighOnePanel } from "../../components/pricebook/WeighOnePanel";
import { Button, focusRing } from "../../components/ui";
import { formatDate } from "../../lib/format";

export interface NeedsBridgeRowProps {
  item: NeedsBridgeItem;
  /** Whether a unit counts pieces, so a pack in it has no weight. */
  counted: (unit: string | null) => boolean;
  /** The in-place pack editor is open under this row (issue 186). */
  open: boolean;
  onTogglePack: () => void;
  onPackSaved: (title: string) => void;
  /** The "Measure in ml instead" panel is open under this row (issue 358). */
  toMl: boolean;
  onToggleMl: () => void;
  /** The "Weigh one" panel is open under this row (issue 357). */
  weighing: boolean;
  onToggleWeigh: () => void;
  /** Close whichever panel is open, after it saved. */
  onPanelDone: () => void;
}

/** One product on Needs a bridge, with what it is missing and how to fix it. */
export function NeedsBridgeRow({
  item,
  counted,
  open,
  onTogglePack,
  onPackSaved,
  toMl,
  onToggleMl,
  weighing,
  onToggleWeigh,
  onPanelDone,
}: NeedsBridgeRowProps) {
  const notice = useNotice();
  const done = (message: string) => {
    onPanelDone();
    notice.show({ tone: "success", message });
  };
  const name = item.ingredient.name;
  const title = productTitle(item.product);
  const quickPack = item.status === "no_pack";
  const rowKey = `${item.product.id}-${item.status}`;
  // A pack counted in pieces is priced through what one piece weighs (issue 357).
  const inPieces =
    quickPack && counted(item.product.pack_unit) && item.ingredient.canonical_unit !== "each";
  // A price per bunch is weighed through the ingredient's "bunch" measure (issue 371).
  const bunch = item.status === "no_bunch_weight";
  const weighed = bunch
    ? `Saved what a bunch of ${name} weighs. Its bunches are priced through it.`
    : `Saved what one ${name} weighs. Its packs in pieces are priced through it.`;
  return (
    <>
      <tr data-testid="needs-bridge">
        <td className="py-2 pr-3">
          <Link
            to={`/catalog/ingredients/${item.ingredient.id}`}
            className={`rounded-sm underline-offset-2 hover:underline ${focusRing}`}
          >
            {item.ingredient.name}
          </Link>
          <span className="text-xs text-neutral-600 dark:text-neutral-400">
            {" "}
            · {item.ingredient.canonical_unit}
          </span>
        </td>
        <td className="py-2 pr-3">
          <Link
            to={`/catalog/products/${item.product.id}`}
            className={`rounded-sm underline-offset-2 hover:underline ${focusRing}`}
          >
            {title}
          </Link>
          {formatPack(item.product.pack_qty, item.product.pack_unit) ? (
            <span className="text-xs text-neutral-600 dark:text-neutral-400">
              {" "}
              · {formatPack(item.product.pack_qty, item.product.pack_unit)}
            </span>
          ) : null}
        </td>
        <td className="py-2 pr-3">
          <Badge tone="warn">{inPieces ? "pack has no weight" : normStatusText[item.status]}</Badge>
        </td>
        <td className="py-2 pr-3 text-right tabular-nums">{item.observation_count}</td>
        <td className="py-2 pr-3 whitespace-nowrap">{formatDate(item.latest_observed_at)}</td>
        <td className="py-2">
          <FixCell
            item={item}
            rowKey={rowKey}
            inPieces={inPieces}
            open={open}
            weighing={weighing}
            toMl={toMl}
            onTogglePack={onTogglePack}
            onToggleWeigh={onToggleWeigh}
            onToggleMl={onToggleMl}
          />
        </td>
      </tr>
      {toMl ? (
        <PanelRow id={`ml-row-${rowKey}`}>
          <MlSwitchPanel
            ingredientId={item.ingredient.id}
            name={item.ingredient.name}
            onDone={() => done(`${name} is measured in ml now.`)}
            onCancel={onToggleMl}
          />
        </PanelRow>
      ) : null}
      {weighing ? (
        <PanelRow id={`weigh-row-${rowKey}`}>
          <WeighOnePanel
            ingredientId={item.ingredient.id}
            name={item.ingredient.name}
            canonicalUnit={item.ingredient.canonical_unit}
            measure={bunch ? "bunch" : "each"}
            onDone={() => done(weighed)}
            onCancel={onToggleWeigh}
          />
        </PanelRow>
      ) : null}
      {open ? (
        <PanelRow id={`pack-row-${item.product.id}`}>
          <QuickPackEditor
            key={item.product.id}
            productId={item.product.id}
            title={title}
            canonicalUnit={item.ingredient.canonical_unit}
            onSaved={() => onPackSaved(title)}
            onCancel={onTogglePack}
          />
        </PanelRow>
      ) : null}
    </>
  );
}

/** A full-width row under a product for its open panel. Sticky, so the panel
 * stays in view when the table scrolls sideways on a phone. */
function PanelRow({ id, children }: { id: string; children: ReactNode }) {
  return (
    <tr id={id}>
      <td colSpan={6} className="pb-3">
        <div className="sticky left-0 max-w-[calc(100vw-4rem)] px-0.5 lg:max-w-none">
          {children}
        </div>
      </td>
    </tr>
  );
}

/** What fixes the row: the pack editor, "Weigh one" or "Weigh one bunch", or the link to
 * the ingredient. */
function FixCell({
  item,
  rowKey,
  inPieces,
  open,
  weighing,
  toMl,
  onTogglePack,
  onToggleWeigh,
  onToggleMl,
}: {
  item: NeedsBridgeItem;
  rowKey: string;
  inPieces: boolean;
  open: boolean;
  weighing: boolean;
  toMl: boolean;
  onTogglePack: () => void;
  onToggleWeigh: () => void;
  onToggleMl: () => void;
}) {
  const quickPack = item.status === "no_pack";
  const fix = bridgeFixLink(item.status, item.ingredient.id, item.product.id);
  if (item.status === "no_bunch_weight") {
    return (
      <Button
        variant="secondary"
        className="whitespace-nowrap"
        aria-expanded={weighing}
        aria-controls={weighing ? `weigh-row-${rowKey}` : undefined}
        onClick={onToggleWeigh}
      >
        Weigh one bunch
      </Button>
    );
  }
  return quickPack ? (
    <div className="flex flex-col items-start gap-1">
      {inPieces ? (
        <Button
          variant="secondary"
          className="whitespace-nowrap"
          aria-expanded={weighing}
          aria-controls={weighing ? `weigh-row-${rowKey}` : undefined}
          onClick={onToggleWeigh}
        >
          Weigh one
        </Button>
      ) : null}
      <Button
        variant={inPieces ? "ghost" : "secondary"}
        className="whitespace-nowrap"
        aria-expanded={open}
        aria-controls={open ? `pack-row-${item.product.id}` : undefined}
        onClick={onTogglePack}
      >
        Set the pack
      </Button>
    </div>
  ) : (
    <div className="flex flex-col items-start gap-1">
      <Link to={fix.to} className={`rounded-sm font-medium underline ${focusRing}`}>
        {fix.text}
      </Link>
      {item.switch_to_ml ? (
        <Button
          variant="secondary"
          className="whitespace-nowrap"
          aria-expanded={toMl}
          aria-controls={toMl ? `ml-row-${rowKey}` : undefined}
          onClick={onToggleMl}
        >
          Measure in ml instead
        </Button>
      ) : null}
    </div>
  );
}
