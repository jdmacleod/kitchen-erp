import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { photographProduct, type Proposal } from "../api/proposals";
import { readingSentence } from "../api/inbox";
import { flourProduct, flourProductId, units } from "./catalog-fixtures";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

// 2L, slice S5: the product review page (10; UI-6.2–6.6), the inbox row and
// reading line (UI-6.1, UI-6.12) and Photograph a product (UI-6.11).

const proposalId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9b01";
const nextId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9b02";
const otherProductId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9b03";
const code = (digits: string) => digits.replace(/ /g, "");
const GTIN = code("0 0501 2345 0000 22");
const OTHER_GTIN = code("0 0501 2345 0000 39");

function proposal(extra: Partial<Proposal> = {}): Proposal {
  return {
    id: proposalId,
    kind: "new_product",
    status: "pending",
    product_id: null,
    capture: { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9b10", channel: "clip", source_url: "https://shop.example.test/p/flour", captured_at: "2026-09-30T10:00:00Z" },
    fields: {
      title: { value: "Strong white flour", source: "page_data", confidence: null, alternatives: [{ value: "Flour | Shop", source: "page_meta", confidence: null }], conflict: false },
      brand: { value: "Larkfield", source: "page_data", confidence: null, alternatives: [], conflict: false },
      pack: { value: { qty: "1.5", unit: "kg" }, source: "page_data", confidence: null, alternatives: [], conflict: false },
      gtin: { value: GTIN, source: "page_data", confidence: null, alternatives: [], conflict: false },
    },
    match: { strong: null, candidates: [], preselect: null },
    listing: { vendor_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9b20", canonical_url: "https://shop.example.test/p/flour" },
    vendor: { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9b20", name: "Juniper Market", price_scope: "chain", locations: [{ id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9b21", name: "Juniper Market" }], suggested_location_id: null },
    price: { amount: "2.10", qty: "1", unit: "each", is_promo: false },
    photos: [],
    jobs: [{ id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9b30", kind: "extract", status: "done", last_error: null }],
    decided_at: null,
    result: null,
    created_at: "2026-09-30T10:00:00Z",
    ...extra,
  };
}

const strong = (): Proposal =>
  proposal({ match: { strong: { product_id: flourProductId, reason: "identifier" }, candidates: [], preselect: `update:${flourProductId}` } });

function routes(p: Proposal, extra: Record<string, (c: RecordedCall) => Response> = {}, pending: string[] = [proposalId]) {
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
    "GET /product-proposals": () =>
      jsonResponse(200, {
        items: pending.map((id) => ({ id, kind: "new_product", status: "pending", title: "Flour", brand: null, channel: "clip", has_conflict: false, created_at: "2026-09-30T10:00:00Z" })),
        counts: { new_product: pending.length },
      }),
    ...extra,
  };
}

afterEach(() => vi.unstubAllGlobals());

describe("product review", () => {
  it("collapses a strong match to its summary and accepts with Ctrl+Enter, then opens the next", async () => {
    let pending = [proposalId, nextId];
    const calls = mockApi({
      ...routes(strong(), {
        [`POST /product-proposals/${proposalId}/accept`]: () => {
          pending = [nextId];
          return jsonResponse(200, { ...strong(), status: "accepted", result: { product_id: flourProductId } });
        },
        [`GET /product-proposals/${nextId}`]: () => jsonResponse(200, proposal({ id: nextId })),
      }),
      "GET /product-proposals": () =>
        jsonResponse(200, {
          items: pending.map((id) => ({ id, kind: "new_product", status: "pending", title: "Flour", brand: null, channel: "clip", has_conflict: false, created_at: "2026-09-30T10:00:00Z" })),
          counts: {},
        }),
    });
    const user = userEvent.setup();
    renderApp(`/catalog/products/review/${proposalId}`);

    expect(await screen.findByRole("heading", { level: 1, name: `Update ${flourProduct.name}` })).toBeInTheDocument();
    expect(screen.getByText(/From Juniper Market · Clipped from the page/)).toBeInTheDocument();
    const match = screen.getByRole("group", { name: "Match" });
    expect(within(match).getByRole("radio", { name: new RegExp(`Update ${flourProduct.name}`) })).toBeChecked();
    // Collapsed: the summary, with Edit details instead of the fields.
    expect(screen.getByTestId("review-summary")).toHaveTextContent("Strong white flour · Larkfield · 1.5 kg · 5012345000022");
    expect(screen.getByRole("button", { name: "Edit details" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Brand")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept" })).toBeEnabled();

    await user.keyboard("{Control>}{Enter}{/Control}");
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/accept"))).toBe(true));
    const sent = calls.find((c) => c.path.endsWith("/accept"))!.body as Record<string, unknown>;
    expect(sent.action).toBe("update");
    expect(sent.product_id).toBe(flourProductId);
    expect(await screen.findByText("Added Strong white flour.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open it" })).toHaveAttribute("href", `/catalog/products/${flourProductId}`);
    expect(await screen.findByRole("heading", { level: 1, name: "New product: Strong white flour" })).toBeInTheDocument();
  });

  it("keeps Accept disabled with its reason while something blocks it", async () => {
    mockApi(routes(proposal()));
    const user = userEvent.setup();
    renderApp(`/catalog/products/review/${proposalId}`);
    expect(await screen.findByText("Choose whether to update a product or create one")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept" })).toBeDisabled();
    // Focus starts on Match.
    expect(screen.getByRole("radio", { name: "Create new product" })).toHaveFocus();
    await user.click(screen.getByRole("radio", { name: "Create new product" }));
    expect(screen.getByText("Choose an ingredient to accept")).toBeInTheDocument();
    // Only a field with alternatives opens as choices, with neutral source badges.
    const name = screen.getByRole("group", { name: "Name" });
    expect(within(name).getByRole("radio", { name: /Strong white flour/ })).toBeChecked();
    expect(within(name).getAllByText("Store page")).toHaveLength(2);
    expect(screen.getByRole("group", { name: "Kind" })).toBeInTheDocument();
  });

  it("opens a barcode conflict with no default and blocks Accept until one is chosen", async () => {
    const conflicted = strong();
    conflicted.fields.gtin = { value: GTIN, source: "scan", confidence: null, alternatives: [{ value: OTHER_GTIN, source: "page_data", confidence: null }], conflict: true };
    mockApi(routes(conflicted));
    const user = userEvent.setup();
    renderApp(`/catalog/products/review/${proposalId}`);
    const barcode = await screen.findByRole("group", { name: "Barcode" });
    expect(within(barcode).getByText("These disagree. Choose one.")).toBeInTheDocument();
    expect(within(barcode).getAllByRole("radio").every((r) => !(r as HTMLInputElement).checked)).toBe(true);
    expect(screen.getByText("Choose a barcode to accept")).toBeInTheDocument();
    await user.click(within(barcode).getAllByRole("radio")[1]);
    expect(screen.getByRole("button", { name: "Accept" })).toBeEnabled();
  });

  it("offers to update the product that holds the barcode", async () => {
    let attempts = 0;
    const calls = mockApi(
      routes(strong(), {
        [`POST /product-proposals/${proposalId}/accept`]: () => {
          attempts += 1;
          return attempts === 1
            ? errorResponse(409, "identifier_taken", "Another product has that code.", { product_id: otherProductId, name: "Bread flour" })
            : jsonResponse(200, { ...strong(), status: "accepted", result: { product_id: otherProductId } });
        },
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/products/review/${proposalId}`);
    await user.click(await screen.findByRole("button", { name: "Accept" }));
    const panel = await screen.findByRole("alert");
    expect(panel).toHaveTextContent("Barcode 5012345000022 already belongs to Bread flour.");
    await user.click(within(panel).getByRole("button", { name: "Update Bread flour instead" }));
    await waitFor(() => expect(calls.filter((c) => c.path.endsWith("/accept"))).toHaveLength(2));
    const second = calls.filter((c) => c.path.endsWith("/accept"))[1].body as Record<string, unknown>;
    expect(second).toMatchObject({ action: "update", product_id: otherProductId });
  });

  it("rejects without a dialog and says the capture is kept", async () => {
    const calls = mockApi(
      routes(proposal(), { [`POST /product-proposals/${proposalId}/reject`]: () => jsonResponse(200, { ...proposal(), status: "rejected" }) }, []),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/products/review/${proposalId}`);
    await user.click(await screen.findByRole("button", { name: "Reject" }));
    expect(await screen.findByText("Rejected. The capture is kept.")).toBeInTheDocument();
    expect(calls.filter((c) => c.path.endsWith("/reject"))).toHaveLength(1);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("is read-only once superseded, decided, or says when it is still reading or read nothing", async () => {
    mockApi(routes(proposal({ status: "superseded", decided_at: "2026-10-01T09:00:00Z", result: { superseded_by: nextId } })));
    renderApp(`/catalog/products/review/${proposalId}`);
    expect(await screen.findByText(/A newer capture replaced this/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open it" })).toHaveAttribute("href", `/catalog/products/review/${nextId}`);
    expect(screen.queryByRole("button", { name: "Accept" })).not.toBeInTheDocument();
  });

  it("says it is reading, then that nothing was read", async () => {
    const reading = proposal({ fields: {}, capture: { ...proposal().capture!, channel: "photo" }, jobs: [{ id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9b31", kind: "identify", status: "running", last_error: null }] });
    mockApi(routes(reading));
    renderApp(`/catalog/products/review/${proposalId}`);
    expect(await screen.findByText("Reading this photo…")).toBeInTheDocument();
  });

  it("asks the reader to fill in what they know when nothing was read", async () => {
    mockApi(routes(proposal({ fields: {}, capture: { ...proposal().capture!, channel: "photo" }, reading: { path: "ocr_text", error: "model_unavailable" } })));
    renderApp(`/catalog/products/review/${proposalId}`);
    expect(await screen.findByText("Nothing could be read from this photo. Fill in what you know.")).toBeInTheDocument();
    expect(screen.getByText("The model couldn't be reached, so nothing was read.")).toBeInTheDocument();
    expect(screen.getByLabelText("Name")).toHaveValue("");
  });
});

describe("the reading line and Capture", () => {
  it("names every kind of reading in one sentence", () => {
    expect(readingSentence({ count: 2, photos: 0, pages: 1, oldest_at: null, stalled: false })).toBe("Reading 2 receipts and 1 product page…");
    expect(readingSentence({ count: 0, photos: 2, pages: 0, oldest_at: null, stalled: false })).toBe("Reading 2 product photos…");
    expect(readingSentence({ count: 1, photos: 1, pages: 1, oldest_at: null, stalled: false })).toBe("Reading 1 receipt, 1 product photo and 1 product page…");
  });

  it("sends up to four photos as one proposal and reports progress", async () => {
    const sent: { form?: FormData } = {};
    class FakeXhr {
      upload: { onprogress?: (e: { lengthComputable: boolean; loaded: number; total: number }) => void } = {};
      onload?: () => void;
      onerror?: () => void;
      status = 201;
      responseText = JSON.stringify(proposal());
      withCredentials = false;
      open() {}
      setRequestHeader() {}
      send(form: FormData) {
        sent.form = form;
        this.upload.onprogress?.({ lengthComputable: true, loaded: 5, total: 10 });
        this.onload?.();
      }
    }
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
    const progress: number[] = [];
    const result = await photographProduct(
      [
        { file: new File(["a"], "front.jpg", { type: "image/jpeg" }), role: "product" },
        { file: new File(["b"], "label.jpg", { type: "image/jpeg" }), role: "label_nutrition" },
      ],
      (f) => progress.push(f),
    );
    expect(result.id).toBe(proposalId);
    expect(progress).toEqual([0.5]);
    expect(sent.form!.getAll("roles")).toEqual(["product", "label_nutrition"]);
  });

  it("offers Photograph a product as the fourth Capture mode", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /inbox": () => jsonResponse(200, { items: [], reading: { count: 0, oldest_at: null, stalled: false } }),
      "GET /vendor-locations": () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    const user = userEvent.setup();
    renderApp("/catalog/products/photograph");
    expect(await screen.findByRole("heading", { level: 1, name: "Photograph a product" })).toBeInTheDocument();
    await user.click(screen.getAllByRole("button", { name: "Capture" })[0]);
    const sheet = await screen.findByRole("dialog");
    expect(within(sheet).getByRole("link", { name: /Photograph a product/ })).toHaveAttribute("href", "/catalog/products/photograph");
  });
});
