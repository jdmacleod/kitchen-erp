import { describe, expect, it } from "vitest";
import type { CostLine } from "../api/recipes";
import { conversionFix } from "../components/cook/CostTable";
import { stewCost } from "./recipe-fixtures";

// The fixture's unconvertible line (ground fennel), with the failure the backend gave.
function unconvertible(): CostLine {
  const found = stewCost().lines.find((l) => l.status === "unconvertible");
  if (!found) throw new Error("the fixture has an unconvertible line");
  return found;
}
const base = unconvertible();
const price = stewCost().lines.find((l) => l.price)?.price ?? null;
const withCode = (failure_code: string, over: Partial<CostLine["line"]> = {}): CostLine => ({
  ...base,
  failure_code,
  line: { ...base.line, ...over },
});
const ingredient = `/catalog/ingredients/${base.line.ingredient_id}`;

describe("an unconvertible cost row says what is missing", () => {
  it("asks for a density only when density is what is missing", () => {
    expect(conversionFix(withCode("no_density"))).toEqual({
      label: "add density",
      to: `${ingredient}#density-heading`,
    });
  });

  it("asks for the measure the line is written in", () => {
    expect(conversionFix(withCode("unknown_measure", { unit_text: "sprig" }))).toEqual({
      label: "add a “sprig” measure",
      to: `${ingredient}#measures-heading`,
    });
  });

  it("asks for one bunch to be weighed", () => {
    expect(conversionFix(withCode("no_bunch_weight"))).toEqual({
      label: "weigh one bunch",
      to: `${ingredient}#measures-heading`,
    });
  });

  it("sends a missing pack to the product that was priced", () => {
    if (!price) throw new Error("the fixture has a priced line");
    const line = { ...withCode("no_pack"), price: { ...price, product_id: "p9" } };
    expect(conversionFix(line)).toEqual({ label: "set the pack size", to: "/catalog/products/p9" });
  });

  it("gives words but no link when the line has no ingredient", () => {
    expect(conversionFix(withCode("no_density", { ingredient_id: null })).to).toBeNull();
  });
});
