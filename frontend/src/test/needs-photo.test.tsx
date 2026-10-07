import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { flourProduct, flourProductId, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

// Issue 184: products a barcode can't find had no way to be found or given a photo.

describe("products that need a photo", () => {
  it("filters the products with no main photo, and keeps it in the URL", async () => {
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      "GET /products": () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    const user = userEvent.setup();
    renderApp("/catalog/products");
    await user.click(await screen.findByRole("checkbox", { name: "Needs a photo" }));
    await waitFor(() =>
      expect(calls.some((c) => c.path.split("?")[0] === "/products" && c.query.get("no_photo") === "true")).toBe(true),
    );
    expect(await screen.findByText("No products match that need a photo")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(screen.getByRole("checkbox", { name: "Needs a photo" })).not.toBeChecked();
  });

  it("asks the lookup helper to search by name for a branded product with no barcode", async () => {
    const product = { ...flourProduct, barcode: null };
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      "GET /products-helper": () => jsonResponse(200, { configured: true }),
      [`GET /products/${flourProductId}`]: () => jsonResponse(200, product),
      [`GET /products/${flourProductId}/prices`]: () => jsonResponse(200, { points: [], latest: [] }),
      [`GET /products/${flourProductId}/photos`]: () => jsonResponse(200, { items: [], primary_image_id: null }),
      "GET /price-observations": () => jsonResponse(200, { items: [], next_cursor: null }),
      [`POST /products/${flourProductId}/look-up`]: () =>
        jsonResponse(200, { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a02", kind: "name", status: "open" }),
    });
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);
    const card = (await screen.findByRole("heading", { name: "Look this up online" })).parentElement!;
    expect(within(card).queryByRole("button", { name: "Look up its barcode" })).toBeNull();
    await user.click(within(card).getByRole("button", { name: "Search by name" }));
    expect(await within(card).findByRole("status")).toHaveTextContent("search by the brand, name and size");
    expect(calls.find((c) => c.method === "POST")?.body).toEqual({ by_name: true });
  });

  it("offers no name search for a product without a brand", async () => {
    const product = { ...flourProduct, barcode: null, brand: null };
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      "GET /products-helper": () => jsonResponse(200, { configured: true }),
      [`GET /products/${flourProductId}`]: () => jsonResponse(200, product),
      [`GET /products/${flourProductId}/prices`]: () => jsonResponse(200, { points: [], latest: [] }),
      [`GET /products/${flourProductId}/photos`]: () => jsonResponse(200, { items: [], primary_image_id: null }),
      "GET /price-observations": () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    renderApp(`/catalog/products/${flourProductId}`);
    const card = (await screen.findByRole("heading", { name: "Look this up online" })).parentElement!;
    expect(within(card).queryByRole("button", { name: "Search by name" })).toBeNull();
  });
});
