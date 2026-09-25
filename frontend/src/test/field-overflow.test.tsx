import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SelectField, TextAreaField, inputClass } from "../components/catalog/fields";
import { Field } from "../components/ui";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { units } from "./catalog-fixtures";

// A grid or flex item defaults to `min-width: auto`, which is the width of its
// content: a <select> sized to its longest option pushed the New purchase form
// wider than a 375px screen and took the whole page sideways with it. Both the
// item and the control it wraps have to be allowed to shrink, so both carry
// `min-w-0`. jsdom does no layout, so the class is what a test can see; the
// width itself is checked in the phone project of the browser suite.

describe("shared fields can shrink below their content", () => {
  it("lets a Field and its wrapper shrink", () => {
    render(<Field id="f" label="Total on the slip" />);
    const input = screen.getByLabelText("Total on the slip");
    expect(input).toHaveClass("min-w-0");
    expect(input.parentElement).toHaveClass("min-w-0");
  });

  it("lets a SelectField and its wrapper shrink", () => {
    render(
      <SelectField id="s" label="Location">
        <option value="">Choose a location</option>
      </SelectField>,
    );
    const select = screen.getByLabelText("Location");
    expect(select).toHaveClass("min-w-0");
    expect(select.parentElement).toHaveClass("min-w-0");
  });

  it("lets a TextAreaField and its wrapper shrink", () => {
    render(<TextAreaField id="t" label="Note" />);
    const area = screen.getByLabelText("Note");
    expect(area).toHaveClass("min-w-0");
    expect(area.parentElement).toHaveClass("min-w-0");
  });

  it("carries the rule in the shared input chrome, which the comboboxes reuse", () => {
    // Combobox, OpeningHoursInput and the purchase line inputs take their
    // chrome from this string rather than from Field.
    expect(inputClass.split(/\s+/)).toContain("min-w-0");
  });
});

describe("the New purchase form's widest control", () => {
  it("puts the location select in a grid item that is allowed to shrink", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
      "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    renderApp("/purchases/new");

    const select = await screen.findByLabelText("Location");
    const item = select.parentElement;
    expect(item).toHaveClass("min-w-0");
    // The item really is the grid child: without `min-w-0` here the longest
    // option name, not the viewport, sets the row's width.
    expect(item?.parentElement?.className).toMatch(/\bgrid\b/);
    expect(select).toHaveClass("min-w-0");
  });
});
