import { useId, useState, type FormEvent } from "react";
import { geoErrorMessage, IMPORT_MAX_BYTES, useImportVendors, type ImportCounts, type ImportItem, type ImportReport } from "../../api/geo";
import { Disclosure } from "../catalog/fields";
import { Drawer } from "../Drawer";
import { useNotice } from "../Notice";
import { Alert } from "../ui";

const muted = "text-neutral-600 dark:text-neutral-400";

const FIELD: Record<string, string> = {
  name: "name",
  kind: "kind",
  price_scope: "price scope",
  website: "website",
  brand: "brand",
  wikidata: "Wikidata",
  address: "address",
  phone: "phone",
  opening_hours: "hours",
  osm: "OpenStreetMap link",
  notes: "notes",
  active: "active",
  publishable: "shared",
  stop_overhead_min: "stop overhead",
  receipt_identifiers: "store codes",
  home_base: "kitchen",
};
const label = (field: string) => FIELD[field] ?? field.replace(/_/g, " ");
const show = (value: string | null) => (value === null || value === "" ? "empty" : value);

/**
 * "{c} to create · {u} to update · {n} need you · {k} unchanged" (design D4).
 * An unknown kitchen is a row under Needs you, so it counts there too.
 */
export function countStrip(r: Pick<ImportReport, "counts" | "unresolved_home_bases">): string {
  const c = r.counts;
  const needs = c.conflicts + c.unmatched + r.unresolved_home_bases.length;
  return `${c.created} to create · ${c.updated} to update · ${needs} need you · ${c.unchanged} unchanged`;
}

/** The Notice after an import, saying so when the run differed from its preview (D6). */
export function importNotice(done: ImportCounts, preview: ImportCounts | null): string {
  const parts = [`Imported: ${done.created} created, ${done.updated} updated`];
  if (done.conflicts + done.unmatched > 0) parts.push(`${done.conflicts + done.unmatched} left for you`);
  let message = `${parts.join(", ")}.`;
  if (preview) {
    const moved = Math.abs(done.created - preview.created) + Math.abs(done.updated - preview.updated);
    if (moved > 0) message += ` ${moved} changed since the preview.`;
  }
  return message;
}

function itemTitle(item: ImportItem, vendorNames: Map<string, string>): string {
  if (item.target === "vendor") return item.name;
  const vendor = item.vendor_key ? vendorNames.get(item.vendor_key) : undefined;
  return vendor && vendor !== item.name ? `${vendor} · ${item.name}` : item.name;
}

