import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { flourId, flourProductId } from "./catalog-fixtures";
import { chainLocation } from "./geo-fixtures";
import { manualPurchase } from "./purchase-fixtures";
import { adminUser, errorResponse, jsonResponse, mainRegion, mockApi, renderApp, type RecordedCall, type RouteHandler } from "./helpers";

/**
 * The landing route. Issue #15 reported that a new deployment lands on a form for
 * a thing there is no reason to add yet, and that the first thing a new owner
 * actually tries is a dead end.
 *
 * Two things these tests exist to hold down, beyond "the page renders":
 *
 *   - Only a *committed* purchase completes setup. Receipt parsing inserts a
 *     draft the moment a photo is read, so an unfiltered probe would mark a
 *     household as finished on the strength of an upload nobody reviewed.
 *   - Loading and errored are not empty. Neither may render the checklist, or an
 *     established household is told to go and add a shop it already has.
 */

const NEVER = () => new Promise<Response>(() => {}) as unknown as Response;

const CHECKLIST_STEP = "Add somewhere you shop";

interface Routes {
  locations?: RouteHandler;
  purchases?: RouteHandler;
  toIdentify?: RouteHandler;
  needsBridge?: RouteHandler;
}

function mount({ locations, purchases, toIdentify, needsBridge }: Routes = {}): RecordedCall[] {
  const calls = mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /vendor-locations": locations ?? (() => jsonResponse(200, { items: [] })),
    "GET /purchases": purchases ?? (() => jsonResponse(200, { items: [], next_cursor: null })),
    "GET /to-identify": toIdentify ?? (() => jsonResponse(200, { items: [] })),
    "GET /price-book/needs-bridge": needsBridge ?? (() => jsonResponse(200, { items: [] })),
  });
  renderApp("/");
  return calls;
}

describe("home page, first run", () => {
  it("shows both steps, neither done, on an empty deployment", async () => {
    mount();

    expect(await screen.findByRole("heading", { name: "Set up your kitchen" })).toBeInTheDocument();
    const steps = screen.getAllByRole("listitem").filter((li) => li.textContent?.includes("To do"));
    expect(steps).toHaveLength(2);
    expect(screen.getByText(CHECKLIST_STEP)).toBeInTheDocument();
    expect(screen.getByText("Record your first purchase")).toBeInTheDocument();
  });

  it("deep-links step one into placing mode", async () => {
    // MapView ignores clicks unless it is placing, so a bare /map would be the
    // same dead end one screen later.
    mount();

    expect(await screen.findByRole("link", { name: "Open the map" })).toHaveAttribute(
      "href",
      "/map?place=location",
    );
  });

  it("ticks step one once a location exists, and leaves step two open", async () => {
    mount({ locations: () => jsonResponse(200, { items: [chainLocation] }) });

    await screen.findByRole("heading", { name: "Set up your kitchen" });
    // Completion is not carried by colour: the word is in the accessible name.
    const done = screen.getAllByRole("listitem").filter((li) => li.textContent?.includes("Done"));
    expect(done).toHaveLength(1);
    expect(done[0].textContent).toContain(CHECKLIST_STEP);

    const open = screen.getAllByRole("listitem").filter((li) => li.textContent?.includes("To do"));
    expect(open).toHaveLength(1);
    expect(open[0].textContent).toContain("Record your first purchase");
  });

  it("asks only for committed purchases, so an unreviewed receipt does not finish setup", async () => {
    // The defect this replaced: GET /purchases with no status filter returns the
    // draft that receipt parsing inserts, and the checklist would vanish.
    const calls = mount();

    await screen.findByRole("heading", { name: "Set up your kitchen" });
    const purchaseCalls = calls.filter((c) => c.path.startsWith("/purchases"));
    expect(purchaseCalls).toHaveLength(1);
    expect(purchaseCalls[0].query.get("status")).toBe("committed");
  });
});

