import { useEffect, useRef, useState } from "react";
import { errorMessage } from "../../api/client";
import {
  useNameProducts,
  useNamingRows,
  useSuggestNames,
  type NameProductRow,
} from "../../api/naming";
import { purchaseErrorMessage } from "../../api/purchases";
import { hintClass } from "../catalog/fields";
import { choiceInput } from "../catalog/IngredientPicker";
import { useWidePage } from "../chrome";
import { useNotice } from "../Notice";
import { Alert, Button } from "../ui";
import { NamingControls } from "./NamingControls";
import { loadDrafts, saveDrafts } from "./namingDrafts";
import { NamingRowItem } from "./NamingRowItem";
import {
  forgotten,
  problemWith,
  rowKey,
  valueOf,
  type ExistingProduct,
  type RowEdit,
} from "./namingRows";

// Rows shown at once; more on request (issue 389).
const PAGE = 20;

/**
 * Name every waiting group's product in one pass (04, 2I; #88). Each row starts
 * from the line's wording and is created only once a person has ticked it (N5).
 */
export function NameProducts() {
  // Four fields a row, the ingredient picker among them, need more than reading width.
  useWidePage();
  const naming = useNamingRows(true);
  const create = useNameProducts();
  const suggest = useSuggestNames();
  const notice = useNotice();
  // Unsent edits survive a reload (issue 389).
  const [edits, setEdits] = useState<Record<string, RowEdit>>(loadDrafts);
  const [vendor, setVendor] = useState("");
  const [shown, setShown] = useState(PAGE);
  const restored = useRef(false);
  const [problems, setProblems] = useState<Record<string, string>>({});
  // Rows refused because a product with the same name and ingredient exists.
  const [existing, setExisting] = useState<Record<string, ExistingProduct>>({});
  const rows = naming.data ?? [];

  // Keep only the drafts that still have a row; a created or removed row's go.
  useEffect(() => {
    if (!naming.data) return;
    const keys = new Set(naming.data.map(rowKey));
    saveDrafts(Object.fromEntries(Object.entries(edits).filter(([key]) => keys.has(key))));
  }, [edits, naming.data]);
  // Once the rows load, say how many drafts came back.
  useEffect(() => {
    if (restored.current || !naming.data) return;
    restored.current = true;
    const keys = new Set(naming.data.map(rowKey));
    const kept = Object.keys(edits).filter((key) => keys.has(key)).length;
    if (kept > 0)
      notice.show({
        tone: "info",
        message: `Restored ${kept} ${kept === 1 ? "row" : "rows"} you hadn't created.`,
      });
  }, [naming.data, edits, notice]);

  // Editing a row says it has been looked at, so it is ticked (N5).
  const change = (key: string, patch: RowEdit) => {
    setEdits((all) => ({ ...all, [key]: { ...all[key], ticked: true, ...patch } }));
    setProblems((all) => {
      const rest = { ...all };
      delete rest[key];
      return rest;
    });
    setExisting((all) => {
      const rest = { ...all };
      delete rest[key];
      return rest;
    });
  };

  const values = rows.map((row) => ({
    row,
    key: rowKey(row),
    v: valueOf(row, edits[rowKey(row)]),
  }));
  const ticked = values.filter((x) => x.v.ticked);
  // Most lines first, then the most recently bought (issue 389).
  const ordered = [...values].sort(
    (a, b) =>
      b.row.line_count - a.row.line_count ||
      (b.row.latest_at ?? "").localeCompare(a.row.latest_at ?? ""),
  );
  const listed = ordered.filter((x) => vendor === "" || x.row.vendor.id === vendor);
  const page = listed.slice(0, shown);
  const vendors = [...new Map(rows.map((r) => [r.vendor.id, r.vendor])).values()];
  const confident = values.filter((x) => x.row.confident && !x.v.ticked);
  const tickConfident = () =>
    setEdits((all) => ({
      ...all,
      ...Object.fromEntries(confident.map((x) => [x.key, { ...all[x.key], ticked: true }])),
    }));

  const submit = () => {
    const found: Record<string, string> = {};
    for (const { key, v } of ticked) {
      const p = problemWith(v);
      if (p) found[key] = p;
    }
    setProblems(found);
    if (Object.keys(found).length > 0) return;
    const payload: NameProductRow[] = ticked.map(({ row, v }) =>
      v.use
        ? {
            vendor_id: row.vendor.id,
            raw_text_norm: row.raw_text_norm ?? "",
            name: v.use.name,
            product_id: v.use.id,
          }
        : {
            vendor_id: row.vendor.id,
            raw_text_norm: row.raw_text_norm ?? "",
            name: v.name.trim(),
            ...(v.packQty.trim() ? { pack_qty: v.packQty.trim(), pack_unit: v.packUnit } : {}),
            ...(v.duplicate ? { allow_duplicate: true } : {}),
            ...(forgotten(row, v).length > 0 ? { forget_words: forgotten(row, v) } : {}),
            ...choiceInput(v.ingredient!),
          },
    );
    const using = new Set(ticked.filter((x) => x.v.use).map((x) => x.key));
    create.mutate(payload, {
      onSuccess: ({ results }) => {
        const failed: Record<string, string> = {};
        const offered: Record<string, ExistingProduct> = {};
        let products = 0;
        let used = 0;
        let lines = 0;
        for (const r of results) {
          const key = `${r.vendor_id}:${r.raw_text_norm}`;
          if (r.error) {
            failed[key] = r.error.message;
            if (r.error.code === "product_exists" && r.error.product)
              offered[key] = r.error.product;
          } else {
            if (using.has(key)) used += 1;
            else products += 1;
            lines += r.applied;
          }
        }
        setProblems(failed);
        setExisting(offered);
        // A created row leaves the list; a failed one keeps what was typed.
        setEdits((all) =>
          Object.fromEntries(
            Object.entries(all).filter(
              ([key]) =>
                key in failed || !results.some((r) => `${r.vendor_id}:${r.raw_text_norm}` === key),
            ),
          ),
        );
        if (products + used > 0) {
          const made = [
            products > 0 ? `Created ${products} ${products === 1 ? "product" : "products"}` : "",
            used > 0
              ? `${products > 0 ? "used" : "Used"} ${used} existing ${used === 1 ? "product" : "products"}`
              : "",
          ].filter(Boolean);
          notice.show({
            tone: "success",
            message: `${made.join(", ")} and identified ${lines} ${lines === 1 ? "line" : "lines"}.`,
          });
        }
      },
    });
  };

  if (naming.isPending) {
    return (
      <p role="status" className={hintClass}>
        Loading…
      </p>
    );
  }
  if (naming.isError) return <Alert tone="error">{errorMessage(naming.error)}</Alert>;
  if (rows.length === 0)
    return <p className={`py-4 ${hintClass}`}>Every line has a product or is ignored.</p>;

  const failedCount = Object.keys(problems).length;
  // Rows the wording couldn't name, and where the model stands on them (N2, N3).
  const unnamed = rows.filter((r) => !r.ingredient);
  const asking = rows.filter((r) => r.model?.status === "asking").length;
  const modelFailed = unnamed.filter((r) => r.model?.status === "failed").length;
  const askable = unnamed.filter((r) => !r.model || r.model.status === "failed").length;
  return (
    <div className="flex flex-col gap-4">
      <p className={hintClass}>
        Each row starts from the receipt's wording. Tick a row, or change anything in it, to include
        it; nothing is created until you press Create.
      </p>
      {askable > 0 || asking > 0 ? (
        <div className="flex flex-wrap items-center gap-3">
          {askable > 0 && asking === 0 ? (
            <Button
              variant="secondary"
              disabled={suggest.isPending}
              onClick={() => suggest.mutate()}
            >
              {modelFailed > 0 && modelFailed === askable
                ? "Ask the model again"
                : "Suggest names with the model"}
            </Button>
          ) : null}
          <span role="status" className={hintClass}>
            {asking > 0
              ? `Asking the model about ${asking} ${asking === 1 ? "line" : "lines"}… You can keep working; rows fill in as answers arrive.`
              : modelFailed > 0
                ? `The model couldn't answer for ${modelFailed} ${modelFailed === 1 ? "line" : "lines"}.`
                : `The wording didn't name an ingredient for ${askable} ${askable === 1 ? "line" : "lines"}.`}
          </span>
        </div>
      ) : null}
      {suggest.error ? <Alert tone="error">{purchaseErrorMessage(suggest.error)}</Alert> : null}
      {create.error ? <Alert tone="error">{purchaseErrorMessage(create.error)}</Alert> : null}
      {failedCount > 0 ? (
        <Alert tone="error">
          {failedCount === 1 ? "1 row wasn't created" : `${failedCount} rows weren't created`}. The
          reason is on each row.
        </Alert>
      ) : null}
      <NamingControls
        vendors={vendors}
        vendor={vendor}
        onVendor={(id) => {
          setVendor(id);
          setShown(PAGE);
        }}
        confident={confident.length}
        onTickConfident={tickConfident}
        disabled={create.isPending}
      />
      <ul
        aria-label="New products to name"
        className="flex flex-col divide-y divide-neutral-200 rounded-lg border border-neutral-200 bg-white dark:divide-neutral-800 dark:border-neutral-800 dark:bg-neutral-900"
      >
        {page.map(({ row, key, v }, i) => (
          <NamingRowItem
            key={key}
            id={`name-${i}`}
            onNext={() => document.getElementById(`name-${i + 1}-name`)?.focus()}
            row={row}
            value={v}
            problem={problems[key]}
            existing={existing[key]}
            busy={create.isPending}
            onChange={(patch) => change(key, patch)}
          />
        ))}
      </ul>
      {listed.length > page.length ? (
        <Button variant="secondary" className="self-start" onClick={() => setShown(shown + PAGE)}>
          Show {Math.min(PAGE, listed.length - page.length)} more ({listed.length - page.length}{" "}
          waiting)
        </Button>
      ) : null}
      <div className="sticky bottom-[calc(6rem+env(safe-area-inset-bottom))] z-10 flex flex-wrap items-center gap-3 rounded-lg border border-neutral-200 bg-neutral-50/95 p-3 shadow-card backdrop-blur lg:bottom-4 dark:border-neutral-800 dark:bg-neutral-950/95">
        <Button
          onClick={submit}
          disabled={create.isPending || ticked.length === 0}
          className="min-h-12 flex-1 text-base lg:min-h-10 lg:flex-none lg:text-sm"
        >
          {create.isPending
            ? "Creating…"
            : ticked.length === 0
              ? "Create products"
              : `Create ${ticked.length} ${ticked.length === 1 ? "product" : "products"}`}
        </Button>
        <span className={hintClass}>
          {ticked.length} of {rows.length} ticked
        </span>
      </div>
    </div>
  );
}
