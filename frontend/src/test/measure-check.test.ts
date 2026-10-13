import { describe, expect, it } from "vitest";
import { measureLooksOff } from "../lib/measureCheck";
import { units } from "./catalog-fixtures";

describe("measureLooksOff", () => {
  it("passes measures a food could weigh", () => {
    expect(measureLooksOff("cup", "125", "g", units)).toBeNull();
    expect(measureLooksOff("cup, sliced", "150", "g", units)).toBeNull();
    expect(measureLooksOff("oz, boneless", "28.35", "g", units)).toBeNull();
  });

  it("flags a volume measure lighter or heavier than any food", () => {
    expect(measureLooksOff("cup juice", "0.33", "g", units)).toMatch(/lighter than any food/);
    expect(measureLooksOff("cups", "2000", "g", units)).toMatch(/heavier than any food/);
  });

  it("flags a mass measure that disagrees with its unit", () => {
    expect(measureLooksOff("lb", "100", "g", units)).toBe(
      "One lb at 100 g doesn't match 1 lb (454 g).",
    );
  });

  it("leaves labels it can't read and count or volume ingredients alone", () => {
    expect(measureLooksOff("clove", "0.1", "g", units)).toBeNull();
    expect(measureLooksOff("cup", "1", "ml", units)).toBeNull();
    expect(measureLooksOff("cup", "1", "each", units)).toBeNull();
    expect(measureLooksOff("cup", "not a number", "g", units)).toBeNull();
  });
});
