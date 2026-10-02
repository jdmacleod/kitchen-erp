import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ProductPhoto, ProductPhotoList } from "../api/productPhotos";
import { flourProduct, flourProductId, units } from "./catalog-fixtures";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

// 1I: the product page's Photos and Labels cards (10, PD4, PD16; UI-6.8–6.9)
// and the Products table thumbnail (PD5; UI-6.10).

const sha = "a".repeat(64);
const maskSha = "b".repeat(64);
const urls = (s: string, mask: string | null = null) => ({
  small: `/api/v1/media/${s}/160-0a1b2c3d`,
  medium: `/api/v1/media/${s}/480-0a1b2c3d`,
  large: `/api/v1/media/${s}/1200-0a1b2c3d`,
  cutout_medium: mask ? `/api/v1/media/${s}/cutout-480-0a1b2c3d-${mask}` : null,
  cutout_large: mask ? `/api/v1/media/${s}/cutout-1200-0a1b2c3d-${mask}` : null,
});

function photo(id: string, extra: Partial<ProductPhoto> = {}): ProductPhoto {
  return {
    id,
    product_id: flourProductId,
    width: 800,
    height: 800,
    has_cutout: false,
    urls: urls(sha),
    role: "product",
    status: "active",
    source_kind: "user_photo",
    source_url: null,
    attribution: null,
    cutout_source: null,
    pinned: false,
    is_stock_suspect: false,
    ocr_text: null,
    captured_at: null,
    created_at: "2026-09-01T12:00:00Z",
    is_main: false,
    ...extra,
  };
}

const mainId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9a01";
const otherId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9a02";
const hiddenId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9a03";
const labelId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9a04";

function routes(list: () => ProductPhotoList, extra: Record<string, (c: RecordedCall) => Response> = {}) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    [`GET /products/${flourProductId}`]: () => jsonResponse(200, flourProduct),
    [`GET /products/${flourProductId}/prices`]: () => jsonResponse(200, { points: [], latest: [] }),
    "GET /price-observations": () => jsonResponse(200, { items: [], next_cursor: null }),
    [`GET /products/${flourProductId}/photos`]: () => jsonResponse(200, list()),
    ...extra,
  };
}

