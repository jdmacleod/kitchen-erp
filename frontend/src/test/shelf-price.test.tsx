import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import { flourId, hits, units } from "./catalog-fixtures";
import { chainLocation, chainLocationId, marketLocation, marketLocationId } from "./geo-fixtures";
import { adminUser, jsonResponse, mainRegion, mockApi, renderApp, type RecordedCall } from "./helpers";
import { observationNoDensity, observationOk } from "./purchase-fixtures";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const LAST_LOCATION = "kerp.lastVendorLocationId";

function baseRoutes() {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": (call: RecordedCall) =>
      jsonResponse(200, {
        items: call.query.has("near")
          ? [
              { ...marketLocation, distance_m: "120.5" },
              { ...chainLocation, distance_m: "5400" },
            ]
          : [chainLocation, marketLocation],
      }),
    "GET /products/search": (call: RecordedCall) => jsonResponse(200, { items: call.query.get("q")?.includes("flour") ? hits : [] }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
  };
}

afterEach(() => {
  localStorage.clear();
  delete (navigator as { geolocation?: unknown }).geolocation;
});

describe("shelf price", () => {
  it("posts the observation with an Idempotency-Key and shows the normalized price", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    const calls = mockApi({
      ...baseRoutes(),
      "POST /price-observations": () => jsonResponse(201, observationOk),
    });
    const user = userEvent.setup();
    renderApp("/prices/new");

    const location = await screen.findByLabelText("Location");
    // No position in jsdom: the remembered location is the default.
    await waitFor(() => expect(location).toHaveValue(chainLocationId));

    await user.type(screen.getByRole("combobox", { name: "Product" }), "flour");
    await user.click(await screen.findByRole("option", { name: /All-Purpose Flour/ }));
    expect(screen.getByTestId("shelf-product-choice")).toHaveTextContent("Millstone All-Purpose Flour");
    // One "each" is one pack; the price field is next.
    expect(screen.getByLabelText("Unit")).toHaveValue("each");
    expect(screen.getByLabelText("Quantity")).toHaveValue("1");
    expect(screen.getByLabelText("Price")).toHaveFocus();

    await user.keyboard("4.99{Enter}");

    expect(await screen.findByTestId("norm-price")).toHaveTextContent("$0.0022 per g");
    const post = calls.find((c) => c.method === "POST" && c.path === "/price-observations");
    expect(post?.body).toEqual({
      product_id: hits[0].id,
      vendor_location_id: chainLocationId,
      price: "4.99",
      qty: "1",
      unit: "each",
    });
    expect(post?.headers.get("Idempotency-Key")).toMatch(UUID);

    // Ready for the next tag: product cleared and focused, location kept.
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Product" })).toHaveFocus());
    expect(screen.getByLabelText("Price")).toHaveValue("");
    expect(location).toHaveValue(chainLocationId);
    expect(localStorage.getItem(LAST_LOCATION)).toBe(chainLocationId);
  });

  it("names the missing bridge and links to it when the price cannot be normalized", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    const calls = mockApi({
      ...baseRoutes(),
      "POST /price-observations": () => jsonResponse(201, observationNoDensity),
    });
    const user = userEvent.setup();
    renderApp("/prices/new");
    await waitFor(() => expect(screen.getByLabelText("Location")).toHaveValue(chainLocationId));

    await user.type(screen.getByRole("combobox", { name: "Product" }), "flour");
    await user.click(await screen.findByRole("option", { name: /All-Purpose Flour/ }));
    await user.type(screen.getByLabelText("Price"), "1.25");
    await user.selectOptions(screen.getByLabelText("Unit"), "cup");
    await user.click(screen.getByRole("checkbox", { name: "Sale price" }));
    await user.click(screen.getByRole("button", { name: "Save price" }));

    const note = await within(mainRegion()).findByRole("status", {}, { timeout: 2000 });
    await waitFor(() => expect(note).toHaveTextContent("has no density"));
    expect(screen.getByRole("link", { name: "Add a density" })).toHaveAttribute("href", `/ingredients/${flourId}#density-heading`);
    const post = calls.find((c) => c.method === "POST" && c.path === "/price-observations");
    expect(post?.body).toEqual({
      product_id: hits[0].id,
      vendor_location_id: chainLocationId,
      price: "1.25",
      qty: "1",
      unit: "cup",
      is_promo: true,
    });
  });

  it("defaults to the nearest location when the browser yields a position", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    // A synthetic position inside the SECURITY.md test grid.
    Object.defineProperty(navigator, "geolocation", {
      configurable: true,
      value: {
        getCurrentPosition: (ok: (p: { coords: { latitude: number; longitude: number } }) => void) =>
          ok({ coords: { latitude: 33.45, longitude: -120.55 } }),
      },
    });
    const calls = mockApi(baseRoutes());
    renderApp("/prices/new");

    const location = await screen.findByLabelText("Location");
    await waitFor(() => expect(location).toHaveValue(marketLocationId));
    const near = calls.find((c) => c.path.startsWith("/vendor-locations") && c.query.has("near"));
    expect(near?.query.get("near")).toBe("33.45,-120.55");
    expect(screen.getByRole("option", { name: /Pier Farmers Market · 121 m/ })).toBeInTheDocument();
  });
});
