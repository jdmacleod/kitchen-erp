import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Product } from "../api/catalog";
import { flour, flourProduct, flourProductId, hits, units, ingredientMatch } from "./catalog-fixtures";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp } from "./helpers";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

function baseRoutes(products: () => Product[]) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /products": () => jsonResponse(200, { items: products(), next_cursor: null }),
    "GET /products/search": () => jsonResponse(200, { items: hits }),
    "GET /ingredients/search": () => jsonResponse(200, { items: [] }),
  };
}

/** Open the add drawer from the page header and return its form. */
async function openAddDrawer(user: ReturnType<typeof userEvent.setup>) {
  await screen.findByRole("heading", { name: "Products" });
  await user.click(screen.getAllByRole("button", { name: "Add product" })[0]);
  const dialog = await screen.findByRole("dialog", { name: "Add product" });
  return within(dialog).getByRole("form", { name: "Add product" });
}

describe("products", () => {
  it("creates a product with an inline new ingredient in one request", async () => {
    let products: Product[] = [];
    const created: Product = {
      ...flourProduct,
      id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5d09",
      ingredient: { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5b09", name: "rolled oats", canonical_unit: "g", active: true, category: null, category_key: null },
      brand: null,
      name: "Rolled Oats",
      pack_qty: "1",
      pack_unit: "kg",
      barcode: null,
      quality_rating: 3,
    };
    const calls = mockApi({
      ...baseRoutes(() => products),
      "POST /products": () => {
        products = [created];
        return jsonResponse(201, created);
      },
    });
    const user = userEvent.setup();
    renderApp("/catalog/products");

    const form = await openAddDrawer(user);

    await user.type(within(form).getByRole("combobox", { name: "Ingredient" }), "rolled oats");
    const option = await within(form).findByRole("option", { name: /Create new ingredient/ });
    await user.click(option);
    expect(screen.getByTestId("new-product-ingredient-choice")).toHaveTextContent("rolled oats");

    await user.type(within(form).getByLabelText("Name"), "Rolled Oats");
    await user.type(within(form).getByLabelText("Pack quantity"), "1");
    await user.selectOptions(within(form).getByLabelText("Pack unit"), "kg");
    await user.click(within(form).getByRole("radio", { name: "3" }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Add product" }));

    // The drawer closes, and the new row is in the list, so it takes focus (G10).
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    await waitFor(() => expect(screen.getByRole("link", { name: "Rolled Oats" })).toHaveFocus());
    expect(screen.queryByTestId("notice")).not.toBeInTheDocument();
    const post = calls.find((c) => c.method === "POST" && c.path === "/products");
    expect(post?.body).toEqual({
      name: "Rolled Oats",
      ingredient: { name: "rolled oats" },
      pack_qty: "1",
      pack_unit: "kg",
      quality_rating: 3,
    });
    expect(post?.headers.get("Idempotency-Key")).toMatch(UUID);
  });

  it("links a new product it cannot show from the Notice, with focus on the link (G10)", async () => {
    // The list stays empty: the new row sorts onto a page not loaded yet.
    mockApi({
      ...baseRoutes(() => []),
      "GET /ingredients/search": () => jsonResponse(200, { items: [ingredientMatch(flour)] }),
      "POST /products": () => jsonResponse(201, { ...flourProduct, name: "Zucchini flour", brand: null }),
    });
    const user = userEvent.setup();
    renderApp("/catalog/products");
    const form = await openAddDrawer(user);
    await user.type(within(form).getByRole("combobox", { name: "Ingredient" }), "flour");
    await user.click(await within(form).findByRole("option", { name: /all-purpose flour/ }));
    await user.type(within(form).getByLabelText("Name"), "Zucchini flour");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Add product" }));

    const notice = await screen.findByTestId("notice");
    expect(notice).toHaveTextContent("Added Zucchini flour.");
    const link = within(notice).getByRole("link", { name: "Open it" });
    expect(link).toHaveAttribute("href", `/catalog/products/${flourProduct.id}`);
    await waitFor(() => expect(link).toHaveFocus());
  });

  it("refuses a pack quantity without a unit before calling the API", async () => {
    const calls = mockApi({
      ...baseRoutes(() => []),
      "GET /ingredients/search": () => jsonResponse(200, { items: [ingredientMatch(flour)] }),
    });
    const user = userEvent.setup();
    renderApp("/catalog/products");

    const form = await openAddDrawer(user);
    await user.type(within(form).getByRole("combobox", { name: "Ingredient" }), "flour");
    await user.click(await within(form).findByRole("option", { name: /all-purpose flour/ }));
    await user.type(within(form).getByLabelText("Name"), "Flour");
    await user.type(within(form).getByLabelText("Pack quantity"), "5");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Add product" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("both a pack quantity and a pack unit");
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("shows the server's conflict message for a taken barcode", async () => {
    mockApi({
      ...baseRoutes(() => []),
      "GET /ingredients/search": () => jsonResponse(200, { items: [ingredientMatch(flour)] }),
      "POST /products": () => errorResponse(409, "barcode_taken", "barcode taken"),
    });
    const user = userEvent.setup();
    renderApp("/catalog/products");

    const form = await openAddDrawer(user);
    await user.type(within(form).getByRole("combobox", { name: "Ingredient" }), "flour");
    await user.click(await within(form).findByRole("option", { name: /all-purpose flour/ }));
    await user.type(within(form).getByLabelText("Name"), "Flour");
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Add product" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Another product already has that barcode.");
  });

  it("edits a product, clearing the pack and confirming a density override", async () => {
    let product: Product = { ...flourProduct, density_override: "0.6", density_override_source: "label" };
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      [`GET /products/${flourProductId}`]: () => jsonResponse(200, product),
      [`GET /products/${flourProductId}/prices`]: () => jsonResponse(200, { points: [], latest: [] }),
      [`PATCH /products/${flourProductId}`]: () => {
        product = { ...product, pack_qty: null, pack_unit: null, name: "AP Flour", updated_at: "2026-03-03T00:00:00Z" };
        return jsonResponse(200, product);
      },
      [`POST /products/${flourProductId}/density-override/confirm`]: () => {
        product = { ...product, density_override_confirmed: true };
        return jsonResponse(200, product);
      },
    });
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);

    expect(await screen.findByRole("heading", { name: "Millstone All-Purpose Flour" })).toBeInTheDocument();
    const form = screen.getByRole("form", { name: "Edit product" });
    expect(within(form).getByLabelText("Pack unit")).toHaveValue("lb");

    await user.clear(within(form).getByLabelText("Name"));
    await user.type(within(form).getByLabelText("Name"), "AP Flour");
    await user.clear(within(form).getByLabelText("Pack quantity"));
    await user.selectOptions(within(form).getByLabelText("Pack unit"), "");
    await user.click(within(form).getByRole("button", { name: "Save changes" }));

    await waitFor(() => expect(screen.getByRole("heading", { name: "Millstone AP Flour" })).toBeInTheDocument());
    const patch = calls.find((c) => c.method === "PATCH");
    expect(patch?.body).toEqual({ name: "AP Flour", clear_pack: true });

    await user.click(screen.getByRole("button", { name: "Confirm density override" }));
    await waitFor(() => expect(screen.getByTestId("density-override-summary")).toHaveTextContent("confirmed"));
    expect(screen.queryByRole("button", { name: "Confirm density override" })).not.toBeInTheDocument();
  });
});

describe("a pasted page with the lookup helper (2M)", () => {
  const PAGE = "https://shop.example.test/p/rolled-oats-1kg-4417";

  async function addWithAddress(configured: boolean) {
    let products: Product[] = [];
    const created: Product = { ...flourProduct, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5e01", name: "Rolled Oats", brand: null, barcode: null };
    const calls = mockApi({
      ...baseRoutes(() => products),
      "GET /products-helper": () => jsonResponse(200, { configured }),
      "POST /product-captures/address": () => jsonResponse(200, { vendor: null, canonical_url: PAGE, title: "Rolled oats 1kg", item_number: "4417" }),
      "POST /products": () => {
        products = [created];
        return jsonResponse(201, created);
      },
      [`POST /products/${created.id}/look-up`]: () =>
        jsonResponse(200, { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5e02", kind: "page", value: PAGE, listing_id: null, status: "open", created_at: "2026-10-05T10:00:00Z", answered_at: null }),
    });
    const user = userEvent.setup();
    renderApp("/catalog/products");
    const form = await openAddDrawer(user);
    await user.click(within(form).getByLabelText("Web address (optional)"));
    await user.paste(PAGE);
    await waitFor(() => expect(within(form).getByLabelText("Name")).toHaveValue("Rolled oats 1kg"));
    const hint = configured
      ? /Saving asks the lookup helper to read this page\. What it finds waits on Home for review\./
      : /This page can't be read from here\. Use Save to Kitchen ERP on the page\./;
    expect(within(form).getByText(hint)).toBeInTheDocument();
    await user.type(within(form).getByRole("combobox", { name: "Ingredient" }), "rolled oats");
    await user.click(await within(form).findByRole("option", { name: /Create new ingredient/ }));
    await user.click(within(screen.getByRole("dialog")).getByRole("button", { name: "Add product" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    return { calls, created };
  }

  it("sends the page to the helper after saving, and says so", async () => {
    const { calls, created } = await addWithAddress(true);
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === `/products/${created.id}/look-up`)).toBe(true));
    expect(calls.find((c) => c.path === `/products/${created.id}/look-up`)?.body).toEqual({ page_url: PAGE });
    expect(await screen.findByTestId("notice")).toHaveTextContent("Added Rolled Oats. The lookup helper will read its page; what it finds will wait on Home for review.");
  });

  it("sends nothing without a helper", async () => {
    const { calls } = await addWithAddress(false);
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/products")).toBe(true));
    expect(calls.some((c) => c.path.endsWith("/look-up"))).toBe(false);
  });

  it("says when the address is already in the catalog (2P)", async () => {
    const knownId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5e09";
    mockApi({
      ...baseRoutes(() => []),
      "GET /products-helper": () => jsonResponse(200, { configured: false }),
      "POST /product-captures/address": () =>
        jsonResponse(200, { vendor: null, canonical_url: PAGE, title: "Rolled oats 1kg", item_number: "4417", known: { product_id: knownId, name: "Rolled oats", reason: "identifier" } }),
    });
    const user = userEvent.setup();
    renderApp("/catalog/products");
    const form = await openAddDrawer(user);
    await user.click(within(form).getByLabelText("Web address (optional)"));
    await user.paste(PAGE);
    expect(await within(form).findByText(/Already in your catalog:/)).toBeInTheDocument();
    const link = within(form).getByRole("link", { name: "Rolled oats" });
    expect(link).toHaveAttribute("href", `/catalog/products/${knownId}`);
    expect(link).toHaveAttribute("target", "_blank");
  });
});
