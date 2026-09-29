// Where a vendor or location field's value came from (1F), in one sentence
// pattern everywhere it is shown: the Sources list, edit-form hints and review.

import type { FieldSource, LinkedField } from "../api/geo";
import { formatDate } from "./format";

export const fieldLabel: Record<LinkedField, string> = {
  name: "Name",
  address: "Address",
  opening_hours: "Hours",
  phone: "Phone",
  website: "Website",
};

function osmRef(ref: string | null): string | null {
  const m = ref?.match(/^(node|way|relation)\/(\d+)$/);
  return m ? `${m[1]} ${m[2]}` : ref;
}

function checked(at: string | null, verb: string): string {
  return at ? `, ${verb} ${formatDate(at)}` : "";
}

/**
 * "From OpenStreetMap (node 123), checked 3 Sep 2026", and so on. A field
 * with no source, or one a person changed since its source wrote it (the
 * server then sends none), was entered by hand.
 */
export function describeSource(source: FieldSource | undefined): string {
  if (!source) return "Entered by hand";
  if (source.source === "osm") {
    const ref = osmRef(source.ref);
    return `From OpenStreetMap${ref ? ` (${ref})` : ""}${checked(source.checked_at, "checked")}`;
  }
  if (source.source === "import") {
    return `From the file ${source.ref ?? "an import"}${checked(source.checked_at, "imported")}`;
  }
  if (source.source.startsWith("enriched:")) {
    return `Suggested by ${source.source.slice("enriched:".length)}${checked(source.checked_at, "accepted")}`;
  }
  if (source.source === "observed") return `Read from a receipt${checked(source.checked_at, "on")}`;
  return `From ${source.source}${checked(source.checked_at, "checked")}`;
}

/** Why a typed phone number is not one, or null. Mirrors the server (services/phone.py). */
export function phoneError(value: string): string | null {
  const v = value.trim();
  if (!v) return null;
  if (v.length > 40 || !/^[0-9 +().-]+$/.test(v)) return "Use digits, spaces and + ( ) - . only.";
  const digits = v.replace(/\D/g, "").length;
  return digits < 7 || digits > 15 ? "Needs 7 to 15 digits." : null;
}
