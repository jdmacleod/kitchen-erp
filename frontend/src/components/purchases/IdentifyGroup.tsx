import { useRef, useState } from "react";
import { Link } from "react-router";
import { purchaseErrorMessage, useRememberCode } from "../../api/purchases";
import { useApplyToIdentify, type ToIdentifyGroup } from "../../api/toIdentify";
import { clipKey, setClipTarget, useClipTargetKey } from "../../lib/clipFor";
import { formatMoney } from "../../lib/decimal";
import { formatDate } from "../../lib/format";
import { SelectField } from "../catalog/fields";
import { useNotice } from "../Notice";
import { Alert, Button, focusRing } from "../ui";
import { searchHref, searchWords } from "./identifyList";
import { SupplyChoices } from "./KitchenSupply";
import { NamingCandidates } from "./NamingCandidates";
import { ProductPicker } from "./ProductPicker";

/** An answered group that carried an item code, offered to be remembered (2K). */
export interface Offer {
  purchaseId: string;
  lineId: string;
  code: string;
  product: string;
}

/** "Remember {code} for {product}" after a group with an item code is answered (2K). */
export function RememberOffer({ offer, onDone }: { offer: Offer; onDone: () => void }) {
  const remember = useRememberCode(offer.purchaseId);
  return (
    <div
      role="status"
      className="mb-4 flex flex-wrap items-center gap-3 rounded-md border border-neutral-300 px-3 py-2 text-sm dark:border-neutral-700"
    >
      {remember.isSuccess ? (
        <span>
          Remembered {offer.code} for {offer.product}.
        </span>
      ) : (
        <>
          <span>This line carries the item code {offer.code}.</span>
          <Button
            variant="secondary"
            disabled={remember.isPending}
            onClick={() => remember.mutate(offer.lineId)}
          >
            Remember {offer.code} for {offer.product}
          </Button>
          {remember.isError ? (
            <span className="text-red-800 dark:text-red-300">
              {purchaseErrorMessage(remember.error)}
            </span>
          ) : null}
        </>
      )}
      <Button variant="secondary" onClick={onDone}>
        {remember.isSuccess ? "Done" : "No thanks"}
      </Button>
    </div>
  );
}

