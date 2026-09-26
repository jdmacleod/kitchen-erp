import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { ProductPrices } from "../api/pricebook";
import { flourId, flourProductId, hits, units } from "./catalog-fixtures";
import { chainLocation, chainLocationId, marketLocation, marketLocationId } from "./geo-fixtures";
import { adminUser, jsonResponse, mainRegion, mockApi, renderApp, type RecordedCall } from "./helpers";
import { observationNoDensity, observationOk } from "./purchase-fixtures";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;
const LAST_LOCATION = "kerp.lastVendorLocationId";
const CHAIN = "Millstone Market — Millstone Harbour";
const BARCODE = hits[0].barcode!;

const emptyPrices: ProductPrices = { points: [], latest: [] };

function baseRoutes(overrides: { near?: string; observations?: unknown[]; prices?: ProductPrices } = {}) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /inbox": () => jsonResponse(200, { items: [], reading: { count: 0, oldest_at: null, stalled: false } }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": (call: RecordedCall) =>
      jsonResponse(200, {
        items: call.query.has("near")
          ? [
              { ...marketLocation, distance_m: overrides.near ?? "120.5" },
              { ...chainLocation, distance_m: "5400" },
            ]
          : [chainLocation, marketLocation],
      }),
    "GET /products/search": (call: RecordedCall) => {
      const q = call.query.get("q") ?? "";
      if (q === BARCODE) return jsonResponse(200, { items: [{ ...hits[0], match: "barcode", score: "1" }] });
      return jsonResponse(200, { items: q.includes("flour") ? hits : [] });
    },
    "GET /price-observations": () => jsonResponse(200, { items: overrides.observations ?? [], next_cursor: null }),
    [`GET /products/${flourProductId}/prices`]: () => jsonResponse(200, overrides.prices ?? emptyPrices),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
  };
}

function withPosition(latitude = 33.45, longitude = -120.55) {
  // A synthetic position inside the SECURITY.md test grid.
  Object.defineProperty(navigator, "geolocation", {
    configurable: true,
    value: {
      getCurrentPosition: (ok: (p: { coords: { latitude: number; longitude: number } }) => void) => ok({ coords: { latitude, longitude } }),
    },
  });
}

const entry = () => screen.getByRole("combobox", { name: "Barcode or product name" });

/** Narrower than lg (1024px), as a phone is. jsdom has no matchMedia, which reads as wide. */
function phoneWidth() {
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: (query: string) => ({ matches: false, media: query, addEventListener: () => {}, removeEventListener: () => {} }),
  });
}

afterEach(() => {
  localStorage.clear();
  delete (navigator as { geolocation?: unknown }).geolocation;
  delete (window as { matchMedia?: unknown }).matchMedia;
});

