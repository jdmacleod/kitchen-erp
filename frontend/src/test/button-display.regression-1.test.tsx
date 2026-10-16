// Regression: a desktop-only Button ("hidden lg:inline-flex") showed on phones,
// because its own "hidden" lost to the default inline-flex by stylesheet order.
// Found reviewing receipt-review screenshots on 2026-10-16 ("Side by side" at 390px).
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { Button } from "../components/ui";

describe("Button display", () => {
  it("is inline-flex when the caller sets no display", () => {
    render(<Button>Save</Button>);
    const classes = screen.getByRole("button", { name: "Save" }).className.split(/\s+/);
    expect(classes).toContain("inline-flex");
  });

  it("uses only the caller's display when it sets one", () => {
    render(<Button className="hidden lg:inline-flex">Side by side</Button>);
    const classes = screen.getByRole("button", { name: "Side by side" }).className.split(/\s+/);
    expect(classes).toEqual(expect.arrayContaining(["hidden", "lg:inline-flex"]));
    expect(classes).not.toContain("inline-flex");
  });

  it("keeps the default with a responsive-only display such as lg:hidden", () => {
    render(<Button className="lg:hidden">More</Button>);
    const classes = screen.getByRole("button", { name: "More" }).className.split(/\s+/);
    expect(classes).toEqual(expect.arrayContaining(["inline-flex", "lg:hidden"]));
  });
});
