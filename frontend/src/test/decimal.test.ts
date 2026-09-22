import { describe, expect, it } from "vitest";
import { add, cmp, div, formatMoney, isDecimal, isNonNegativeDecimal, isZero, mul, roundTo, stripZeros, sub } from "../lib/decimal";

describe("decimal", () => {
  it("adds without floating-point error", () => {
    expect(add("0.1", "0.2")).toBe("0.3");
    expect(add("9.2169", "5")).toBe("14.2169");
    expect(add("1.005", "-1")).toBe("0.005");
    expect(sub("10", "9.2169")).toBe("0.7831");
    expect(sub("0.3", "0.1")).toBe("0.2");
  });

  it("multiplies exactly, keeping every digit", () => {
    expect(mul("2.31", "3.99")).toBe("9.2169");
    expect(mul("0.1", "0.1")).toBe("0.01");
    expect(mul("3", "4")).toBe("12");
    expect(mul("1.5", "-2")).toBe("-3.0");
    expect(mul("0.5", "0")).toBe("0.0");
  });

  it("divides to a fixed number of places, rounding half to even", () => {
    expect(div("9.2169", "2.31", 4)).toBe("3.9900");
    expect(div("10", "2.31", 4)).toBe("4.3290");
    expect(div("1", "3", 4)).toBe("0.3333");
    expect(div("2", "3", 4)).toBe("0.6667");
    // Exact halves go to the even neighbour.
    expect(div("0.00005", "1", 4)).toBe("0.0000");
    expect(div("0.00015", "1", 4)).toBe("0.0002");
    expect(div("0.00025", "1", 4)).toBe("0.0002");
    expect(div("-1", "8", 2)).toBe("-0.12");
    expect(() => div("1", "0")).toThrow(/zero/);
  });

  it("rounds half to even and pads to the requested places", () => {
    expect(roundTo("2.5", 0)).toBe("2");
    expect(roundTo("3.5", 0)).toBe("4");
    expect(roundTo("2.675", 2)).toBe("2.68");
    expect(roundTo("2.665", 2)).toBe("2.66");
    expect(roundTo("2.6651", 2)).toBe("2.67");
    expect(roundTo("5", 4)).toBe("5.0000");
    expect(roundTo("-0.00005", 4)).toBe("0.0000");
  });

  it("compares and tests for zero", () => {
    expect(cmp("1.0", "1")).toBe(0);
    expect(cmp("0.0031", "0")).toBe(1);
    expect(cmp("-0.5", "0.5")).toBe(-1);
    expect(isZero("0.0000")).toBe(true);
    expect(isZero("-0")).toBe(true);
    expect(isZero("0.0001")).toBe(false);
  });

  it("validates decimal text", () => {
    for (const ok of ["1", "1.", ".5", "0.00", "-3.2", " 12 "]) expect(isDecimal(ok)).toBe(true);
    for (const bad of ["", ".", "-", "1e3", "1,5", "abc", "1.2.3"]) expect(isDecimal(bad)).toBe(false);
    expect(isNonNegativeDecimal("0")).toBe(true);
    expect(isNonNegativeDecimal("-0")).toBe(true);
    expect(isNonNegativeDecimal("-0.01")).toBe(false);
  });

  it("strips zeros and formats money for display only", () => {
    expect(stripZeros("3.9900", 2)).toBe("3.99");
    expect(stripZeros("5.0000", 2)).toBe("5.00");
    expect(stripZeros("5", 2)).toBe("5.00");
    expect(stripZeros("1.50")).toBe("1.5");
    expect(formatMoney("9.2169")).toBe("$9.22");
    expect(formatMoney("9.2169", 2, 4)).toBe("$9.2169");
    expect(formatMoney("0.002200", 2, 6)).toBe("$0.0022");
    expect(formatMoney("-0.7831")).toBe("-$0.78");
    expect(formatMoney(null)).toBe("");
    expect(formatMoney("nonsense")).toBe("");
  });
});
