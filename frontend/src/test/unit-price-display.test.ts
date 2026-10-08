import { describe, expect, it } from "vitest";
import { formatUnitPrice } from "../components/pricebook/PriceAge";

// issue 245: the server chooses the unit and the places; the browser only prints them.
describe("formatUnitPrice", () => {
  it("prints the server's price and unit as sent", () => {
    expect(formatUnitPrice("2.99", "lb")).toBe("$2.99/lb");
    expect(formatUnitPrice("2.00", "oz")).toBe("$2.00/oz");
    // Three places under a dollar, trailing zero kept: close prices stay distinguishable.
    expect(formatUnitPrice("0.680", "fl oz")).toBe("$0.680/fl oz");
    expect(formatUnitPrice("9.10", "L")).toBe("$9.10/L");
  });

  it("says each after the price", () => {
    expect(formatUnitPrice("1.29", "each")).toBe("$1.29 each");
  });

  it("shows a dash when nothing is normalized", () => {
    expect(formatUnitPrice(null, "lb")).toBe("—");
    expect(formatUnitPrice("2.99", null)).toBe("—");
  });

  it("does no arithmetic: a per-gram figure is not converted", () => {
    expect(formatUnitPrice("0.0022", "g")).toBe("$0.0022/g");
  });
});
