import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { Product } from "../api/catalog";
import { flourProduct, flourProductId, hits, units } from "./catalog-fixtures";
import { chainLocation, chainLocationId, marketLocation } from "./geo-fixtures";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";
import { manualPurchase } from "./purchase-fixtures";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const LAST_LOCATION = "kerp.lastVendorLocationId";

function baseRoutes() {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
    "GET /products/search": (call: RecordedCall) => jsonResponse(200, { items: call.query.get("q")?.includes("flour") ? hits : [] }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    // Flour was last bought by the pound; the other product has never been bought.
    [`GET /products/${flourProductId}/last-purchase-unit`]: () => jsonResponse(200, { unit: "lb" }),
    [`GET /products/${hits[1].id}/last-purchase-unit`]: () => errorResponse(404, "not_found", "no purchases"),
  };
}

const line = (n: number) => screen.getByRole("group", { name: `Line ${n}` });

/** Pick the first flour hit into line n with the pointer. */
async function pickFlour(user: ReturnType<typeof userEvent.setup>, n: number) {
  await user.type(within(line(n)).getByRole("combobox", { name: `Product ${n}` }), "flour");
  await user.click(await screen.findByRole("option", { name: /All-Purpose Flour/ }));
  await waitFor(() => expect(within(line(n)).getByLabelText(`Unit ${n}`)).toHaveValue("lb"));
}

afterEach(() => localStorage.clear());