export function GroupCard({
  group,
  index,
  onAnswered,
  onWording,
}: {
  group: ToIdentifyGroup;
  index: number;
  onAnswered: (offer: Offer | null) => void;
  /** Show every store's groups with this wording. */
  onWording: (text: string) => void;
}) {
  const apply = useApplyToIdentify();
  const notice = useNotice();
  // The group leaves the list once applied, so the confirmation lives in the Notice.
  const onApplied = ({ applied }: { applied: number }, chosen: string | null) => {
    notice.show({
      tone: "success",
      message: `Applied to ${applied} ${applied === 1 ? "line" : "lines"}.`,
    });
    const first = all ? group.lines[0] : group.lines.find((l) => l.line_id === lineId);
    onAnswered(
      group.code && chosen && first
        ? {
            purchaseId: first.purchase_id,
            lineId: first.line_id,
            code: group.code.value,
            product: chosen,
          }
        : null,
    );
  };
  const [all, setAll] = useState(true);
  const [lineId, setLineId] = useState(group.lines[0]?.line_id ?? "");
  // The kitchen supply choices (spec 04, 2T), open under the group's actions.
  const [supplying, setSupplying] = useState(false);
  const supplyButton = useRef<HTMLButtonElement>(null);
  const id = `identify-${index}`;
  const target = () => ({
    vendor_id: group.vendor.id,
    raw_text_norm: group.raw_text_norm ?? "",
    ...(all ? {} : { line_ids: [lineId] }),
  });

  return (
    <div
      className="flex flex-col gap-3"
      role="group"
      aria-label={`${group.vendor.name}: ${group.raw_text_norm ?? "(no text)"}`}
    >
      <GroupHeading group={group} />
      {apply.error ? <Alert tone="error">{purchaseErrorMessage(apply.error)}</Alert> : null}
      <GroupLines group={group} />
      <GroupHints group={group} onWording={onWording} />
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]">
        <ProductPicker
          id={id}
          label="Product"
          value={null}
          lineText={group.raw_text_norm}
          onChange={(p) => {
            if (!p) return;
            apply.mutate(
              { ...target(), product_id: p.id },
              { onSuccess: (r) => onApplied(r, p.name) },
            );
          }}
          disabled={apply.isPending}
        />
        <div className="flex flex-wrap items-end gap-2">
          <Button
            variant="secondary"
            disabled={apply.isPending}
            onClick={() => {
              apply.mutate({ ...target(), ignore: true }, { onSuccess: (r) => onApplied(r, null) });
            }}
          >
            Ignore
          </Button>
          <Button
            ref={supplyButton}
            variant="secondary"
            disabled={apply.isPending}
            onClick={() => setSupplying(true)}
          >
            Kitchen supply
          </Button>
        </div>
      </div>
      <NamingCandidates
        candidates={group.candidates ?? []}
        disabled={apply.isPending}
        onUse={(c) =>
          apply.mutate(
            { ...target(), product_id: c.id },
            { onSuccess: (r) => onApplied(r, c.name) },
          )
        }
      />
      {supplying ? (
        <SupplyChoices
          id={`${id}-supply`}
          subject={group.raw_text_norm ?? "this line"}
          busy={apply.isPending}
          onChoose={(supply_category) =>
            apply.mutate({ ...target(), supply_category }, { onSuccess: (r) => onApplied(r, null) })
          }
          onCancel={() => {
            setSupplying(false);
            supplyButton.current?.focus();
          }}
        />
      ) : null}
      <GroupScope
        group={group}
        id={id}
        all={all}
        onAll={setAll}
        lineId={lineId}
        onLineId={setLineId}
      />
    </div>
  );
}

/** Each line of the group: its purchase date (a link), its printed text when it differs, its total. */
function GroupLines({ group }: { group: ToIdentifyGroup }) {
  return (
    <ul className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-neutral-700 dark:text-neutral-300">
      {group.lines.map((l) => (
        <li key={l.line_id}>
          <Link
            to={`/shop/purchases/${l.purchase_id}`}
            className={`rounded-sm underline ${focusRing}`}
          >
            {formatDate(l.purchased_at)}
          </Link>
          {l.raw_text && l.raw_text !== group.raw_text_norm ? (
            <span className="font-mono"> {l.raw_text}</span>
          ) : null}
          {l.line_total !== null ? <span> · {formatMoney(l.line_total)}</span> : null}
        </li>
      ))}
    </ul>
  );
}

/** "Apply to all n", or the one line an answer is narrowed to. */
function GroupScope({
  group,
  id,
  all,
  onAll,
  lineId,
  onLineId,
}: {
  group: ToIdentifyGroup;
  id: string;
  all: boolean;
  onAll: (all: boolean) => void;
  lineId: string;
  onLineId: (id: string) => void;
}) {
  return (
    <div className="flex flex-wrap items-end gap-3">
      {/* A group of one line has nothing else to apply to: "Apply to all 1"
          offered a choice that changed nothing (QA 2026-10-13). */}
      {group.line_count > 1 ? (
        <label className="inline-flex min-h-11 lg:min-h-10 items-center gap-2 text-sm">
          <input
            type="checkbox"
            checked={all}
            onChange={(e) => onAll(e.target.checked)}
            className={`size-4 ${focusRing}`}
          />
          Apply to all {group.line_count}
        </label>
      ) : null}
      {!all && group.lines.length > 1 ? (
        <SelectField
          id={`${id}-line`}
          label="Only this line"
          value={lineId}
          onChange={(e) => onLineId(e.target.value)}
          className="w-56"
        >
          {group.lines.map((l) => (
            <option key={l.line_id} value={l.line_id}>
              {formatDate(l.purchased_at)} {l.line_total !== null ? formatMoney(l.line_total) : ""}
            </option>
          ))}
        </SelectField>
      ) : null}
    </div>
  );
}