describe("home page, set up", () => {
  it("shows the home page and no checklist once a purchase exists", async () => {
    mount({ purchases: () => jsonResponse(200, { items: [manualPurchase], next_cursor: null }) });

    const main = within(await screen.findByRole("main"));
    expect(await main.findByRole("heading", { name: "Home" })).toBeInTheDocument();
    expect(screen.queryByText(CHECKLIST_STEP)).not.toBeInTheDocument();
    // Scoped: the sidebar carries its own "New purchase" link.
    expect(main.getByRole("link", { name: "New purchase" })).toHaveAttribute("href", "/purchases/new");
  });

  it("lists recent purchases under Lately", async () => {
    mount({ purchases: () => jsonResponse(200, { items: [manualPurchase], next_cursor: null }) });

    const lately = await screen.findByLabelText("Lately");
    expect(lately).toHaveTextContent("Pier Farmers Market");
    expect(lately).toHaveTextContent("14.21");
  });

  it("hides Needs you when both queues are empty", async () => {
    mount({ purchases: () => jsonResponse(200, { items: [manualPurchase], next_cursor: null }) });

    await screen.findByLabelText("Lately");
    // Scoped: the sidebar health line is itself a role="status" live region.
    await waitFor(() => expect(within(mainRegion()).queryByRole("status")).not.toBeInTheDocument());
    expect(screen.queryByLabelText("Needs you")).not.toBeInTheDocument();
  });

  it("counts receipt lines rather than groups", async () => {
    mount({
      purchases: () => jsonResponse(200, { items: [manualPurchase], next_cursor: null }),
      toIdentify: () =>
        jsonResponse(200, {
          items: [
            { vendor: { id: "v1", name: "Millstone Market" }, raw_text_norm: "oats", line_count: 2, lines: [] },
            { vendor: { id: "v1", name: "Millstone Market" }, raw_text_norm: "rye", line_count: 1, lines: [] },
          ],
        }),
    });

    expect(await screen.findByText("3 receipt lines to identify")).toBeInTheDocument();
  });

  it("keeps Needs you on screen when a queue fails, and names it", async () => {
    // A hidden section and a broken one must not look alike: dropping the section
    // silently would leave receipt lines unreviewed behind a page that reads calm.
    mount({
      purchases: () => jsonResponse(200, { items: [manualPurchase], next_cursor: null }),
      toIdentify: () => errorResponse(500, "unavailable", "down"),
    });

    const section = await screen.findByLabelText("Needs you");
    await waitFor(() => expect(section).toHaveTextContent("Could not load the to-identify queue"));
  });

  it("raises one alert, not one per queue, when both fail", async () => {
    mount({
      purchases: () => jsonResponse(200, { items: [manualPurchase], next_cursor: null }),
      toIdentify: () => errorResponse(500, "unavailable", "down"),
      needsBridge: () => errorResponse(500, "unavailable", "down"),
    });

    await screen.findByLabelText("Needs you");
    // Alert tone="error" is role="alert"; two of them would be two assertive live
    // regions talking over each other.
    await waitFor(() => expect(screen.getAllByRole("alert")).toHaveLength(1));
    expect(screen.getByRole("alert")).toHaveTextContent(
      "Could not load the to-identify queue or the needs-a-bridge queue",
    );
  });
});

describe("home page, review findings", () => {
  it("counts distinct products needing a bridge, not response rows", async () => {
    // `needs_bridge` groups by norm_status as well as product, so one product
    // failing two ways comes back as two rows. Counting rows would tell the
    // household there is twice as much waiting for them as there is.
    const oneProductTwoStatuses = [
      { ingredient: { id: flourId, name: "flour", canonical_unit: "g" }, product: { id: flourProductId, name: "Flour", brand: null, pack_qty: null, pack_unit: null }, status: "no_density", observation_count: 2, latest_observed_at: "2026-09-19T15:00:00Z" },
      { ingredient: { id: flourId, name: "flour", canonical_unit: "g" }, product: { id: flourProductId, name: "Flour", brand: null, pack_qty: null, pack_unit: null }, status: "no_pack", observation_count: 1, latest_observed_at: "2026-09-18T15:00:00Z" },
    ];
    mount({
      purchases: () => jsonResponse(200, { items: [manualPurchase], next_cursor: null }),
      needsBridge: () => jsonResponse(200, { items: oneProductTwoStatuses }),
    });

    expect(await screen.findByText("1 product needs a unit bridge")).toBeInTheDocument();
  });

  it("shows a failed lookup rather than waiting on the other one", async () => {
    // A known error hidden behind "Loading…" leaves someone watching a spinner
    // that can only ever resolve into that error.
    mount({ locations: () => errorResponse(500, "unavailable", "down"), purchases: NEVER });

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText(CHECKLIST_STEP)).not.toBeInTheDocument();
  });
});

describe("home page, neither loading nor errored is empty", () => {
  it("shows no checklist while the answers are still coming", async () => {
    mount({ purchases: NEVER });

    const main = await screen.findByRole("main");
    expect(await within(main).findByRole("status")).toHaveTextContent("Loading…");
    expect(screen.queryByText(CHECKLIST_STEP)).not.toBeInTheDocument();
  });

  it("never claims a household is new when the purchase lookup fails", async () => {
    mount({ purchases: () => errorResponse(500, "unavailable", "down") });

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText(CHECKLIST_STEP)).not.toBeInTheDocument();
    expect(mainRegion()).not.toHaveTextContent("Set up your kitchen");
  });

  it("never claims a household is new when the location lookup fails", async () => {
    mount({ locations: () => errorResponse(500, "unavailable", "down") });

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText(CHECKLIST_STEP)).not.toBeInTheDocument();
  });
});
