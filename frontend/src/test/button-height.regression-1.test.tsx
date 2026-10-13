// Regression: ISSUE-006 — a compact Button's own min-height lost to the default lg:min-h-10, so desktop compact buttons were 40px
// Found by /qa on 2026-10-13
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Button } from "../components/ui";

// Two min-h utilities on one element are settled by stylesheet order, not class
// order, so the Button must not add its default when the caller sets one.

describe("Button height", () => {
  it("keeps the default 44px / 40px minimum when the caller sets none", () => {
    render(<Button>Save</Button>);
    const classes = screen.getByRole("button", { name: "Save" }).className.split(/\s+/);
    expect(classes).toEqual(expect.arrayContaining(["min-h-11", "lg:min-h-10"]));
  });

  it("uses only the caller's minimum height when it sets one", () => {
    render(<Button className="min-h-11 lg:min-h-8 px-2 text-xs">Edit</Button>);
    const classes = screen.getByRole("button", { name: "Edit" }).className.split(/\s+/);
    expect(classes).toContain("lg:min-h-8");
    expect(classes).not.toContain("lg:min-h-10");
    expect(classes.filter((c) => c === "min-h-11")).toHaveLength(1);
  });
});