describe("shelf price: the store (G2)", () => {
  it("labels the remembered store as a guess, never as near, without a position", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    mockApi(baseRoutes());
    renderApp("/shop/shelf-prices");

    const chip = await screen.findByRole("button", { name: `Last used: ${CHAIN}. Change store` });
    expect(chip).toHaveTextContent(`Last used: ${CHAIN}·change`);
    expect(screen.queryByText(/Near /)).toBeNull();
  });

  it("says near only for a store within a kilometre of the position", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    withPosition();
    const calls = mockApi(baseRoutes());
    renderApp("/shop/shelf-prices");

    expect(await screen.findByRole("button", { name: "Near Pier Farmers Market. Change store" })).toBeInTheDocument();
    expect(calls.find((c) => c.query.has("near"))?.query.get("near")).toBe("33.45,-120.55");
  });

  it("falls back to the last-used store when the nearest is far away", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    withPosition();
    mockApi(baseRoutes({ near: "2400" }));
    renderApp("/shop/shelf-prices");

    expect(await screen.findByRole("button", { name: `Last used: ${CHAIN}. Change store` })).toBeInTheDocument();
  });

  it("offers Skip while it is finding the position", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    // A position that never arrives.
    Object.defineProperty(navigator, "geolocation", { configurable: true, value: { getCurrentPosition: () => {} } });
    mockApi(baseRoutes());
    const user = userEvent.setup();
    renderApp("/shop/shelf-prices");

    expect(await screen.findByText("Finding where you are…")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Skip" }));
    expect(await screen.findByRole("button", { name: `Last used: ${CHAIN}. Change store` })).toBeInTheDocument();
  });

  it("changes the store from the chip, and with no guess asks for one", async () => {
    mockApi(baseRoutes());
    const user = userEvent.setup();
    renderApp("/shop/shelf-prices");

    const select = await screen.findByLabelText("Store");
    await waitFor(() => expect(within(select).getByRole("option", { name: "Pier Farmers Market" })).toBeInTheDocument());
    await user.selectOptions(select, marketLocationId);
    const chip = screen.getByRole("button", { name: "Pier Farmers Market. Change store" });

    await user.click(chip);
    await user.selectOptions(screen.getByLabelText("Store"), chainLocationId);
    expect(screen.getByRole("button", { name: `${CHAIN}. Change store` })).toBeInTheDocument();
  });

  it("uses the store chosen before arriving, e.g. in Capture", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    mockApi(baseRoutes());
    renderApp({ pathname: "/shop/shelf-prices", state: { locationId: marketLocationId } });

    expect(await screen.findByRole("button", { name: "Pier Farmers Market. Change store" })).toBeInTheDocument();
  });
});