/** The group's wording, its store and how many lines wait. */
function GroupHeading({ group }: { group: ToIdentifyGroup }) {
  return (
    <div className="flex flex-wrap items-baseline justify-between gap-2">
      <h2 className="text-base font-medium">
        <span className="font-mono">{group.raw_text_norm ?? "(no text)"}</span>
        <span className="text-neutral-600 dark:text-neutral-400"> at {group.vendor.name}</span>
      </h2>
      <span className="text-xs text-neutral-600 dark:text-neutral-400">
        {group.line_count} {group.line_count === 1 ? "line" : "lines"}
      </span>
    </div>
  );
}

/** "Also waiting at": the same wording at other stores, one click to list them together. */
function Elsewhere({
  group,
  onWording,
}: {
  group: ToIdentifyGroup;
  onWording: (text: string) => void;
}) {
  const others = group.elsewhere ?? [];
  if (others.length === 0 || !group.raw_text_norm) return null;
  const wording = group.raw_text_norm;
  return (
    <p className="text-sm text-neutral-700 dark:text-neutral-300">
      Also waiting at{" "}
      {others.map((o, i) => (
        <span key={o.vendor_id}>
          {i > 0 ? ", " : ""}
          {o.vendor_name} ({o.line_count})
        </span>
      ))}
      .{" "}
      <button
        type="button"
        className={`rounded-sm text-blue-700 underline dark:text-blue-300 ${focusRing}`}
        onClick={() => onWording(wording)}
      >
        Show them together
      </button>
    </p>
  );
}

/** "Search <store> for …": the store's own search page, in a new tab, to find a new product. */
function SearchStore({ group }: { group: ToIdentifyGroup }) {
  const words = searchWords(group);
  const href = searchHref(group.vendor.search_url, words);
  if (!href) return null;
  return (
    <p className="text-sm">
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        onClick={() => markClip(group)}
        className={`rounded-sm text-blue-700 underline dark:text-blue-300 ${focusRing}`}
      >
        Search {group.vendor.name} for “{words}”
      </a>
    </p>
  );
}

/** Where else to look: the store's search page, a page clipped for these lines, other stores. */
function GroupHints({
  group,
  onWording,
}: {
  group: ToIdentifyGroup;
  onWording: (text: string) => void;
}) {
  return (
    <>
      <SearchStore group={group} />
      <ClipFor group={group} />
      <Elsewhere group={group} onWording={onWording} />
    </>
  );
}

/** Mark the group the next clipped page is for, so accepting that page identifies it. */
function markClip(group: ToIdentifyGroup) {
  if (!group.raw_text_norm) return;
  setClipTarget({
    vendor_id: group.vendor.id,
    vendor_name: group.vendor.name,
    raw_text_norm: group.raw_text_norm,
    line_count: group.line_count,
  });
}

/**
 * A page clipped for these lines waits for review; or the next page clipped is for
 * them; or a button that makes it so.
 */
function ClipFor({ group }: { group: ToIdentifyGroup }) {
  const target = useClipTargetKey();
  const waiting = group.waiting_proposal;
  if (waiting)
    return (
      <p className="text-sm">
        Clipped: {waiting.title ?? "a product page"}, waiting for review ·{" "}
        <Link
          to={`/catalog/products/review/${waiting.id}`}
          className={`rounded-sm text-blue-700 underline dark:text-blue-300 ${focusRing}`}
        >
          Review it
        </Link>
      </p>
    );
  if (!group.raw_text_norm) return null;
  if (target === clipKey(group.vendor.id, group.raw_text_norm))
    return (
      <p role="status" className="text-sm text-neutral-700 dark:text-neutral-300">
        The next product page you clip is for these lines.
      </p>
    );
  return (
    <p className="text-sm">
      <button
        type="button"
        className={`rounded-sm text-blue-700 underline dark:text-blue-300 ${focusRing}`}
        onClick={() => markClip(group)}
      >
        Clip a product page for these lines
      </button>
    </p>
  );
}
