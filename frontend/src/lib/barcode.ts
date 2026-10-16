// Which eight-digit barcodes need the person to name their symbology (spec 03, 1H).
// Mirrors backend/app/catalog/identifiers.py: an eight-digit code valid both as
// EAN-8 and as UPC-E, whose two readings name different products, is never read
// silently, so the form asks "Is this EAN-8 or UPC-E?" and sends the answer.

export type BarcodeSymbology = "ean8" | "upce";

/** The barcode fields a product create or update sends. */
export interface BarcodeInput {
  barcode?: string;
  /** Sent only for an eight-digit code that reads both ways. */
  barcode_symbology?: BarcodeSymbology;
}

/** The GS1 check digit for `body` (every digit but the last). */
export function checkDigit(body: string): number {
  let total = 0;
  for (let i = 0; i < body.length; i++) {
    // Weights run 3, 1, 3, ... from the rightmost digit of the body.
    total += Number(body[body.length - 1 - i]) * (i % 2 === 0 ? 3 : 1);
  }
  return (10 - (total % 10)) % 10;
}

function gs1Ok(digits: string): boolean {
  return /^\d{2,}$/.test(digits) && checkDigit(digits.slice(0, -1)) === Number(digits.slice(-1));
}

/** Expand an eight-digit UPC-E (number system 0 or 1) to UPC-A, or null. */
export function upceToUpca(code: string): string | null {
  if (!/^[01]\d{7}$/.test(code)) return null;
  const ns = code[0];
  const [d0, d1, d2, d3, d4, last] = code.slice(1, 7);
  const check = code[7];
  let body: string;
  if ("012".includes(last)) body = `${ns}${d0}${d1}${last}0000${d2}${d3}${d4}`;
  else if (last === "3") body = `${ns}${d0}${d1}${d2}00000${d3}${d4}`;
  else if (last === "4") body = `${ns}${d0}${d1}${d2}${d3}00000${d4}`;
  else body = `${ns}${d0}${d1}${d2}${d3}${d4}0000${last}`;
  return body + check;
}

/**
 * True when the typed code is eight digits valid as EAN-8 and as UPC-E, and the
 * two readings are different products: the server refuses it without a symbology.
 */
export function needsSymbology(raw: string): boolean {
  const code = raw.trim();
  if (!/^\d{8}$/.test(code) || !gs1Ok(code)) return false;
  const upca = upceToUpca(code);
  if (upca === null || !gs1Ok(upca)) return false;
  return code.padStart(14, "0") !== upca.padStart(14, "0");
}

/** Whether to ask: a typed code that needs a symbology, unless it is the one already saved. */
export function asksSymbology(raw: string, saved?: string | null): boolean {
  return needsSymbology(raw) && raw.trim() !== (saved ?? "");
}
