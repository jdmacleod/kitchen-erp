import { Link } from "react-router";
import { formatPack, productTitle } from "../../api/catalog";
import { bridgeFixLink, normStatusText, type NeedsBridgeItem } from "../../api/pricebook";
import { Badge } from "../../components/catalog/fields";
import { MlSwitchPanel } from "../../components/pricebook/MlSwitchPanel";
import { QuickPackEditor } from "../../components/pricebook/QuickPackEditor";
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
  onMlDone: () => void;
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
  onMlDone,
}: NeedsBridgeRowProps) {
  const fix = bridgeFixLink(item.status, item.ingredient.id, item.product.id);
  const title = productTitle(item.product);
  const quickPack = item.status === "no_pack";
  const rowKey = `${item.product.id}-${item.status}`;
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
          <Badge tone="warn">
            {quickPack && counted(item.product.pack_unit)
              ? "pack has no weight"
              : normStatusText[item.status]}
          </Badge>
        </td>
        <td className="py-2 pr-3 text-right tabular-nums">{item.observation_count}</td>
        <td className="py-2 pr-3 whitespace-nowrap">{formatDate(item.latest_observed_at)}</td>
        <td className="py-2">
          {quickPack ? (
            <Button
              variant="secondary"
              className="whitespace-nowrap"
              aria-expanded={open}
              aria-controls={open ? `pack-row-${item.product.id}` : undefined}
              onClick={onTogglePack}
            >
              Set the pack
            </Button>
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
          )}
        </td>
      </tr>
      {toMl ? (
        <tr id={`ml-row-${rowKey}`}>
          <td colSpan={6} className="pb-3">
            <div className="sticky left-0 max-w-[calc(100vw-4rem)] px-0.5 lg:max-w-none">
              <MlSwitchPanel
                ingredientId={item.ingredient.id}
                name={item.ingredient.name}
                onDone={onMlDone}
                onCancel={onToggleMl}
              />
            </div>
          </td>
        </tr>
      ) : null}
      {open ? (
        <tr id={`pack-row-${item.product.id}`}>
          <td colSpan={6} className="pb-3">
            {/* Sticky so the editor stays in view when the table scrolls sideways on a phone. */}
            <div className="sticky left-0 max-w-[calc(100vw-4rem)] px-0.5 lg:max-w-none">
              <QuickPackEditor
                key={item.product.id}
                productId={item.product.id}
                title={title}
                canonicalUnit={item.ingredient.canonical_unit}
                onSaved={() => onPackSaved(title)}
                onCancel={onTogglePack}
              />
            </div>
          </td>
        </tr>
      ) : null}
    </>
  );
}
