import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Purchase } from "../api/purchases";
import { flourProductId, hits, units } from "./catalog-fixtures";
import { chainLocation, marketLocation, marketLocationId } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";
import { manualPurchase, purchaseId } from "./purchase-fixtures";

function baseRoutes() {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
  };
}

describe("purchases", () => {
  it("lists purchases with their totals and loads more by cursor", async () => {
    const second: Purchase = {
      ...manualPurchase,
      id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8002",
      purchased_at: "2026-09-12T10:00:00Z",
      total: null,
      computed_total: "3.50",
      status: "draft",
      source: "receipt",
      lines: [manualPurchase.lines[0]],
    };
    const calls = mockApi({
      ...baseRoutes(),
      "GET /purchases": (call: RecordedCall) =>
        call.query.get("cursor") === "page2"
          ? jsonResponse(200, { items: [second], next_cursor: null })
          : jsonResponse(200, { items: [manualPurchase], next_cursor: "page2" }),
    });
    const user = userEvent.setup();
    renderApp("/shop/purchases");

    const table = await screen.findByRole("table", { name: "Purchases" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(1);
    expect(rows[0]).toHaveTextContent("Pier Farmers Market");
    expect(rows[0]).toHaveTextContent("$14.21");
    expect(rows[0]).toHaveTextContent("Committed");
    expect(rows[0]).toHaveTextContent("Manual");
    expect(within(rows[0]).getByRole("link")).toHaveAttribute("href", `/shop/purchases/${purchaseId}`);
    expect(within(rows[0]).getAllByRole("cell").at(-1)).toHaveTextContent("2");

    await user.click(screen.getByRole("button", { name: "Load more" }));
    await waitFor(() => expect(within(table).getAllByRole("row")).toHaveLength(3));
    const more = within(table).getAllByRole("row")[2];
    expect(more).toHaveTextContent("$3.50");
    expect(more).toHaveTextContent("Draft");
    expect(more).toHaveTextContent("Receipt");
    expect(calls.some((c) => c.path.startsWith("/purchases") && c.query.get("cursor") === "page2")).toBe(true);
    expect(screen.queryByRole("button", { name: "Load more" })).not.toBeInTheDocument();
  });

  it("shows the lines of a purchase and whether each has an observation", async () => {
    mockApi({
      ...baseRoutes(),
      [`GET /purchases/${purchaseId}`]: () => jsonResponse(200, manualPurchase),
    });
    renderApp(`/shop/purchases/${purchaseId}`);

    expect(await screen.findByRole("heading", { name: /Pier Farmers Market/ })).toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Lines" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(2);
    expect(within(rows[0]).getByRole("link", { name: "Millstone All-Purpose Flour" })).toHaveAttribute("href", `/catalog/products/${flourProductId}`);
    expect(rows[0]).toHaveTextContent("2.31 × lb");
    expect(rows[0]).toHaveTextContent("$3.99");
    expect(rows[0]).toHaveTextContent("$9.22");
    expect(rows[0]).toHaveTextContent("observed");
    expect(rows[1]).toHaveTextContent("1 × each");
    expect(rows[1]).toHaveTextContent("no observation");
    expect(screen.getByRole("button", { name: "Edit" })).toBeInTheDocument();
  });

  it("edits a manual purchase with the entry form and PUTs every line", async () => {
    let purchase = manualPurchase;
    const calls = mockApi({
      ...baseRoutes(),
      [`GET /purchases/${purchaseId}`]: () => jsonResponse(200, purchase),
      [`PUT /purchases/${purchaseId}`]: () => {
        purchase = {
          ...purchase,
          total: "15.00",
          computed_total: "15.0000",
          lines: [{ ...purchase.lines[0], unit_price: "4.3290", line_total: "10", observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7102" }, purchase.lines[1]],
        };
        return jsonResponse(200, purchase);
      },
    });
    const user = userEvent.setup();
    renderApp(`/shop/purchases/${purchaseId}`);

    await screen.findByRole("heading", { name: /Pier Farmers Market/ });
    await user.click(screen.getByRole("button", { name: "Edit" }));

    const form = await screen.findByRole("form", { name: "Edit purchase" });
    expect(within(form).getByLabelText("Location")).toHaveValue(marketLocationId);
    expect(within(form).getByLabelText("Total on the slip")).toHaveValue("14.21");
    const first = within(form).getByRole("group", { name: "Line 1" });
    expect(within(first).getByText("Millstone All-Purpose Flour")).toBeInTheDocument();
    expect(within(first).getByLabelText("Quantity 1")).toHaveValue("2.31");
    expect(within(first).getByLabelText("Unit 1")).toHaveValue("lb");
    // The paid total is the driver; the unit price shown is derived from it.
    expect(within(first).getByLabelText("Line total 1")).toHaveValue("9.2169");
    expect(within(first).getByLabelText("Unit price 1")).toHaveValue("3.99");
    expect(within(first).getByTestId(/unit-price-computed$/)).toBeInTheDocument();
    expect(within(form).getByRole("group", { name: "Line 3" })).toBeInTheDocument();

    const total = within(first).getByLabelText("Line total 1");
    await user.clear(total);
    await user.type(total, "10");
    const slip = within(form).getByLabelText("Total on the slip");
    await user.clear(slip);
    await user.type(slip, "15");
    await user.click(within(form).getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(screen.queryByRole("form", { name: "Edit purchase" })).not.toBeInTheDocument());
    const put = calls.find((c) => c.method === "PUT");
    expect(put?.body).toEqual({
      vendor_location_id: marketLocationId,
      purchased_at: manualPurchase.purchased_at,
      total: "15",
      lines: [
        { product_id: flourProductId, qty: "2.31", unit: "lb", line_total: "10" },
        { product_id: hits[1].id, qty: "1", unit: "each", line_total: "5.0000" },
      ],
    });
    const table = screen.getByRole("table", { name: "Lines" });
    expect(within(table).getAllByRole("row")[1]).toHaveTextContent("$10.00");
  });
});
