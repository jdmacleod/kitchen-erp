import { formatPieces } from "../../api/catalog";
import type { Pack, PriceBasis } from "../../api/proposals";
import { formatMoney, stripZeros } from "../../lib/decimal";

/** What a price is for, as a person reads it: "" for 1 each, " / lb", " / 100 g". */
export function basisText(qty: string | undefined, unit: string | undefined): string {
  const q = stripZeros(qty ?? "1");
  const u = (unit ?? "each").replace("_", " ");
  if (u === "each" && q === "1") return "";
  return q === "1" ? ` / ${u}` : ` / ${q} ${u}`;
}

export function showPrice(
  price: { amount: string; qty?: string; unit?: string },
  sayEach = false,
): string {
  const basis = basisText(price.qty, price.unit);
  return `${formatMoney(price.amount)}${basis || (sayEach ? " each" : "")}`;
}

/** A price candidate side by side with others: "each" is said, not implied. */
export function comparedPrice(value: unknown): string {
  if (typeof value === "string") return `${formatMoney(value)} each`;
  return showPrice(value as PriceBasis, true);
}

/** A field's value as a person reads it. */
export function showValue(field: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (field === "pack" && typeof value === "object") {
    const pack = value as Pack;
    return `${pack.qty.replace(/\.0+$|(\.\d*?)0+$/, "$1")} ${pack.unit}`;
  }
  if (field === "pieces" && typeof value === "object") {
    const pieces = value as { count: string; name?: string };
    return formatPieces(Number(pieces.count), pieces.name);
  }
  if (field === "gtin" && typeof value === "string") return value.replace(/^0+(?=\d{12,13}$)/, "");
  if (field === "price" && typeof value === "string") return formatMoney(value);
  if (field === "price" && typeof value === "object") return showPrice(value as PriceBasis);
  if (typeof value === "boolean") return value ? "Yes" : "No";
  return String(value);
}

/** "5 links" or "5" as pieces; the name singular, as the product stores it. */
export function parsePieces(text: string): { count: string; name?: string } | null {
  const m = /^(\d{1,5})(?:\s+([A-Za-z][A-Za-z -]{0,31}))?$/.exec(text.trim());
  if (!m || Number(m[1]) <= 0) return null;
  const name = m[2]
    ?.trim()
    .toLowerCase()
    .replace(/(ies)$/, "y")
    .replace(/(ches|shes|sses|xes|zes)$/, (s) => s.slice(0, -2))
    .replace(/([^s])s$/, "$1");
  return { count: String(Number(m[1])), ...(name ? { name } : {}) };
}
