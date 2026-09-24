import { describe, expect, it } from "vitest";
import { parseLatLon } from "../lib/latlon";

describe("parseLatLon", () => {
  it("accepts the shapes a map application copies", () => {
    // Coordinates in the synthetic Pacific box from SECURITY.md.
    for (const text of ["33.512345, -120.487654", "33.512345,-120.487654", "33.512345 -120.487654", "  33.512345 ,  -120.487654  "]) {
      expect(parseLatLon(text)).toEqual({ lat: "33.512345", lon: "-120.487654" });
    }
    expect(parseLatLon("+33.5, +120.4")).toEqual({ lat: "+33.5", lon: "+120.4" });
    expect(parseLatLon("33, -120")).toEqual({ lat: "33", lon: "-120" });
  });

  it("carries the digits through as typed, never as a number", () => {
    // Non-negotiable 1: nothing in the path from input to storage turns a
    // printed value into a float. A trailing zero that Number() would drop
    // survives, and so does precision past what a double can hold.
    expect(parseLatLon("33.5000000, -120.4000000")).toEqual({ lat: "33.5000000", lon: "-120.4000000" });
    expect(parseLatLon("33.12345678901234567890, -120.1")?.lat).toBe("33.12345678901234567890");
  });

  it("rejects what it cannot read rather than guessing", () => {
    for (const text of ["", "33.5", "not coordinates", "33.5, -120.4, 12", "33°30'N 120°29'W", "91, 0", "0, 181", "-91, 0"]) {
      expect(parseLatLon(text)).toBeNull();
    }
  });
});
