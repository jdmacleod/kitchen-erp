import { describe, expect, it } from "vitest";
import { breakdown, checks, hints, oneDigitOff, totalIsPaid, weightFits } from "../lib/reconcile";
import {
  appleSaving,
  apples,
  peppers,
  tortillaSaving,
  weightRow,
  youPayPurchase,
} from "./why-off-fixtures";

describe("why a receipt's lines are off (2S)", () => {
  it("breaks the lines down and measures the gap", () => {
    expect(breakdown(youPayPurchase)).toMatchObject({
      items: "6.57",
      savings: "1.65",
      linesTotal: "4.92",
      total: "6.57",
      gap: "-1.65",
    });
  });

  it("compares what the receipt prints about itself", () => {
    expect(checks(youPayPurchase).map((c) => [c.key, c.ok])).toEqual([
      ["payment", true],
      ["savings", true],
      ["items", true],
    ]);
    expect(totalIsPaid(youPayPurchase)).toBe(true);
  });

  it("names every saving counted twice when the gap is exactly the savings", () => {
    const [first] = hints(youPayPurchase);
    expect(first).toMatchObject({ kind: "savings_in_price", amount: "1.65" });
    expect(first.kind === "savings_in_price" && first.lines.map((l) => l.seq)).toEqual([2, 6]);
  });

  it("finds the one saving whose misread digit explains the printed savings total", () => {
    const misread = { ...appleSaving, line_total: "1.1600" };
    const purchase = {
      ...youPayPurchase,
      lines: youPayPurchase.lines.map((l) => (l.id === misread.id ? misread : l)),
    };
    expect(hints(purchase)).toContainEqual({
      kind: "saving_misread",
      line: misread,
      from: "1.1600",
      to: "1.15",
    });
  });

  it("counts a saving already in its price once, beside the others", () => {
    const kept = { ...tortillaSaving, line_total: "0.0000", in_price_amount: "0.5000" };
    const purchase = {
      ...youPayPurchase,
      lines: youPayPurchase.lines.map((l) => (l.id === kept.id ? kept : l)),
    };
    expect(breakdown(purchase)).toMatchObject({ savings: "1.15", inPrice: "0.50" });
    expect(checks(purchase).find((c) => c.key === "savings")?.ok).toBe(true);
  });

  it("offers a loose weight row to the one neighbour its weight at the rate comes to", () => {
    expect(weightFits(weightRow, { above: peppers, below: apples })).toEqual({
      into: peppers,
      where: "above",
      amount: "0.63",
    });
    expect(weightFits(weightRow, { above: apples, below: apples })).toBeNull();
    expect(hints(youPayPurchase).map((h) => h.kind)).toContain("weights_not_joined");
  });

  it("points at one line that comes to exactly the gap when nothing else explains it", () => {
    const extra = { ...peppers, id: "extra", seq: 3, line_total: "1.0000" };
    const purchase = {
      ...youPayPurchase,
      printed_checks: null,
      lines: [peppers, { ...apples, seq: 2 }, extra],
      lines_total: "4.58",
      total: "3.58",
    };
    expect(hints(purchase)).toEqual([{ kind: "line_is_gap", line: extra, amount: "1.00" }]);
  });

  it("tells one misread digit from a different amount", () => {
    expect(oneDigitOff("1.89", "1.99")).toBe(true);
    expect(oneDigitOff("1.89", "2.09")).toBe(false);
    expect(oneDigitOff("9.99", "10.99")).toBe(false);
  });
});