describe("manual purchase entry", () => {
  it("computes the line total from a unit price and the unit price from a line total", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    mockApi(baseRoutes());
    const user = userEvent.setup();
    renderApp("/purchases/new");
    await screen.findByRole("group", { name: "Line 1" });

    await pickFlour(user, 1);
    await user.type(within(line(1)).getByLabelText("Quantity 1"), "2.31");
    await user.type(within(line(1)).getByLabelText("Unit price 1"), "3.99");
    // 2.31 × 3.99 = 9.2169 exactly; nothing to round at four places.
    const total = within(line(1)).getByLabelText("Line total 1");
    expect(total).toHaveValue("9.2169");
    expect(within(line(1)).getByTestId(/line-total-computed$/)).toHaveTextContent("computed");
    expect(within(line(1)).queryByTestId(/unit-price-computed$/)).not.toBeInTheDocument();
    expect(screen.getByTestId("new-purchase-running-total")).toHaveTextContent("$9.22");

    // Typing a line total flips the driver: 10 ÷ 2.31 = 4.329004… → 4.3290 half-even, shown trimmed.
    await user.clear(total);
    await user.type(total, "10");
    expect(within(line(1)).getByLabelText("Unit price 1")).toHaveValue("4.329");
    expect(within(line(1)).getByTestId(/unit-price-computed$/)).toHaveTextContent("computed");
    expect(within(line(1)).queryByTestId(/line-total-computed$/)).not.toBeInTheDocument();
    expect(screen.getByTestId("new-purchase-running-total")).toHaveTextContent("$10.00");
  });

  it("commits a line on Enter in its last field and opens the next with focus on its product", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    mockApi(baseRoutes());
    const user = userEvent.setup();
    renderApp("/purchases/new");
    await screen.findByRole("group", { name: "Line 1" });

    await pickFlour(user, 1);
    await user.type(within(line(1)).getByLabelText("Quantity 1"), "1");
    await user.type(within(line(1)).getByLabelText("Unit price 1"), "4.99{Enter}");

    const second = await screen.findByRole("group", { name: "Line 2" });
    await waitFor(() => expect(within(second).getByRole("combobox", { name: "Product 2" })).toHaveFocus());
    expect(within(line(1)).getByTestId(/product-choice$/)).toHaveTextContent("Millstone All-Purpose Flour");
  });

  it("enters three lines by keyboard alone and saves them in one request", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    const calls = mockApi({
      ...baseRoutes(),
      "POST /purchases": () => jsonResponse(201, manualPurchase),
      [`GET /purchases/${manualPurchase.id}`]: () => jsonResponse(200, manualPurchase),
    });
    const user = userEvent.setup();
    renderApp("/purchases/new");
    await screen.findByRole("group", { name: "Line 1" });
    await waitFor(() => expect(screen.getByLabelText("Location")).toHaveValue(chainLocationId));

    const enterLine = async (n: number, hit: number, qty: string, unit: string, price: string) => {
      const box = within(line(n)).getByRole("combobox", { name: `Product ${n}` });
      await waitFor(() => expect(box).toHaveFocus());
      await user.keyboard("flour");
      // Wait for the typeahead's hits (the selects' own <option>s also carry that role).
      await screen.findByRole("option", { name: /All-Purpose Flour/ });
      await user.keyboard(`${"{ArrowDown}".repeat(hit + 1)}{Enter}`);
      await waitFor(() => expect(within(line(n)).getByLabelText(`Quantity ${n}`)).toHaveFocus());
      await user.keyboard(`${qty}{Enter}`);
      const unitSelect = within(line(n)).getByLabelText(`Unit ${n}`);
      expect(unitSelect).toHaveFocus();
      await waitFor(() => expect(unitSelect).toHaveValue(unit));
      await user.keyboard("{Enter}");
      expect(within(line(n)).getByLabelText(`Unit price ${n}`)).toHaveFocus();
      await user.keyboard(`${price}{Enter}`);
    };

    within(line(1)).getByRole("combobox", { name: "Product 1" }).focus();
    await enterLine(1, 0, "2.31", "lb", "3.99");
    await enterLine(2, 1, "1", "each", "5");
    await enterLine(3, 0, "2", "lb", "1.5");
    await screen.findByRole("group", { name: "Line 4" });
    expect(screen.getByTestId("new-purchase-running-total")).toHaveTextContent("$17.22");

    await user.keyboard("{Control>}{Enter}{/Control}");

    expect(await screen.findByRole("heading", { name: /Pier Farmers Market/ })).toBeInTheDocument();
    const posts = calls.filter((c) => c.method === "POST" && c.path === "/purchases");
    expect(posts).toHaveLength(1);
    const body = posts[0].body as { vendor_location_id: string; purchased_at: string; total?: string; lines: unknown[] };
    expect(body.vendor_location_id).toBe(chainLocationId);
    expect(body.total).toBeUndefined();
    expect(body.lines).toEqual([
      { product_id: flourProductId, qty: "2.31", unit: "lb", unit_price: "3.99" },
      { product_id: hits[1].id, qty: "1", unit: "each", unit_price: "5" },
      { product_id: flourProductId, qty: "2", unit: "lb", unit_price: "1.5" },
    ]);
    // Today's date at the current local time, sent as a UTC instant.
    const sent = new Date(body.purchased_at);
    expect(body.purchased_at).toMatch(/Z$/);
    expect(sent.toDateString()).toBe(new Date().toDateString());
    expect(posts[0].headers.get("Idempotency-Key")).toMatch(UUID);
    expect(localStorage.getItem(LAST_LOCATION)).toBe(chainLocationId);
  });

  it("creates a product and its ingredient inline without losing the lines already entered", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    const created: Product = {
      ...flourProduct,
      id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5d09",
      ingredient: { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5b09", name: "rolled oats", canonical_unit: "g", active: true, category: null, category_key: null },
      brand: null,
      name: "Rolled Oats",
      pack_qty: null,
      pack_unit: null,
      barcode: null,
    };
    const calls = mockApi({
      ...baseRoutes(),
      "POST /products": () => jsonResponse(201, created),
      [`GET /products/${created.id}/last-purchase-unit`]: () => errorResponse(404, "not_found", "no purchases"),
    });
    const user = userEvent.setup();
    renderApp("/purchases/new");
    await screen.findByRole("group", { name: "Line 1" });

    await pickFlour(user, 1);
    await user.type(within(line(1)).getByLabelText("Quantity 1"), "1");
    await user.type(within(line(1)).getByLabelText("Unit price 1"), "4.99{Enter}");
    const second = await screen.findByRole("group", { name: "Line 2" });

    await user.click(within(second).getByRole("button", { name: "New product" }));
    const form = within(second).getByRole("group", { name: "New product" });
    await user.type(within(form).getByRole("combobox", { name: "Ingredient" }), "rolled oats");
    await user.click(await within(form).findByRole("option", { name: /Create new ingredient/ }));
    await user.type(within(form).getByLabelText("Name"), "Rolled Oats{Enter}");

    await waitFor(() => expect(within(second).getByTestId(/product-choice$/)).toHaveTextContent("Rolled Oats"));
    const post = calls.find((c) => c.method === "POST" && c.path === "/products");
    expect(post?.body).toEqual({ name: "Rolled Oats", ingredient: { name: "rolled oats" } });
    expect(post?.headers.get("Idempotency-Key")).toMatch(UUID);
    // Enter inside the inline form created the product; it did not save the purchase.
    expect(calls.some((c) => c.method === "POST" && c.path === "/purchases")).toBe(false);
    // Line 1 is exactly as entered.
    expect(within(line(1)).getByTestId(/product-choice$/)).toHaveTextContent("Millstone All-Purpose Flour");
    expect(within(line(1)).getByLabelText("Quantity 1")).toHaveValue("1");
    expect(within(line(1)).getByLabelText("Unit 1")).toHaveValue("lb");
    expect(within(line(1)).getByLabelText("Unit price 1")).toHaveValue("4.99");
    expect(within(line(1)).getByLabelText("Line total 1")).toHaveValue("4.99");
    expect(within(second).getByLabelText("Unit 2")).toHaveValue("each");
  });

  it("reconciles an entered total against the running total", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    mockApi(baseRoutes());
    const user = userEvent.setup();
    renderApp("/purchases/new");
    await screen.findByRole("group", { name: "Line 1" });

    await pickFlour(user, 1);
    await user.type(within(line(1)).getByLabelText("Quantity 1"), "2.31");
    await user.type(within(line(1)).getByLabelText("Unit price 1"), "3.99");
    expect(screen.queryByTestId("new-purchase-difference")).not.toBeInTheDocument();

    const slip = screen.getByLabelText("Total on the slip");
    await user.type(slip, "10");
    expect(screen.getByTestId("new-purchase-difference")).toHaveTextContent("slip is higher by $0.7831");
    await user.clear(slip);
    await user.type(slip, "9.2169");
    expect(screen.getByTestId("new-purchase-difference")).toHaveTextContent("matches");
    await user.clear(slip);
    await user.type(slip, "9");
    expect(screen.getByTestId("new-purchase-difference")).toHaveTextContent("slip is lower by $0.2169");
  });

  it("refuses to save a line with a product but no price, and keeps the draft", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    const calls = mockApi(baseRoutes());
    const user = userEvent.setup();
    renderApp("/purchases/new");
    await screen.findByRole("group", { name: "Line 1" });

    await pickFlour(user, 1);
    await user.type(within(line(1)).getByLabelText("Quantity 1"), "2");
    await user.click(screen.getByRole("button", { name: "Save purchase" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Enter a unit price or a line total.");
    expect(within(line(1)).getByLabelText("Unit price 1")).toHaveFocus();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });
});