describe("product photos", () => {
  it("shows the category placeholder and Add photo when there is no photo", async () => {
    mockApi(routes(() => ({ items: [], primary_image_id: null })));
    renderApp(`/catalog/products/${flourProductId}`);
    const card = (await screen.findByRole("heading", { name: "Photos" })).closest("section")!;
    expect(await within(card).findByText("No photo yet.")).toBeInTheDocument();
    const placeholder = within(card).getByTestId("photo-placeholder");
    expect(placeholder).toHaveTextContent(flourProduct.name.charAt(0).toUpperCase());
    expect(placeholder).toHaveAttribute("aria-hidden", "true");
    expect(within(card).getByRole("button", { name: "Add photo" })).toBeInTheDocument();
    // No label photos, no Labels card.
    expect(screen.queryByRole("heading", { name: "Labels" })).not.toBeInTheDocument();
  });

  it("shows the main photo's cutout with Cutout | Original, and the tile actions", async () => {
    const calls = mockApi(
      routes(
        () => ({
          items: [
            photo(mainId, { is_main: true, has_cutout: true, urls: urls(sha, maskSha), cutout_source: "tool" }),
            photo(otherId, { source_kind: "manufacturer", attribution: "Image courtesy of the maker" }),
            photo(hiddenId, { status: "hidden" }),
          ],
          primary_image_id: mainId,
        }),
        { [`POST /product-photos/${otherId}/use-as-main`]: () => jsonResponse(200, photo(otherId, { is_main: true, pinned: true })) },
      ),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);

    const main = await screen.findByTestId("main-photo");
    const image = within(main).getByRole("img");
    expect(image.getAttribute("src")).toContain("cutout-480");
    expect(image).toHaveClass("cutout-shadow");
    expect(within(main).getByText("Main photo")).toBeInTheDocument();
    await user.click(within(main).getByRole("button", { name: "Original" }));
    expect(within(main).getByRole("img").getAttribute("src")).toContain("/480-");

    const tiles = screen.getByRole("list", { name: "Other photos" });
    expect(within(tiles).getAllByTestId("photo-tile")).toHaveLength(1);
    expect(within(tiles).getByText("Manufacturer")).toBeInTheDocument();
    expect(within(tiles).getByText("Image courtesy of the maker")).toBeInTheDocument();

    await user.click(screen.getByLabelText("Show hidden (1)"));
    expect(within(tiles).getAllByTestId("photo-tile")).toHaveLength(2);
    expect(within(tiles).getByRole("button", { name: "Show" })).toBeInTheDocument();

    await user.click(within(tiles).getByRole("button", { name: "Use as main photo" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === `/product-photos/${otherId}/use-as-main`)).toBe(true));
  });

  it("says when a photo is being prepared or failed, and offers Retry", async () => {
    const calls = mockApi(
      routes(
        () => ({
          items: [photo(mainId, { is_main: true }), photo(otherId, { status: "processing", urls: null }), photo(hiddenId, { status: "failed", urls: null })],
          primary_image_id: mainId,
        }),
        { [`POST /product-photos/${hiddenId}/retry`]: () => jsonResponse(200, photo(hiddenId, { status: "processing", urls: null })) },
      ),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);
    expect(await screen.findByText("Preparing photo…")).toBeInTheDocument();
    expect(screen.getByText("Couldn't process this photo")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(calls.some((c) => c.path === `/product-photos/${hiddenId}/retry`)).toBe(true));
  });

  it("lists label photos in the Labels card with the text read from them", async () => {
    mockApi(
      routes(() => ({
        items: [photo(mainId, { is_main: true }), photo(labelId, { role: "label_nutrition", ocr_text: "Serving size 30 g" })],
        primary_image_id: mainId,
      })),
    );
    renderApp(`/catalog/products/${flourProductId}`);
    const card = (await screen.findByRole("heading", { name: "Labels" })).closest("section")!;
    expect(within(card).getByRole("heading", { name: "Nutrition" })).toBeInTheDocument();
    expect(within(card).getByText("Serving size 30 g")).toBeInTheDocument();
    // A label is never offered as the main photo.
    expect(screen.queryByRole("list", { name: "Other photos" })).not.toBeInTheDocument();
  });

  it("uploads chosen photos with a role each, and says a refused one plainly", async () => {
    let refuse = true;
    const calls = mockApi(
      routes(() => ({ items: [], primary_image_id: null }), {
        "POST /product-photos": () =>
          refuse
            ? errorResponse(422, "image_too_large", "This photo is over 50 megapixels.")
            : jsonResponse(201, { items: [photo(mainId, { status: "processing", urls: null })], primary_image_id: null }),
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);
    await user.click(await screen.findByRole("button", { name: "Add photo" }));
    const input = document.querySelector<HTMLInputElement>('input[type="file"]')!;
    await user.upload(input, [new File(["a"], "front.jpg", { type: "image/jpeg" }), new File(["b"], "back.jpg", { type: "image/jpeg" })]);
    const staged = screen.getByRole("list", { name: "Photos to add" });
    await user.selectOptions(within(staged).getByLabelText("Role for back.jpg"), "Nutrition");

    await user.click(screen.getByRole("button", { name: "Upload 2 photos" }));
    expect(await screen.findByText("This photo is over 50 megapixels.")).toBeInTheDocument();

    refuse = false;
    await user.click(screen.getByRole("button", { name: "Upload 2 photos" }));
    expect(await screen.findByText("Photos saved. They'll be ready in a moment.")).toBeInTheDocument();
    const posted = calls.filter((c) => c.method === "POST" && c.path === "/product-photos");
    expect(posted).toHaveLength(2);
  });
});

describe("the products table", () => {
  it("leads each row with a 40px photo or the placeholder", async () => {
    const withPhoto = { ...flourProduct, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5d02", name: "Bread flour", photo: { id: mainId, width: 800, height: 800, has_cutout: false, urls: urls(sha) } };
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      "GET /products": () => jsonResponse(200, { items: [{ ...flourProduct, photo: null }, withPhoto], next_cursor: null }),
    });
    renderApp("/catalog/products");
    const table = await screen.findByRole("table", { name: "Products" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(within(rows[0]).getByTestId("photo-placeholder")).toHaveStyle({ width: "40px", height: "40px" });
    const img = rows[1].querySelector("img")!;
    expect(img.getAttribute("src")).toContain("/160-");
    expect(img).toHaveAttribute("alt", "");
  });
});
