import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { PriceChange, Proposal } from "../api/proposals";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

// 2N in the screens (UI-6.12, UI-6.13): "Look this up online" and its states,
// the "Lookup helper" badge, the overdue-lookup line, and posted prices.

const proposalId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9d01";
const minutesAgo = (n: number) => new Date(Date.now() - n * 60_000).toISOString();

function proposal(extra: Partial<Proposal> = {}): Proposal {
  return {
    id: proposalId,
    kind: "new_product",
    status: "pending",
    product_id: null,
    capture: { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9d10", channel: "barcode", source_url: null, captured_at: minutesAgo(30) },
    fields: {
      gtin: { value: "05012345000022", source: "scan", confidence: null, via: null, alternatives: [], conflict: false },
      title: {
        value: "Strong White Flour",
        source: "manufacturer",
        confidence: null,
        via: "helper",
        alternatives: [{ value: "Flour", source: "model", confidence: "0.4", via: null }],
        conflict: false,
      },
    },
    match: { strong: null, candidates: [], preselect: null },
    listing: null,
    vendor: null,
    price: null,
    photos: [],
    jobs: [],
    decided_at: null,
    result: null,
    created_at: minutesAgo(30),
    ...extra,
  };
}

function routes(p: Proposal, configured: boolean, extra: Record<string, (c: RecordedCall) => Response> = {}) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /inbox": () => jsonResponse(200, { items: [], reading: { count: 0, oldest_at: null, stalled: false } }),
    "GET /units": () => jsonResponse(200, { items: [] }),
    [`GET /product-proposals/${proposalId}`]: () => jsonResponse(200, p),
    "GET /product-proposals": () => jsonResponse(200, { items: [], counts: {} }),
    "GET /products-helper": () => jsonResponse(200, { configured }),
    ...extra,
  };
}

describe("Look this up online", () => {
  it("is absent without a products helper", async () => {
    mockApi(routes(proposal(), false));
    renderApp(`/catalog/products/review/${proposalId}`);
    await screen.findByRole("heading", { level: 1 });
    await waitFor(() => expect(screen.queryByRole("button", { name: "Look this up online" })).not.toBeInTheDocument());
  });

  it("asks the helper, then says when it asked, and badges what it found", async () => {
    let p = proposal();
    const calls = mockApi({
      ...routes(p, true),
      [`GET /product-proposals/${proposalId}`]: () => jsonResponse(200, p),
      [`POST /product-proposals/${proposalId}/look-up`]: () => {
        p = proposal({ lookup: { status: "open", created_at: minutesAgo(2), answered_at: null } });
        return jsonResponse(200, { id: "x", kind: "gtin", value: "05012345000022", status: "open", created_at: minutesAgo(2), answered_at: null });
      },
    });
    const user = userEvent.setup();
    renderApp(`/catalog/products/review/${proposalId}`);
    await user.click(await screen.findByRole("button", { name: "Look this up online" }));
    expect(await screen.findByText("Asked the lookup helper 2 min ago.")).toBeInTheDocument();
    expect(calls.filter((c) => c.path.endsWith("/look-up"))).toHaveLength(1);
    // The helper's value is badged as its own; the model's guess as a guess.
    await user.click(screen.getByRole("radio", { name: "Create new product" }));
    const name = screen.getByRole("group", { name: "Name" });
    expect(within(name).getByText("Lookup helper")).toBeInTheDocument();
    expect(within(name).getByText("Model's guess")).toBeInTheDocument();
  });

  it("turns squash when no answer has come", async () => {
    mockApi(routes(proposal({ lookup: { status: "open", created_at: minutesAgo(45), answered_at: null } }), true));
    renderApp(`/catalog/products/review/${proposalId}`);
    expect(await screen.findByText(/No answer yet/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Check System" })).toHaveAttribute("href", "/settings/system");
  });
});

describe("Home", () => {
  it("gives overdue lookups their own line, and shows the helper's inbox rows", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
      "GET /inbox": () =>
        jsonResponse(200, {
          items: [
            { kind: "product_update", title: "2 product updates to review", detail: "x", action_label: "Review", action_route: `/catalog/products/review/${proposalId}`, created_at: minutesAgo(5) },
            { kind: "posted_prices", title: "4 posted prices changed", detail: "x", action_label: "Review", action_route: "/catalog/products/posted-prices", created_at: minutesAgo(4) },
          ],
          reading: { count: 0, photos: 0, pages: 0, oldest_at: null, stalled: false, lookups_overdue: 3, lookups_since: minutesAgo(90) },
        }),
    });
    renderApp("/");
    expect(await screen.findByText(/3 lookups waiting for the lookup helper since/)).toBeInTheDocument();
    expect(screen.getByText("2 product updates to review")).toBeInTheDocument();
    expect(screen.getByText("4 posted prices changed")).toBeInTheDocument();
  });
});

describe("posted prices", () => {
  const change: PriceChange = {
    id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9d20",
    amount: "3.1900",
    qty: "1",
    unit: "each",
    is_promo: false,
    seen_at: minutesAgo(60),
    listing_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9d21",
    title: "Oats",
    canonical_url: "https://shop.example.test/p/oats",
    product_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9d22",
    product_name: "Rolled oats tin",
    vendor_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9d23",
    vendor_name: "Juniper Market",
    created_at: minutesAgo(30),
  };

  it("records a posted price only when a person accepts it, asking the store when needed", async () => {
    let attempts = 0;
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /inbox": () => jsonResponse(200, { items: [], reading: { count: 0, oldest_at: null, stalled: false } }),
      "GET /listing-price-changes": () => jsonResponse(200, { items: attempts < 2 ? [change] : [] }),
      "GET /vendor-locations": () => jsonResponse(200, { items: [{ id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9d24", name: "Juniper Market Harbour", vendor: { id: change.vendor_id, name: "Juniper Market", kind: "chain", price_scope: "chain" } }], next_cursor: null }),
      [`POST /listing-price-changes/${change.id}/accept`]: () => {
        attempts += 1;
        return attempts === 1
          ? errorResponse(422, "location_required", "Say which of this vendor's stores the price is for.")
          : jsonResponse(200, { id: change.id, status: "accepted", observation_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9d25" });
      },
    });
    const user = userEvent.setup();
    renderApp("/catalog/products/posted-prices");
    const list = await screen.findByRole("list", { name: "Posted prices" });
    expect(within(list).getByText("Rolled oats tin")).toBeInTheDocument();
    expect(within(list).getByText(/\$3\.19/)).toBeInTheDocument();
    await user.click(within(list).getByRole("button", { name: "Accept" }));
    const store = await screen.findByLabelText("At which Juniper Market store?");
    await user.selectOptions(store, "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9d24");
    await user.click(within(list).getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith("/accept"))).toHaveLength(2));
    expect(calls.filter((c) => c.path.endsWith("/accept"))[1].body).toEqual({ vendor_location_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9d24" });
    expect(await screen.findByText("No posted prices waiting")).toBeInTheDocument();
  });
});
