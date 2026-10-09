import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ProductPhoto } from "../api/productPhotos";
import { photographProduct, type Proposal } from "../api/proposals";
import { parsePieces } from "../pages/catalog/ProductReviewPage";
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

const media = (n: number) => ({ small: `/api/v1/media/${n}/160`, medium: `/api/v1/media/${n}/480`, large: `/api/v1/media/${n}/1200`, cutout_medium: null, cutout_large: null });

function arrivedPhoto(id: string, size: number): ProductPhoto {
  return {
    id,
    product_id: null,
    width: size,
    height: size,
    has_cutout: false,
    urls: media(size),
    role: "product",
    status: "candidate",
    source_kind: "vendor_listing",
    source_url: null,
    attribution: null,
    cutout_source: null,
    pinned: false,
    is_stock_suspect: false,
    ocr_text: null,
    captured_at: null,
    created_at: "2026-09-30T10:00:00Z",
    is_main: false,
  };
}

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
    // An update keeps what the product has unless the person changes it.
    expect(await screen.findByText(`${flourProduct.name} · ${flourProduct.brand} · 5 lb · 5012345000022`)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Edit details" })).toBeInTheDocument();
    expect(screen.queryByLabelText("Brand")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept" })).toBeEnabled();

    await user.keyboard("{Control>}{Enter}{/Control}");
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/accept"))).toBe(true));
    const sent = calls.find((c) => c.path.endsWith("/accept"))!.body as Record<string, unknown>;
    expect(sent.action).toBe("update");
    expect(sent.product_id).toBe(flourProductId);
    expect(await screen.findByText(`Updated ${flourProduct.name}.`)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Open it" })).toHaveAttribute("href", `/catalog/products/${flourProductId}`);
    expect(await screen.findByRole("heading", { level: 1, name: "New product: Strong white flour" })).toBeInTheDocument();
  });

  // Regression: ISSUE-003/004/005 — a lookup update said "Same barcode", "Not one of your
  // stores" for a known store without locations, and showed the helper's name and brand
  // as if accepting would replace the product's own. Found by /qa on 2026-10-05.
  it("shows a lookup update as keeping the product's own values unless one is chosen", async () => {
    const vendorId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9b40";
    const looked = proposal({
      kind: "product_update",
      product_id: flourProductId,
      capture: null,
      fields: {
        title: { value: "MILLSTONE PLAIN FLOUR", source: "adapter", confidence: null, via: "helper", alternatives: [{ value: "Product Detail", source: "page_meta", confidence: null, via: "helper" }], conflict: false },
        brand: { value: "MILLSTONE", source: "adapter", confidence: null, via: "helper", alternatives: [], conflict: false },
        gtin: { value: GTIN, source: "adapter", confidence: null, via: "helper", alternatives: [], conflict: false },
      },
      match: { strong: { product_id: flourProductId, reason: "lookup" }, candidates: [], preselect: `update:${flourProductId}` },
      vendor: { id: vendorId, name: "Fennel Street Grocer", price_scope: "location", locations: [], suggested_location_id: null },
    });
    const calls = mockApi(
      routes(looked, {
        [`POST /product-proposals/${proposalId}/accept`]: () => jsonResponse(200, { ...looked, status: "accepted", result: { product_id: flourProductId } }),
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/products/review/${proposalId}`);

    const match = await screen.findByRole("group", { name: "Match" });
    expect(within(match).getByRole("radio", { name: /Looked up for this product/ })).toBeChecked();
    expect(await screen.findByText(`${flourProduct.name} · ${flourProduct.brand} · 5 lb · 5012345000022`)).toBeInTheDocument();
    expect(screen.getByText(/Fennel Street Grocer has no stores yet, so no price is recorded/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add one" })).toHaveAttribute("href", `/catalog/vendors/${vendorId}`);

    await user.click(screen.getByRole("button", { name: "Edit details" }));
    const name = screen.getByRole("group", { name: "Name" });
    expect(within(name).getByRole("radio", { name: new RegExp(`^Keep “${flourProduct.name}”`) })).toBeChecked();
    expect(screen.getByLabelText("Brand")).toHaveValue(flourProduct.brand);
    expect(screen.getByText(/Kept from the product. The lookup helper says “MILLSTONE”/)).toBeInTheDocument();

    // Choosing the helper's name replaces the product's; the untouched brand is kept.
    await user.click(within(name).getByRole("radio", { name: /MILLSTONE PLAIN FLOUR/ }));
    await user.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/accept"))).toBe(true));
    const sent = calls.find((c) => c.path.endsWith("/accept"))!.body as { edits: Record<string, unknown> };
    expect(sent.edits).toEqual({ title: "MILLSTONE PLAIN FLOUR" });
    expect(await screen.findByText("Updated MILLSTONE PLAIN FLOUR.")).toBeInTheDocument();
  });

  it("shows a pack's pieces and takes typed ones as the person's", async () => {
    const withPieces = proposal({
      fields: {
        ...proposal().fields,
        pieces: { value: { count: "4", name: "link" }, source: "page_meta", confidence: null, alternatives: [], conflict: false },
      },
    });
    const calls = mockApi(
      routes(withPieces, {
        "POST /ingredients": () => jsonResponse(201, { ...flourProduct.ingredient, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9b50", name: "flour" }),
        [`POST /product-proposals/${proposalId}/accept`]: () => jsonResponse(200, { ...withPieces, status: "accepted", result: { product_id: flourProductId } }),
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/products/review/${proposalId}`);
    expect(await screen.findByTestId("review-summary")).toHaveTextContent("1.5 kg · 4 links");

    await user.click(screen.getByRole("radio", { name: "Create new product" }));
    const pieces = screen.getByLabelText("Pieces");
    expect(pieces).toHaveValue("4 links");
    await user.clear(pieces);
    await user.type(pieces, "6 Links");
    expect(screen.getByTestId("review-summary")).toHaveTextContent("6 Links");
    const ingredient = screen.getByRole("combobox", { name: "Ingredient" });
    await user.type(ingredient, "flour");
    await user.click(await screen.findByRole("option", { name: /Create new ingredient/ }));
    await user.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/accept"))).toBe(true));
    const sent = calls.find((c) => c.path.endsWith("/accept"))!.body as { edits: Record<string, unknown> };
    expect(sent.edits.pieces).toEqual({ count: "6", name: "link" });
  });

  it("offers no pieces for a pack counted in each, and offers them once the pack is a weight", async () => {
    const single = proposal({
      fields: { ...proposal().fields, pack: { value: { qty: "1", unit: "each" }, source: "page_data", confidence: null, alternatives: [], conflict: false } },
    });
    mockApi(routes(single));
    const user = userEvent.setup();
    renderApp(`/catalog/products/review/${proposalId}`);
    await screen.findByTestId("review-summary");
    await user.click(screen.getByRole("radio", { name: "Create new product" }));
    expect(screen.getByLabelText("Pack")).toHaveValue("1 each");
    expect(screen.queryByLabelText("Pieces")).not.toBeInTheDocument();

    await user.clear(screen.getByLabelText("Pack"));
    await user.type(screen.getByLabelText("Pack"), "300 g");
    expect(screen.getByLabelText("Pieces")).toBeInTheDocument();
  });

  it("reads typed pieces", () => {
    expect(parsePieces("5 links")).toEqual({ count: "5", name: "link" });
    expect(parsePieces("3 patties")).toEqual({ count: "3", name: "patty" });
    expect(parsePieces("2 boxes")).toEqual({ count: "2", name: "box" });
    expect(parsePieces("12")).toEqual({ count: "12" });
    expect(parsePieces("4.5 links")).toBeNull();
    expect(parsePieces("0")).toBeNull();
  });

  it("asks whether to update or create while the catalog offers a candidate", async () => {
    const similar = proposal({ match: { strong: null, candidates: [{ product_id: otherProductId, name: "Bread flour", brand: null, score: "0.6" }], preselect: null } });
    mockApi(routes(similar));
    renderApp(`/catalog/products/review/${proposalId}`);
    expect(await screen.findByText("Choose whether to update a product or create one")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept" })).toBeDisabled();
    expect(screen.getByRole("radio", { name: "Create new product" })).not.toBeChecked();
  });

  it("labels each candidate with its verdict and pack, and still preselects nothing (2P)", async () => {
    const ingredient = { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9b40", name: "Flour", category_key: null };
    const base = { brand: "Larkfield", pack_count: null, piece_name: null, photo: null, ingredient, score: "0.9", reasons: ["words"], only_here: [], only_there: [] };
    const sizeId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9b41";
    const variantId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9b42";
    const candidates = [
      { ...base, product_id: otherProductId, name: "Strong white flour", pack_qty: "1.5", pack_unit: "kg", verdict: "same" as const },
      { ...base, product_id: sizeId, name: "Strong white flour", pack_qty: "3", pack_unit: "kg", verdict: "other_size" as const, reasons: ["pack"] },
      { ...base, product_id: variantId, name: "Strong brown flour", pack_qty: "1.5", pack_unit: "kg", verdict: "variant" as const, only_here: ["white"], only_there: ["brown"] },
    ];
    const stored = candidates.map(({ product_id, name, brand, score }) => ({ product_id, name, brand, score }));
    mockApi(routes(proposal({ match: { strong: null, candidates: stored, preselect: null }, candidates })));
    renderApp(`/catalog/products/review/${proposalId}`);
    const match = await screen.findByRole("group", { name: "Match" });
    const radios = within(match).getAllByRole("radio");
    expect(radios[0]).toHaveAccessibleName(/Strong white flour \(Larkfield\) · 1\.5 kg\s*Likely the same product/);
    expect(radios[1]).toHaveAccessibleName(/3 kg\s*Different size \(3 kg\)/);
    expect(radios[2]).toHaveAccessibleName(/Different variant \(brown, not white\)/);
    // A verdict labels; nothing is chosen until the reviewer chooses (criterion 74).
    for (const radio of radios) expect(radio).not.toBeChecked();
  });

  it("links to look-alikes waiting in the queue (2P)", async () => {
    mockApi(routes(proposal({ look_alikes: [{ id: nextId, title: "Strong white flour 1.5kg" }] })));
    renderApp(`/catalog/products/review/${proposalId}`);
    const line = await screen.findByTestId("look-alikes");
    expect(line).toHaveTextContent("Also waiting: 1 likely the same.");
    expect(within(line).getByRole("link", { name: "Strong white flour 1.5kg" })).toHaveAttribute("href", `/catalog/products/review/${nextId}`);
  });

  it("keeps the product's larger main photo over a smaller one that arrived, and flags the smaller one", async () => {
    const currentId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c01";
    const smallId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c02";
    const withPhoto = { ...flourProduct, photo: { id: currentId, width: 500, height: 500, has_cutout: false, urls: media(500) } };
    const update = (photos: ProductPhoto[]) => ({ ...strong(), photos });
    const calls = mockApi(
      routes(update([arrivedPhoto(smallId, 200)]), {
        [`GET /products/${flourProductId}`]: () => jsonResponse(200, withPhoto),
        [`POST /product-proposals/${proposalId}/accept`]: () => jsonResponse(200, { ...strong(), status: "accepted", result: { product_id: flourProductId } }),
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/products/review/${proposalId}`);

    const images = await screen.findByRole("group", { name: "Images" });
    await within(images).findByText("Current");
    const [current, arrived] = within(images).getAllByTestId("review-tile");
    expect(within(current).getByText("Current")).toBeInTheDocument();
    expect(within(current).getByText("500 × 500 px")).toBeInTheDocument();
    expect(within(current).getByRole("radio", { name: "Main photo" })).toBeChecked();
    expect(within(arrived).getByText("200 × 200 px")).toBeInTheDocument();
    expect(within(arrived).getByText("Smaller than the current main photo")).toBeInTheDocument();
    expect(within(arrived).getByRole("radio", { name: "Use as main photo" })).not.toBeChecked();

    await user.click(screen.getByRole("button", { name: "Accept" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/accept"))).toBe(true));
    expect((calls.find((c) => c.path.endsWith("/accept"))!.body as { main_photo_id?: string }).main_photo_id).toBe(currentId);
  });

  it("preselects a larger photo that arrived over the product's main photo", async () => {
    const currentId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c01";
    const largeId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c03";
    const withPhoto = { ...flourProduct, photo: { id: currentId, width: 200, height: 200, has_cutout: false, urls: media(200) } };
    mockApi(routes({ ...strong(), photos: [arrivedPhoto(largeId, 800)] }, { [`GET /products/${flourProductId}`]: () => jsonResponse(200, withPhoto) }));
    renderApp(`/catalog/products/review/${proposalId}`);

    const images = await screen.findByRole("group", { name: "Images" });
    await within(images).findByText("Current");
    const [current, arrived] = within(images).getAllByTestId("review-tile");
    expect(within(current).getByRole("radio", { name: "Keep as main photo" })).not.toBeChecked();
    expect(within(arrived).getByRole("radio", { name: "Main photo" })).toBeChecked();
    expect(within(arrived).queryByText("Smaller than the current main photo")).not.toBeInTheDocument();
  });

  it("keeps Accept disabled with its reason while something blocks it", async () => {
    mockApi(routes(proposal()));
    renderApp(`/catalog/products/review/${proposalId}`);
    // With nothing in the catalog to update, a new product is preselected (clip quality, CQ3).
    expect(await screen.findByText("Choose an ingredient to accept")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Accept" })).toBeDisabled();
    // Focus starts on Match.
    expect(screen.getByRole("radio", { name: "Create new product" })).toBeChecked();
    expect(screen.getByRole("radio", { name: "Create new product" })).toHaveFocus();
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

  it("lets keeping what the product has settle a pack conflict on an update", async () => {
    // The page's pack and the model's reading disagree, but the product being updated
    // already has a pack: keeping it is an answer, and nothing of the conflict is sent.
    const conflicted = strong();
    conflicted.fields.pack = {
      value: { qty: "400", unit: "ml" },
      source: "page_data",
      confidence: null,
      alternatives: [{ value: { qty: "16.2", unit: "oz" }, source: "model", confidence: "0.6" }],
      conflict: true,
    };
    const calls = mockApi(
      routes(conflicted, {
        [`POST /product-proposals/${proposalId}/accept`]: () => jsonResponse(200, { ...conflicted, status: "accepted", result: { product_id: flourProductId } }),
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/products/review/${proposalId}`);
    const accept = await screen.findByRole("button", { name: "Accept" });
    await waitFor(() => expect(accept).toBeEnabled());
    expect(screen.queryByText("Choose a pack to accept")).not.toBeInTheDocument();
    await user.click(accept);
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/accept"))).toBe(true));
    const body = calls.find((c) => c.method === "POST" && c.path.endsWith("/accept"))?.body as { edits?: Record<string, unknown> };
    expect(body.edits?.pack).toBeUndefined();
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
