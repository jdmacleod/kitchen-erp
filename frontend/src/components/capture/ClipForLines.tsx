import { purchaseErrorMessage } from "../../api/purchases";
import { useApplyToIdentify } from "../../api/toIdentify";
import { announceClip, clearClipTarget, type ClipTarget } from "../../lib/clipFor";
import { Alert, Button, focusRing } from "../ui";

/** Whether the clip is for the marked lines: as chosen, else when it is their store's page. */
export function usesTarget(
  target: ClipTarget | null,
  chosen: boolean | null,
  storeId: string | null,
): boolean {
  if (target === null) return false;
  return chosen ?? storeId === target.vendor_id;
}

/** The capture's `for_lines`, when the clip is for the marked lines. */
export function linesFor(
  target: ClipTarget | null,
  use: boolean,
): { vendor_id: string; raw_text_norm: string } | undefined {
  return use && target
    ? { vendor_id: target.vendor_id, raw_text_norm: target.raw_text_norm }
    : undefined;
}

/** After a save: the marked lines are spoken for, and other tabs hear of the clip. */
export function clipSaved(usedTarget: boolean): void {
  if (usedTarget) clearClipTarget();
  announceClip();
}

function lines(n: number): string {
  return `${n} ${n === 1 ? "line" : "lines"}`;
}

/**
 * "Use for <wording> at <store>": the To identify group a card marked before this
 * page was clipped (04, 2D). Accepting the saved page identifies those lines.
 */
export function ClipForLinesChoice({
  target,
  checked,
  onChange,
}: {
  target: ClipTarget;
  checked: boolean;
  onChange: (checked: boolean) => void;
}) {
  return (
    <label className="inline-flex min-h-11 items-center gap-2 text-sm">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => onChange(e.target.checked)}
        className={`size-4 ${focusRing}`}
      />
      <span>
        Use for <span className="font-mono">{target.raw_text_norm}</span> at {target.vendor_name} (
        {lines(target.line_count)})
      </span>
    </label>
  );
}

/** The page is a product already: identify the marked lines as it, without saving. */
export function UseKnownForLines({
  target,
  product,
}: {
  target: ClipTarget;
  product: { product_id: string; name: string };
}) {
  const apply = useApplyToIdentify();
  if (apply.isSuccess)
    return (
      <Alert tone="success">
        Identified {lines(apply.data.applied)} as {product.name}.
      </Alert>
    );
  return (
    <div className="flex flex-col gap-2">
      <Button
        variant="secondary"
        disabled={apply.isPending}
        onClick={() =>
          apply.mutate(
            {
              vendor_id: target.vendor_id,
              raw_text_norm: target.raw_text_norm,
              product_id: product.product_id,
            },
            {
              onSuccess: () => {
                clearClipTarget();
                announceClip();
              },
            },
          )
        }
      >
        Use {product.name} for {target.raw_text_norm}
      </Button>
      {apply.error ? <Alert tone="error">{purchaseErrorMessage(apply.error)}</Alert> : null}
    </div>
  );
}
