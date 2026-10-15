import type { Purchase, PurchaseLine } from "../api/purchases";
import { receiptPurchase, unmatchedLine } from "./purchase-fixtures";

// An invented receipt from a till that prints Price and You Pay: each item is
// read at You Pay, which already has its saving off, and the savings beneath are
// subtracted a second time. Store, codes and items are made up.

const line = (seq: number, over: Partial<PurchaseLine>): PurchaseLine => ({
  ...unmatchedLine,
  id: `0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f84${String(seq).padStart(2, "0")}`,
  seq,
  suggestions: [],
  flags: [],
  qty: "1",
  unit: "each",
  unit_price: null,
  parent_line_id: null,
  ...over,
});

export const tortillas = line(1, {
  raw_text: "MEADOW TORTILLAS",
  printed_text: "44112 MEADOW TORTILLAS 3.49 2.99 S",
  line_total: "2.9900",
});
export const tortillaSaving = line(2, {
  raw_text: "Club Savings",
  printed_text: "Club Savings -0.50",
  line_kind: "discount",
  qty: null,
  unit: null,
  line_total: "0.5000",
  parent_line_id: tortillas.id,
  flags: ["parent_inferred"],
});
export const peppers = line(3, {
  raw_text: "PEPPERS FRESNO",
  printed_text: "3781 PEPPERS FRESNO 0.63 0.63 S",
  line_total: "0.6300",
});
export const weightRow = line(4, {
  raw_text: "WT 0.07 lb @ $8.99 /lb",
  qty: "0.07",
  unit: "lb",
  unit_price: "8.9900",
  line_total: "0.0000",
  flags: ["quantity_line", "no_amount_printed"],
});
export const apples = line(5, {
  raw_text: "GALA APPLES",
  printed_text: "4041 GALA APPLES 4.10 2.95 S",
  line_total: "2.9500",
});
/** Under a weight row, so the reader lost its item. */
export const appleSaving = line(6, {
  raw_text: "Club Savings",
  printed_text: "Club Savings -1.15",
  line_kind: "discount",
  qty: null,
  unit: null,
  line_total: "1.1500",
  flags: ["parent_inferred"],
});

export const youPayPurchase: Purchase = {
  ...receiptPurchase,
  subtotal: null,
  tax: "0.00",
  total: "6.57",
  computed_total: "4.92",
  lines_total: "4.92",
  trust: "check_lines",
  gap: "1.65",
  flags: ["reconcile_mismatch"],
  printed_checks: { savings_total: "1.65", item_count: 3, payment: "6.57" },
  lines: [tortillas, tortillaSaving, peppers, weightRow, apples, appleSaving],
};
