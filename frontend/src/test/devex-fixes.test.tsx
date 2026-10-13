import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { formatPack, productTitle, unitLabel } from "../api/catalog";
import { HitRow } from "../components/catalog/ProductTypeahead";
import { flour, flourId, flourProduct, flourProductId, hits, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

// Found by /devex-review on 2026-10-05, with invented products.
// Imports stay at the top: importing the app inside a test counts its cold
// module load against that test's 5 s timeout (#328).

describe("product titles and units", () => {
  it("names the brand once when the name already starts with it", () => {
    expect(productTitle({ brand: "Larkfield", name: "Larkfield Rolled Oats" })).toBe(
      "Larkfield Rolled Oats",
    );
    expect(productTitle({ brand: "Larkfield's", name: "Larkfield's Rye - 20 oz" })).toBe(
      "Larkfield's Rye - 20 oz",
    );
    expect(productTitle({ brand: "Larkfield", name: "Larkfield" })).toBe("Larkfield");
    expect(productTitle({ brand: "Larkfield", name: "Rolled oats" })).toBe("Larkfield Rolled oats");
    expect(productTitle({ brand: "Lark", name: "Larkfield oats" })).toBe("Lark Larkfield oats");
    expect(productTitle({ brand: null, name: "Rolled oats" })).toBe("Rolled oats");
  });

  it("writes unit codes as people do", () => {
    expect(unitLabel("fl_oz")).toBe("fl oz");
    expect(formatPack("17", "fl_oz")).toBe("17 fl oz");
  });
});

describe("a product search hit", () => {
  it("is read aloud with its parts apart", () => {
    const { container } = render(
      <HitRow hit={{ ...hits[0], pack_qty: "17", pack_unit: "fl_oz" }} />,
    );
    const label = container.firstElementChild?.getAttribute("aria-label") ?? "";
    expect(label).toContain(`${hits[0].name}, `);
    expect(label).toContain("17 fl oz");
    expect(label).toMatch(/, matched by (barcode|name|brand|ingredient)$/);
  });
});

describe("looking a product up from its page", () => {
  it("sends its barcode, or a pasted page, to the lookup helper", async () => {
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /inbox": () =>
        jsonResponse(200, { items: [], reading: { count: 0, oldest_at: null, stalled: false } }),
      [`GET /ingredients/${flourId}`]: () => jsonResponse(200, flour),
      "GET /units": () => jsonResponse(200, { items: units }),
      "GET /products-helper": () => jsonResponse(200, { configured: true }),
      [`GET /products/${flourProductId}`]: () => jsonResponse(200, flourProduct),
      [`GET /products/${flourProductId}/prices`]: () =>
        jsonResponse(200, { points: [], latest: [] }),
      [`GET /products/${flourProductId}/photos`]: () =>
        jsonResponse(200, { items: [], primary_image_id: null }),
      "GET /price-observations": () => jsonResponse(200, { items: [], next_cursor: null }),
      [`POST /products/${flourProductId}/look-up`]: () =>
        jsonResponse(200, {
          id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a01",
          kind: "gtin",
          status: "open",
        }),
    });
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);
    // This is the file's first render of the whole app, so the wait covers its
    // cold start (about 1.1 s alone on a loaded machine), past findBy's 1 s default.
    const card = (
      await screen.findByRole("heading", { name: "Look this up online" }, { timeout: 4000 })
    ).closest("section, div")!.parentElement!;
    await user.click(within(card).getByRole("button", { name: "Look up its barcode" }));
    expect(
      await within(card).findByText(/The lookup helper will look up the barcode/),
    ).toBeInTheDocument();
    await user.type(
      within(card).getByLabelText("A store page for it"),
      "https://shop.example.test/p/flour",
    );
    await user.click(within(card).getByRole("button", { name: "Read this page" }));
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith("/look-up"))).toHaveLength(2));
    const bodies = calls.filter((c) => c.path.endsWith("/look-up")).map((c) => c.body);
    expect(bodies).toEqual([{}, { page_url: "https://shop.example.test/p/flour" }]);
  });
});