function Report({ report, fileName }: { report: ImportReport; fileName: string }) {
  const vendorNames = new Map(report.items.filter((i) => i.target === "vendor").map((i) => [i.key, i.name]));
  const needs = report.items.filter((i) => i.outcome === "conflict" || i.outcome === "unmatched");
  const changing = report.items.filter((i) => i.outcome === "created" || i.outcome === "updated");
  const unchanged = report.items.filter((i) => i.outcome === "unchanged");
  const nothing = changing.length === 0;
  return (
    <div className="flex flex-col gap-3">
      <p className={`text-sm ${muted}`}>
        {fileName} · {report.mode === "household" ? "Household file" : "Public file"}
      </p>
      <p role="status" className="rounded-md bg-neutral-100 px-3 py-2 text-sm dark:bg-neutral-800">
        {countStrip(report)}
      </p>
      {nothing ? <p className="text-sm font-medium">This file matches what you have. Nothing to import.</p> : null}
      {needs.length > 0 || report.unresolved_home_bases.length > 0 ? (
        <section aria-labelledby="import-needs-you">
          <h3 id="import-needs-you" className="text-sm font-semibold">
            Needs you
          </h3>
          <p className={`text-xs ${muted}`}>These are left as they are; nothing is guessed.</p>
          <ul className="divide-y divide-neutral-200 dark:divide-neutral-800">
            {needs.map((item) => (
              <li key={`${item.target}-${item.key}`} className="py-2 text-sm">
                <p className="font-medium">{itemTitle(item, vendorNames)}</p>
                {item.reason ? <p className={`text-xs ${muted}`}>{item.reason} Not imported.</p> : null}
                {item.conflicts.map((c) => (
                  <p key={c.field} className={`text-xs ${muted}`}>
                    Your edit is kept: {label(c.field)} ({show(c.current)}). The file says {show(c.file)}.
                  </p>
                ))}
              </li>
            ))}
            {report.unresolved_home_bases.map((name) => (
              <li key={`home-${name}`} className="py-2 text-sm">
                <p className="font-medium">No kitchen called {name}</p>
                <p className={`text-xs ${muted}`}>None is created; those locations use the nearest kitchen.</p>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {changing.length > 0 ? (
        <section aria-labelledby="import-will-change">
          <h3 id="import-will-change" className="text-sm font-semibold">
            Will change
          </h3>
          <ul className="divide-y divide-neutral-200 dark:divide-neutral-800">
            {changing.map((item) => (
              <li key={`${item.target}-${item.key}`} className="py-2 text-sm">
                <p className="font-medium">
                  {itemTitle(item, vendorNames)} {item.outcome === "created" ? <span className={`text-xs font-normal ${muted}`}>new</span> : null}
                </p>
                <p className={`text-xs ${muted}`}>
                  {item.outcome === "created"
                    ? item.changes.map((c) => label(c.field)).join(", ")
                    : item.changes.map((c) => `${label(c.field)}: ${show(c.old)} → ${show(c.new)}`).join(" · ")}
                </p>
              </li>
            ))}
          </ul>
        </section>
      ) : null}
      {unchanged.length > 0 ? (
        <Disclosure summary={`${unchanged.length} unchanged`}>
          <ul className={`text-xs ${muted}`}>
            {unchanged.map((item) => (
              <li key={`${item.target}-${item.key}`}>{itemTitle(item, vendorNames)}</li>
            ))}
          </ul>
        </Disclosure>
      ) : null}
    </div>
  );
}

/**
 * Import a kitchen-erp-vendors/1 file (spec 03 §1F, design D4 and D6). Choosing
 * a file runs a dry run and shows what would change, what needs you first;
 * Import applies it. Cancelling writes nothing (UI-3.14).
 */
export function VendorImportDrawer({ onClose }: { onClose: () => void }) {
  const formId = useId();
  const inputId = useId();
  const notice = useNotice();
  const preview = useImportVendors();
  const apply = useImportVendors();
  const [file, setFile] = useState<{ name: string; text: string } | null>(null);
  const [tooBig, setTooBig] = useState<string | null>(null);

  const choose = async (picked: File | undefined) => {
    preview.reset();
    apply.reset();
    setTooBig(null);
    if (!picked) return;
    if (picked.size > IMPORT_MAX_BYTES) {
      setFile({ name: picked.name, text: "" });
      setTooBig(`${picked.name} is over the 5 MB limit.`);
      return;
    }
    const text = await picked.text();
    setFile({ name: picked.name, text });
    preview.mutate({ text, fileName: picked.name, dryRun: true });
  };

  const report = preview.data ?? null;
  const canImport = report !== null && report.counts.created + report.counts.updated > 0;

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!file || !canImport) return;
    apply.mutate(
      { text: file.text, fileName: file.name, dryRun: false },
      {
        onSuccess: (done) => {
          notice.show({ tone: "success", message: importNotice(done.counts, report?.counts ?? null) });
          onClose();
        },
      },
    );
  };

  return (
    <Drawer title="Import vendors" thing="import" dirty={file !== null} onClose={onClose} formId={formId} primaryLabel="Import" busy={apply.isPending} busyLabel="Importing…" primaryDisabled={!canImport}>
      <form id={formId} onSubmit={onSubmit} className="flex flex-col gap-4" noValidate>
        <p className={`text-sm ${muted}`}>
          A kitchen-erp-vendors/1 file, YAML or JSON. You see what it would change before anything is written. Nothing you edited is overwritten, and nothing is deleted.
        </p>
        <div className="flex flex-col gap-1">
          <label htmlFor={inputId} className="text-sm font-medium">
            {file ? "Choose another file" : "Vendor file"}
          </label>
          <input
            id={inputId}
            type="file"
            accept=".yaml,.yml,.json,application/yaml,application/json"
            className="min-h-11 text-sm"
            onChange={(e) => void choose(e.target.files?.[0])}
            disabled={apply.isPending}
          />
        </div>
        {tooBig ? <Alert tone="error">{tooBig}</Alert> : null}
        {preview.isPending && file ? (
          <p role="status" className={`text-sm ${muted}`}>
            Checking {file.name}…
          </p>
        ) : null}
        {preview.isError ? <Alert tone="error">{geoErrorMessage(preview.error)}</Alert> : null}
        {apply.isError ? <Alert tone="error">{geoErrorMessage(apply.error)}</Alert> : null}
        {report && file ? <Report report={report} fileName={file.name} /> : null}
      </form>
    </Drawer>
  );
}
