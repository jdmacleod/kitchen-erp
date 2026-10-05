import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Proposal, ProposalField } from "../api/proposals";
import { basisText } from "../pages/catalog/ProductReviewPage";
import { flourProduct, flourProductId, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

// Price-basis plan, PB3: a posted price says what it is for, and the reviewer can
// settle or change it. The store and its products are invented.

const proposalId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c01";
const locationId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c21";
const PER_LB = { amount: "0.69", qty: "1", unit: "lb" };

function priceField(conflict: boolean): ProposalField {
  return {
    value: PER_LB,
    source: "adapter",
    confidence: null,
    alternatives: conflict ? [{ value: "3.49", source: "page_data", confidence: null }] : [],
    conflict,
  } as ProposalField;
}

function proposal(conflict = false): Proposal {
  return {
    id: proposalId,
    kind: "new_product",
    status: "pending",
    product_id: null,
    capture: { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c10", channel: "clip", source_url: "https://shop.example.test/p/bananas", captured_at: "2026-10-02T10:00:00Z" },
    fields: {
      title: { value: "Bananas", source: "page_data", confidence: null, alternatives: [], conflict: false },
      price: priceField(conflict),
    },
    match: { strong: { product_id: flourProductId, reason: "identifier" }, candidates: [], preselect: `update:${flourProductId}` },
    listing: { vendor_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c20", canonical_url: "https://shop.example.test/p/bananas" },
    vendor: { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c20", name: "Juniper Market", price_scope: "chain", locations: [{ id: locationId, name: "Juniper Market" }], suggested_location_id: locationId },
    price: { ...PER_LB, is_promo: false },
    photos: [],
    jobs: [],
    decided_at: null,
    result: null,
    created_at: "2026-10-02T10:00:00Z",
  } as Proposal;
}

function routes(p: Proposal) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /inbox": () => jsonResponse(200, { items: [], reading: { count: 0, photos: 0, pages: 0, oldest_at: null, stalled: false } }),
    [`GET /product-proposals/${p.id}`]: () => jsonResponse(200, p),
    [`GET /products/${flourProductId}`]: () => jsonResponse(200, flourProduct),
    [`GET /products/${flourProductId}/photos`]: () => jsonResponse(200, { items: [], primary_image_id: null }),
    [`GET /products/${flourProductId}/prices`]: () => jsonResponse(200, { points: [], latest: [] }),
    "GET /price-observations": () => jsonResponse(200, { items: [], next_cursor: null }),
    "GET /product-proposals": () => jsonResponse(200, { items: [], counts: {} }),
    [`POST /product-proposals/${p.id}/accept`]: () => jsonResponse(200, { ...p, status: "accepted", result: { product_id: flourProductId } }),
  };
}

const accepted = (calls: RecordedCall[]) => calls.find((c) => c.method === "POST" && c.path.endsWith("/accept"))?.body as Record<string, unknown> | undefined;

afterEach(() => vi.unstubAllGlobals());

describe("posted price basis", () => {
  it("writes what a price is for", () => {
    expect(basisText("1", "each")).toBe("");
    expect(basisText("1", "lb")).toBe(" / lb");
    expect(basisText("100.000", "g")).toBe(" / 100 g");
    expect(basisText("1", "fl_oz")).toBe(" / fl oz");
  });

  it("shows a per-pound price and records it as the page gave it", async () => {
    const calls = mockApi(routes(proposal()));
    const user = userEvent.setup();
    renderApp(`/catalog/products/review/${proposalId}`);
    expect(await screen.findByText(/Posted at \$0\.69 \/ lb\./)).toBeInTheDocument();
    expect(screen.queryByLabelText("For")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(accepted(calls)).toBeDefined());
    expect(accepted(calls)!.record_price).toBe(true);
    expect(accepted(calls)!.price).toBeUndefined();
  });

  it("asks the reviewer to settle a page that prices per pound and each", async () => {
    const calls = mockApi(routes(proposal(true)));
    const user = userEvent.setup();
    renderApp(`/catalog/products/review/${proposalId}`);
    expect(await screen.findByText(/The page gives more than one price: \$0\.69 \/ lb and \$3\.49 each\./)).toBeInTheDocument();
    // The editor is open, starting from the page's chosen price.
    expect(screen.getByLabelText("Posted price")).toHaveValue("0.69");
    expect(screen.getByLabelText("For")).toHaveValue("1");
    await user.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(accepted(calls)).toBeDefined());
    expect(accepted(calls)!.price).toEqual(PER_LB);
  });

  it("lets the reviewer change what the price is for, and blocks an unreadable one", async () => {
    const calls = mockApi(routes(proposal()));
    const user = userEvent.setup();
    renderApp(`/catalog/products/review/${proposalId}`);
    await user.click(await screen.findByRole("button", { name: "Change the price or what it is for" }));
    const amount = screen.getByLabelText("Posted price");
    await user.clear(amount);
    await user.type(amount, "abc");
    expect(screen.getByRole("button", { name: "Accept" })).toBeDisabled();
    expect(screen.getByText("Give the posted price an amount and what it is for")).toBeInTheDocument();
    await user.clear(amount);
    await user.type(amount, "1.52");
    await user.selectOptions(screen.getByLabelText("Unit"), "kg");
    await user.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(accepted(calls)).toBeDefined());
    expect(accepted(calls)!.price).toEqual({ amount: "1.52", qty: "1", unit: "kg" });
  });
});

describe("field choices", () => {
  it("shows the same value from two sources once", async () => {
    const p = proposal();
    p.fields.title = {
      value: "Bananas",
      source: "page_data",
      confidence: null,
      alternatives: [
        { value: "Bananas", source: "page_meta", confidence: null },
        { value: "Organic bananas", source: "model", confidence: "0.4" },
      ],
      conflict: false,
    } as ProposalField;
    mockApi(routes(p));
    const user = userEvent.setup();
    renderApp(`/catalog/products/review/${proposalId}`);
    await user.click(await screen.findByRole("button", { name: "Edit details" }));
    const name = screen.getByRole("group", { name: "Name" });
    // Besides keeping the product's own name, the two different values.
    expect(within(name).getAllByRole("radio", { name: /^(?!Keep)/ })).toHaveLength(2);
  });
});