describe("shelf price: entry and saving (G3, G4)", () => {
  it("saves and readies the next tag: the store stays, the rest clears, a 3 s notice names the store", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    const calls = mockApi({ ...baseRoutes(), "POST /price-observations": () => jsonResponse(201, observationOk) });
    const user = userEvent.setup();
    renderApp("/shop/shelf-prices");
    await screen.findByRole("button", { name: `Last used: ${CHAIN}. Change store` });

    await user.type(entry(), "flour");
    await user.click(await screen.findByRole("option", { name: /All-Purpose Flour/ }));
    expect(screen.getByTestId("shelf-product-choice")).toHaveTextContent("Millstone All-Purpose Flour");
    const price = screen.getByLabelText("Price on the shelf");
    expect(price).toHaveFocus();
    expect(price).toHaveAttribute("inputmode", "decimal");

    await user.keyboard("4.99{Enter}");

    const notice = await within(mainRegion()).findByText(`Saved $4.99 at ${CHAIN}`);
    expect(notice).toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST" && c.path === "/price-observations");
    expect(post?.body).toEqual({ product_id: hits[0].id, vendor_location_id: chainLocationId, price: "4.99", qty: "1", unit: "each" });
    expect(post?.headers.get("Idempotency-Key")).toMatch(UUID);

    await waitFor(() => expect(entry()).toHaveFocus());
    expect(entry()).toHaveValue("");
    expect(screen.queryByLabelText("Price on the shelf")).toBeNull();
    expect(screen.getByRole("button", { name: `Last used: ${CHAIN}. Change store` })).toBeInTheDocument();
    expect(localStorage.getItem(LAST_LOCATION)).toBe(chainLocationId);
  });

  it("returns to where Capture was opened on plain Save, carrying the notice", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    mockApi({
      ...baseRoutes(),
      "POST /price-observations": () => jsonResponse(201, observationOk),
      "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    const user = userEvent.setup();
    renderApp({ pathname: "/shop/shelf-prices", state: { from: "/shop/purchases" } });
    await screen.findByRole("button", { name: `Last used: ${CHAIN}. Change store` });

    await user.type(entry(), "flour");
    await user.click(await screen.findByRole("option", { name: /All-Purpose Flour/ }));
    await user.type(screen.getByLabelText("Price on the shelf"), "4.99");
    await user.click(screen.getByRole("checkbox", { name: "On sale" }));
    await user.click(screen.getByRole("button", { name: "Save price" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Purchases" })).toBeInTheDocument();
    expect(within(mainRegion()).getByText(`Saved $4.99 at ${CHAIN}`)).toBeInTheDocument();
  });

  it("does not submit when Enter ends a barcode in the entry field, and takes the exact match when it lands", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    const calls = mockApi(baseRoutes());
    const user = userEvent.setup();
    renderApp("/shop/shelf-prices");
    await screen.findByRole("button", { name: `Last used: ${CHAIN}. Change store` });

    await user.type(entry(), `${BARCODE}{Enter}`);

    expect(await screen.findByTestId("shelf-product-choice")).toHaveTextContent("Millstone All-Purpose Flour");
    expect(screen.getByLabelText("Price on the shelf")).toHaveFocus();
    expect(screen.queryByText("Scan or type a product first.")).toBeNull();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("offers to create a product for an unknown barcode, with the barcode filled in (UI-4.7)", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    mockApi(baseRoutes());
    const user = userEvent.setup();
    renderApp("/shop/shelf-prices");
    await screen.findByRole("button", { name: `Last used: ${CHAIN}. Change store` });

    await user.type(entry(), "4006381333931");
    expect(await screen.findByText("No product has barcode 4006381333931.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Create product" }));

    const form = screen.getByRole("group", { name: "New product" });
    expect(within(form).getByLabelText("Barcode")).toHaveValue("4006381333931");
  });

  it("shows a failed lookup inline with Retry", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    let fail = true;
    mockApi({
      ...baseRoutes(),
      "GET /products/search": () => (fail ? jsonResponse(500, { error: { code: "internal", message: "boom" } }) : jsonResponse(200, { items: hits })),
    });
    const user = userEvent.setup();
    renderApp("/shop/shelf-prices");
    await screen.findByRole("button", { name: `Last used: ${CHAIN}. Change store` });

    await user.type(entry(), "flour");
    const alert = await within(mainRegion()).findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't look that up");
    fail = false;
    await user.click(within(alert).getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(within(mainRegion()).queryByRole("alert")).toBeNull());
  });

  it("lists the last five products logged at this store before typing", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    const products = ["A", "B", "C", "D", "E", "F"].map((letter, i) => ({
      ...observationOk,
      id: `0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f70${10 + i}`,
      product: { ...observationOk.product, id: `0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f60${10 + i}`, name: `Product ${letter}`, brand: null },
    }));
    // Product A was logged twice; it is listed once.
    const calls = mockApi(baseRoutes({ observations: [products[0], products[0], ...products.slice(1)] }));
    const user = userEvent.setup();
    renderApp("/shop/shelf-prices");

    const recent = await screen.findByRole("region", { name: `Recently logged at ${CHAIN}` });
    expect(within(recent).getAllByRole("button").map((b) => b.textContent)).toEqual([
      "Product A5 lb",
      "Product B5 lb",
      "Product C5 lb",
      "Product D5 lb",
      "Product E5 lb",
    ]);
    expect(calls.find((c) => c.path.startsWith("/price-observations"))?.query.get("vendor_location_id")).toBe(chainLocationId);

    await user.click(within(recent).getByRole("button", { name: /Product C/ }));
    expect(screen.getByTestId("shelf-product-choice")).toHaveTextContent("Product C");
  });

  it("shows last paid here and the best known price for the matched product", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    const point = (price: string, source: "receipt" | "shelf", at: string, locationId = chainLocationId) => ({
      observation_id: `obs-${price}`,
      observed_at: at,
      price,
      qty: "1",
      unit: "each",
      is_promo: false,
      source,
      norm_unit_price: "0.002",
      norm_unit: "g",
      norm_status: "ok" as const,
      location_id: locationId,
      location_name: "x",
      vendor_id: "v",
      vendor_name: "x",
      price_scope: "chain" as const,
      series: "s",
    });
    const latest = (price: string, norm: string | null, vendor: string) => ({
      location_id: "l",
      location_name: vendor,
      vendor_id: vendor,
      vendor_name: vendor,
      price_scope: "location" as const,
      observation_id: `latest-${price}`,
      observed_at: "2026-09-20T10:00:00Z",
      price,
      qty: "1",
      unit: "each",
      is_promo: false,
      norm_unit_price: norm,
      norm_unit: norm ? "g" : null,
      norm_status: norm ? ("ok" as const) : ("no_density" as const),
      age_days: "5",
      stale: false,
    });
    mockApi(
      baseRoutes({
        prices: {
          // Ascending by time. The later shelf price here was seen, not paid.
          points: [point("5.49", "receipt", "2026-09-10T10:00:00Z"), point("5.29", "receipt", "2026-09-18T10:00:00Z"), point("4.19", "shelf", "2026-09-22T10:00:00Z")],
          latest: [latest("5.29", "0.0023", "Millstone Market"), latest("3.99", "0.0018", "Pier Farmers Market"), latest("1.00", null, "Uncomparable")],
        },
      }),
    );
    const user = userEvent.setup();
    renderApp("/shop/shelf-prices");
    await screen.findByRole("button", { name: `Last used: ${CHAIN}. Change store` });
    await user.type(entry(), "flour");
    await user.click(await screen.findByRole("option", { name: /All-Purpose Flour/ }));

    const lastPaid = await screen.findByText("Last paid here");
    await waitFor(() => expect(lastPaid.nextElementSibling).toHaveTextContent(/^\$5\.29 · /));
    expect(screen.getByText("Best known").nextElementSibling).toHaveTextContent("$3.99 · Pier Farmers Market");
  });

  it("names the missing bridge after a save that could not be normalized", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    const calls = mockApi({ ...baseRoutes(), "POST /price-observations": () => jsonResponse(201, observationNoDensity) });
    const user = userEvent.setup();
    renderApp("/shop/shelf-prices");
    await screen.findByRole("button", { name: `Last used: ${CHAIN}. Change store` });

    await user.type(entry(), "flour");
    await user.click(await screen.findByRole("option", { name: /All-Purpose Flour/ }));
    await user.type(screen.getByLabelText("Price on the shelf"), "1.25");
    await user.click(screen.getByText("Amount and time"));
    await user.selectOptions(screen.getByLabelText("Unit"), "cup");
    await user.click(screen.getByRole("button", { name: "Save and scan another" }));

    expect(await screen.findByText(/has no density/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add a density" })).toHaveAttribute("href", `/catalog/ingredients/${flourId}#density-heading`);
    const post = calls.find((c) => c.method === "POST" && c.path === "/price-observations");
    expect(post?.body).toMatchObject({ price: "1.25", qty: "1", unit: "cup" });
  });

  it("hides the tab bar and header on this task screen, with its own Back", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    mockApi(baseRoutes());
    renderApp("/shop/shelf-prices");
    await screen.findByRole("button", { name: `Last used: ${CHAIN}. Change store` });

    expect(screen.queryByRole("navigation", { name: "Tabs" })).toBeNull();
    expect(screen.getByRole("button", { name: "Back" })).toBeInTheDocument();
  });

  it("starts from Capture: the sheet labels the store, a store changed there carries in, and Save returns (UI-2.10, UI-4.4)", async () => {
    phoneWidth();
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    mockApi({
      ...baseRoutes(),
      "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
      "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
      "POST /price-observations": () => jsonResponse(201, { ...observationOk, vendor_location: { id: marketLocationId, name: marketLocation.name, vendor: marketLocation.vendor } }),
    });
    const user = userEvent.setup();
    renderApp("/shop/purchases");
    await screen.findByText("No purchases yet");

    await user.click(within(screen.getByRole("navigation", { name: "Tabs" })).getByRole("button", { name: "Capture" }));
    const sheet = await screen.findByRole("dialog", { name: "Capture" });
    await user.click(await within(sheet).findByRole("button", { name: `Last used: ${CHAIN}. Change store` }));
    await user.selectOptions(within(sheet).getByLabelText("Store"), marketLocationId);
    await user.click(within(sheet).getByRole("link", { name: /Log a shelf price/ }));

    // Below lg it is the page, a task screen of its own.
    expect(await screen.findByRole("heading", { level: 1, name: "Shelf price" })).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "Pier Farmers Market. Change store" })).toBeInTheDocument();
    await user.type(entry(), "flour");
    await user.click(await screen.findByRole("option", { name: /All-Purpose Flour/ }));
    await user.type(screen.getByLabelText("Price on the shelf"), "4.99");
    await user.click(screen.getByRole("button", { name: "Save price" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Purchases" })).toBeInTheDocument();
    expect(within(mainRegion()).getByText("Saved $4.99 at Pier Farmers Market")).toBeInTheDocument();
    // Chosen in Capture, so it is the store next time too.
    expect(localStorage.getItem(LAST_LOCATION)).toBe(marketLocationId);
  });
});

