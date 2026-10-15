import { useEffect, useRef, useState } from "react";
import { Link, useLocation } from "react-router";
import { useUnits } from "../../api/catalog";
import { errorMessage } from "../../api/client";
import { useNeedsBridge } from "../../api/pricebook";
import { useNotice } from "../../components/Notice";
import {
  Alert,
  Card,
  EmptyState,
  PageHeader,
  secondaryLinkClass,
  sectionHeading,
} from "../../components/ui";
import { usePageTitle } from "../../lib/usePageTitle";
import { NeedsBridgeRow } from "./NeedsBridgeRow";
import { UnconfirmedSection } from "./UnconfirmedSection";
import { UsdaReviewSection } from "./UsdaReviewSection";

/** Observations that could not be priced per canonical unit, and what each is missing. */
export function NeedsBridgePage() {
  usePageTitle("Needs a bridge");
  const list = useNeedsBridge();
  const items = list.data ?? [];
  const notice = useNotice();
  // The product whose pack is being set in place (issue 186); one at a time.
  const [editing, setEditing] = useState<string | null>(null);
  // The open "Measure in ml instead" (issue 358) or "Weigh one" (issue 357)
  // panel, as "ml:" or "weigh:" and the row's key; one at a time.
  const [panel, setPanel] = useState<string | null>(null);
  const products = new Set(items.map((i) => i.product.id)).size;
  const packIds = items.filter((i) => i.status === "no_pack").map((i) => i.product.id);
  const densities = items.filter((i) => i.status === "no_density").length;
  const units = useUnits().data ?? [];
  const counted = (unit: string | null) =>
    units.find((u) => u.code === unit)?.dimension === "count";
  const location = useLocation();
  const section = useRef<HTMLElement>(null);

  // Home's bridge row links to #products, past the USDA suggestions above.
  useEffect(() => {
    if (location.hash === "#products" && list.data) {
      section.current?.scrollIntoView({ block: "start" });
    }
  }, [location.hash, list.data]);

  // On a phone the table scrolls sideways, and "Set the pack" sits at its far
  // right; bring it back to the start so the editor and the product name show.
  useEffect(() => {
    if (!editing) return;
    const scroller = document.getElementById(`pack-row-${editing}`)?.closest(".overflow-x-auto");
    if (scroller) scroller.scrollLeft = 0;
  }, [editing]);

  // After a save, the next product waiting on a pack opens, so a batch can be
  // worked through from the keyboard without leaving the page.
  function saved(productId: string, title: string) {
    const rest = packIds.filter((id) => id !== productId);
    const next = rest[packIds.indexOf(productId)] ?? rest[0] ?? null;
    setEditing(next);
    notice.show({ tone: "success", message: `Saved the pack for ${title}.`, timeoutMs: 4000 });
  }

  return (
    <>
      <PageHeader
        title="Needs a bridge"
        description="Products whose prices can't be compared until they have a density, a measure or a pack size."
      />
      <UsdaReviewSection />
      <section
        id="products"
        ref={section}
        aria-labelledby="products-heading"
        className="scroll-mt-4"
      >
        <h2 id="products-heading" className={`${sectionHeading} mb-3`}>
          Products
        </h2>
        {list.isPending ? (
          <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
            Loading…
          </p>
        ) : list.isError ? (
          <Alert tone="error">{errorMessage(list.error)}</Alert>
        ) : items.length === 0 ? (
          <EmptyState
            title="Every price is normalized"
            action={
              <Link to="/" className={secondaryLinkClass}>
                Back to Home
              </Link>
            }
          >
            Nothing is waiting on a density, a measure, or a pack size.
          </EmptyState>
        ) : (
          <Card>
            <p role="status" className="mb-3 text-sm text-neutral-600 dark:text-neutral-400">
              {products === 1 ? "1 product" : `${products} products`}
              {packIds.length
                ? ` · ${packIds.length} ${packIds.length === 1 ? "needs" : "need"} a pack size`
                : ""}
              {densities ? ` · ${densities} ${densities === 1 ? "needs" : "need"} a density` : ""}
            </p>
            <div className="overflow-x-auto">
              <table className="w-full text-sm" aria-label="Needs a bridge">
                <thead>
                  <tr className="border-b border-neutral-200 text-left text-xs font-semibold text-neutral-600 dark:text-neutral-400 dark:border-neutral-800">
                    <th className="py-2 pr-3">Ingredient</th>
                    <th className="py-2 pr-3">Product</th>
                    <th className="py-2 pr-3">Missing</th>
                    <th className="py-2 pr-3 text-right">Prices</th>
                    <th className="py-2 pr-3">Latest</th>
                    <th className="py-2">Fix</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-neutral-200 dark:divide-neutral-800">
                  {items.map((item) => {
                    const rowKey = `${item.product.id}-${item.status}`;
                    const open = item.status === "no_pack" && editing === item.product.id;
                    const toMl = item.switch_to_ml === true && panel === `ml:${rowKey}`;
                    const weighing = panel === `weigh:${rowKey}`;
                    return (
                      <NeedsBridgeRow
                        key={rowKey}
                        item={item}
                        counted={counted}
                        open={open}
                        onTogglePack={() => setEditing(open ? null : item.product.id)}
                        onPackSaved={(title) => saved(item.product.id, title)}
                        toMl={toMl}
                        onToggleMl={() => setPanel(toMl ? null : `ml:${rowKey}`)}
                        weighing={weighing}
                        onToggleWeigh={() => setPanel(weighing ? null : `weigh:${rowKey}`)}
                        onPanelDone={() => setPanel(null)}
                      />
                    );
                  })}
                </tbody>
              </table>
            </div>
          </Card>
        )}
      </section>
      <UnconfirmedSection />
    </>
  );
}