describe("shelf price: review findings", () => {
  it("never offers the previous lookup's results while a new one is pending", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    const pending: (() => void)[] = [];
    const release = () => pending.splice(0).forEach((go) => go());
    mockApi({
      ...baseRoutes(),
      "GET /products/search": (call: RecordedCall) => {
        const q = call.query.get("q") ?? "";
        if (q === "flour") return jsonResponse(200, { items: hits });
        // The second lookup hangs until the test lets it go.
        return new Promise((resolve) => pending.push(() => resolve(jsonResponse(200, { items: [] }))));
      },
    });
    const user = userEvent.setup();
    renderApp("/shop/shelf-prices");
    await screen.findByRole("button", { name: `Last used: ${CHAIN}. Change store` });

    await user.type(entry(), "flour");
    expect(await screen.findByRole("option", { name: /All-Purpose Flour/ })).toBeInTheDocument();
    await user.clear(entry());
    await user.type(entry(), "oats");
    await waitFor(() => expect(screen.getByText("Looking up…")).toBeInTheDocument());
    expect(screen.queryByRole("option")).toBeNull();
    // Let every lookup answer, including one the debounce has only now sent.
    await waitFor(() => {
      release();
      expect(screen.getByText("Nothing called “oats” yet.")).toBeInTheDocument();
    });
  });

  it("carries the missing bridge and its link to where plain Save returns", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    mockApi({
      ...baseRoutes(),
      "POST /price-observations": () => jsonResponse(201, observationNoDensity),
      "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    const user = userEvent.setup();
    renderApp({ pathname: "/shop/shelf-prices", state: { from: "/shop/purchases" } });
    await screen.findByRole("button", { name: `Last used: ${CHAIN}. Change store` });

    await user.type(entry(), "flour");
    await user.click(await screen.findByRole("option", { name: /All-Purpose Flour/ }));
    await user.type(screen.getByLabelText("Price on the shelf"), "1.25");
    await user.click(screen.getByRole("button", { name: "Save price" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Purchases" })).toBeInTheDocument();
    expect(within(mainRegion()).getByText(/It can't be compared yet: all-purpose flour has no density\./)).toBeInTheDocument();
    expect(within(mainRegion()).getByRole("link", { name: "Add a density" })).toHaveAttribute("href", `/catalog/ingredients/${flourId}#density-heading`);
  });

  it("offers to create a product for an unknown barcode even when names match it", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    mockApi({ ...baseRoutes(), "GET /products/search": () => jsonResponse(200, { items: [{ ...hits[1], match: "name" }] }) });
    const user = userEvent.setup();
    renderApp("/shop/shelf-prices");
    await screen.findByRole("button", { name: `Last used: ${CHAIN}. Change store` });

    await user.type(entry(), "40063813");
    expect(await screen.findByText("No product has barcode 40063813.")).toBeInTheDocument();
    // The name match can still be chosen.
    expect(screen.getByRole("option", { name: /Bread Flour/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Create product" })).toBeInTheDocument();
  });

  it("takes an exact barcode match of any shape", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    mockApi({ ...baseRoutes(), "GET /products/search": () => jsonResponse(200, { items: [{ ...hits[0], barcode: "ABC-123", match: "barcode" }] }) });
    const user = userEvent.setup();
    renderApp("/shop/shelf-prices");
    await screen.findByRole("button", { name: `Last used: ${CHAIN}. Change store` });

    await user.type(entry(), "ABC-123");
    expect(await screen.findByTestId("shelf-product-choice")).toHaveTextContent("Millstone All-Purpose Flour");
  });

  it("reads further back for recents when one product fills the first page", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    const other = { ...observationOk, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7099", product: { ...observationOk.product, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f6099", name: "Older Product", brand: null } };
    mockApi({
      ...baseRoutes(),
      "GET /price-observations": (call: RecordedCall) =>
        call.query.get("cursor")
          ? jsonResponse(200, { items: [other], next_cursor: null })
          : jsonResponse(200, { items: Array.from({ length: 50 }, () => observationOk), next_cursor: "page-2" }),
    });
    renderApp("/shop/shelf-prices");

    const recent = await screen.findByRole("region", { name: `Recently logged at ${CHAIN}` });
    await waitFor(() => expect(within(recent).getAllByRole("button")).toHaveLength(2));
    expect(within(recent).getByRole("button", { name: /Older Product/ })).toBeInTheDocument();
  });

  it("leaves stale offers out of Best known", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    const latest = (price: string, vendor: string, stale: boolean) => ({
      location_id: vendor,
      location_name: vendor,
      vendor_id: vendor,
      vendor_name: vendor,
      price_scope: "location" as const,
      observation_id: `latest-${price}`,
      observed_at: "2026-09-20T10:00:00Z",
      price,
      qty: "1",
      unit: "each",
      is_promo: false,
      norm_unit_price: price === "1.99" ? "0.0010" : "0.0020",
      norm_unit: "g",
      norm_status: "ok" as const,
      age_days: stale ? "400" : "3",
      stale,
    });
    mockApi(baseRoutes({ prices: { points: [], latest: [latest("1.99", "Old Cheap Place", true), latest("4.19", "Current Place", false)] } }));
    const user = userEvent.setup();
    renderApp("/shop/shelf-prices");
    await screen.findByRole("button", { name: `Last used: ${CHAIN}. Change store` });
    await user.type(entry(), "flour");
    await user.click(await screen.findByRole("option", { name: /All-Purpose Flour/ }));

    const best = await screen.findByText("Best known");
    await waitFor(() => expect(best.nextElementSibling).toHaveTextContent("$4.19 · Current Place"));
  });
});

describe("shelf price in the desktop drawer (G14)", () => {
  async function openDrawer(user: ReturnType<typeof userEvent.setup>) {
    renderApp("/shop/purchases");
    await screen.findByText("No purchases yet");
    await user.click(within(screen.getAllByRole("navigation", { name: "Main" })[0].closest("aside")!).getByRole("button", { name: "Capture" }));
    await user.click(within(await screen.findByRole("dialog", { name: "Capture" })).getByRole("link", { name: /Log a shelf price/ }));
    return screen.findByRole("dialog", { name: "Log a shelf price" });
  }

  function drawerRoutes() {
    return {
      ...baseRoutes(),
      "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
      "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
      "POST /price-observations": () => jsonResponse(201, observationOk),
    };
  }

  it("opens over the page Capture was opened on, and plain Save closes it with the notice there", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    mockApi(drawerRoutes());
    const user = userEvent.setup();
    const drawer = await openDrawer(user);

    // Still on Purchases underneath.
    expect(screen.getByRole("heading", { level: 1, name: "Purchases" })).toBeInTheDocument();
    await user.type(within(drawer).getByRole("combobox", { name: "Barcode or product name" }), "flour");
    await user.click(await within(drawer).findByRole("option", { name: /All-Purpose Flour/ }));
    await user.type(within(drawer).getByLabelText("Price on the shelf"), "4.99");
    await user.click(within(drawer).getByRole("button", { name: "Save price" }));

    await waitFor(() => expect(screen.queryByRole("dialog", { name: "Log a shelf price" })).toBeNull());
    expect(within(mainRegion()).getByText(`Saved $4.99 at ${CHAIN}`)).toBeInTheDocument();
  });

  it("stays open for the next tag on Save and scan another, and on Enter", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    const calls = mockApi(drawerRoutes());
    const user = userEvent.setup();
    const drawer = await openDrawer(user);

    await user.type(within(drawer).getByRole("combobox", { name: "Barcode or product name" }), "flour");
    await user.click(await within(drawer).findByRole("option", { name: /All-Purpose Flour/ }));
    await user.type(within(drawer).getByLabelText("Price on the shelf"), "4.99");
    await user.click(within(drawer).getByRole("button", { name: "Save and scan another" }));
    await waitFor(() => expect(within(drawer).getByRole("combobox", { name: "Barcode or product name" })).toHaveFocus());
    expect(screen.getByRole("dialog", { name: "Log a shelf price" })).toBeInTheDocument();

    await user.type(within(drawer).getByRole("combobox", { name: "Barcode or product name" }), "flour");
    await user.click(await within(drawer).findByRole("option", { name: /All-Purpose Flour/ }));
    await user.type(within(drawer).getByLabelText("Price on the shelf"), "3.99{Enter}");
    await waitFor(() => expect(calls.filter((c) => c.method === "POST")).toHaveLength(2));
    expect(screen.getByRole("dialog", { name: "Log a shelf price" })).toBeInTheDocument();
  });

  it("asks before discarding a typed price", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    mockApi(drawerRoutes());
    const user = userEvent.setup();
    const drawer = await openDrawer(user);

    await user.type(within(drawer).getByRole("combobox", { name: "Barcode or product name" }), "flour");
    await user.click(await within(drawer).findByRole("option", { name: /All-Purpose Flour/ }));
    await user.keyboard("{Escape}");
    expect(within(drawer).getByRole("alert")).toHaveTextContent("Discard this shelf price?");
  });

  it("asks before discarding search text that was typed but not chosen", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    mockApi(drawerRoutes());
    const user = userEvent.setup();
    const drawer = await openDrawer(user);

    await user.type(within(drawer).getByRole("combobox", { name: "Barcode or product name" }), "oat milk");
    await user.click(within(drawer).getByRole("button", { name: "Cancel" }));
    expect(within(drawer).getByRole("alert")).toHaveTextContent("Discard this shelf price?");
  });

  it("stays open through a navigation and ⌘K, so only its own guard can discard", async () => {
    localStorage.setItem(LAST_LOCATION, chainLocationId);
    mockApi({ ...drawerRoutes(), "GET /inbox": () => jsonResponse(200, { items: [], reading: { count: 0, oldest_at: null, stalled: false } }) });
    const user = userEvent.setup();
    const drawer = await openDrawer(user);
    await user.type(within(drawer).getByRole("combobox", { name: "Barcode or product name" }), "oat milk");

    await user.keyboard("{Control>}k{/Control}");
    expect(screen.queryByRole("dialog", { name: "Search" })).toBeNull();
    // A route change underneath, as Back would make.
    await user.click(within(screen.getAllByRole("navigation", { name: "Main" })[0]).getByRole("link", { name: /Home/ }));
    expect(await screen.findByRole("heading", { level: 1 })).not.toHaveTextContent("Purchases");
    expect(screen.getByRole("dialog", { name: "Log a shelf price" })).toBeInTheDocument();
    expect(within(screen.getByRole("dialog", { name: "Log a shelf price" })).getByRole("combobox", { name: "Barcode or product name" })).toHaveValue("oat milk");
  });

  it("offers only Close when there are no locations to price at", async () => {
    mockApi({ ...drawerRoutes(), "GET /vendor-locations": () => jsonResponse(200, { items: [] }) });
    const user = userEvent.setup();
    const drawer = await openDrawer(user);

    expect(await within(drawer).findByRole("button", { name: "Close" })).toBeInTheDocument();
    expect(within(drawer).queryByRole("button", { name: "Save price" })).toBeNull();
    expect(within(drawer).queryByRole("button", { name: "Save and scan another" })).toBeNull();
  });
});
