import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ProductListItem } from "../api/catalog";
import { flour, flourProduct, hits, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall, type RouteHandler } from "./helpers";

const paidFlour: ProductListItem = {
  ...flourProduct,
  quality_rating: 4,
  last_paid: {
    price: "6.4900",
    qty: "1",
    unit: "each",
    is_promo: false,
    vendor_id: "v1",
    vendor_name: "Pier Stand",
    purchase_id: "p1",
    paid_at: "2026-09-20T17:00:00Z",
  },
};

function mount(products: RouteHandler, path = "/catalog/products") {
  const calls = mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /ingredients": () => jsonResponse(200, { items: [flour], next_cursor: null }),
    [`GET /ingredients/${flour.id}`]: () => jsonResponse(200, flour),
    "GET /products/search": () => jsonResponse(200, { items: hits }),
    "GET /products": products,
  });
  renderApp(path);
  return calls;
}

const productCalls = (calls: RecordedCall[]) => calls.filter((c) => c.method === "GET" && c.path.startsWith("/products?"));

describe("the Products page (UI-3.4)", () => {
  it("shows the five columns, with stars and what was last paid", async () => {
    mount(() => jsonResponse(200, { items: [paidFlour, { ...flourProduct, id: "x2", name: "Rye", brand: null, quality_rating: null, last_paid: null }], next_cursor: null }));
    const table = await screen.findByRole("table", { name: "Products" });
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Product", "Ingredient", "Pack", "Quality", "Last paid"]);
    const [first, second] = within(table).getAllByRole("row").slice(1);
    expect(within(first).getByRole("img", { name: "4 of 5 stars" })).toBeInTheDocument();
    expect(first).toHaveTextContent("$6.49");
    expect(first).toHaveTextContent("Pier Stand · Sep 20, 2026");
    expect(within(first).getByText("pantry")).toHaveClass("cat-pantry");
    expect(within(second).getByLabelText("Unrated")).toBeInTheDocument();
    expect(within(second).getByLabelText("Never paid")).toBeInTheDocument();
  });

  it("searches and filters by category on the server, and keeps both in the URL", async () => {
    const calls = mount(() => jsonResponse(200, { items: [paidFlour], next_cursor: null }));
    const user = userEvent.setup();
    await screen.findByRole("table", { name: "Products" });

    await user.type(screen.getByLabelText("Search products"), "oat");
    await waitFor(() => expect(productCalls(calls).some((c) => c.query.get("q") === "oat")).toBe(true));
    await user.click(within(screen.getByRole("group", { name: "Category" })).getByRole("button", { name: "Dairy" }));
    await waitFor(() =>
      expect(productCalls(calls).some((c) => c.query.get("q") === "oat" && c.query.get("category") === "dairy")).toBe(true),
    );
    expect(within(screen.getByRole("group", { name: "Category" })).getByRole("button", { name: "Dairy" })).toHaveAttribute("aria-pressed", "true");
  });

  it("tells a filtered-empty list from an empty catalog (G11)", async () => {
    mount((call) => jsonResponse(200, { items: call.query.get("q") ? [] : [], next_cursor: null }), "/catalog/products?q=oat&category=dairy");
    const empty = await screen.findByRole("region", { name: "No products match ‘oat’ in Dairy" });
    const user = userEvent.setup();
    await user.click(within(empty).getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByRole("region", { name: "Add your first product" })).toBeInTheDocument();
  });

  it("opens the add drawer with the ingredient it arrived from", async () => {
    mount(() => jsonResponse(200, { items: [], next_cursor: null }), `/catalog/products?ingredient_id=${flour.id}`);
    // The drawer shows a loading state first, then the form with the ingredient chosen.
    await waitFor(() => expect(within(screen.getByRole("dialog", { name: "Add product" })).getByTestId("new-product-ingredient-choice")).toHaveTextContent("all-purpose flour"));
  });

  it("explains, in the drawer, why a volume pack needs a density for a mass ingredient (UI-3.5)", async () => {
    mount(() => jsonResponse(200, { items: [], next_cursor: null }), `/catalog/products?ingredient_id=${flour.id}`);
    const user = userEvent.setup();
    await waitFor(() => expect(within(screen.getByRole("dialog", { name: "Add product" })).getByTestId("new-product-ingredient-choice")).toHaveTextContent("all-purpose flour"));
    const dialog = screen.getByRole("dialog", { name: "Add product" });
    expect(within(dialog).queryByTestId("density-reason")).not.toBeInTheDocument();

    await user.selectOptions(within(dialog).getByLabelText("Pack unit"), "ml");
    expect(within(dialog).getByTestId("density-reason")).toHaveTextContent("ml is a volume unit and all-purpose flour is measured in g");
    expect(within(dialog).getByLabelText("Density (g/ml)")).toBeInTheDocument();
  });
});

describe("the Products page, review follow-ups", () => {
  it("counts a half-typed ingredient search as typed input (D5)", async () => {
    mount(() => jsonResponse(200, { items: [], next_cursor: null }));
    const user = userEvent.setup();
    await screen.findByRole("heading", { name: "Products" });
    await user.click(screen.getAllByRole("button", { name: "Add product" })[0]);
    const dialog = await screen.findByRole("dialog", { name: "Add product" });
    await user.type(within(dialog).getByRole("combobox", { name: "Ingredient" }), "oat");
    // The first Escape closes the suggestion list, as a combobox should; the
    // second asks to close the drawer, and the typed text makes it ask first.
    await user.keyboard("{Escape}");
    await user.keyboard("{Escape}");
    expect(await within(dialog).findByText("Discard this product? What you typed will be lost.")).toBeInTheDocument();
  });

  it("gives no density hint when the ingredient already has a density", async () => {
    const dense = { ...flour, density_g_per_ml: "0.53", density_source: "usda" as const };
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      "GET /ingredients": () => jsonResponse(200, { items: [dense], next_cursor: null }),
      [`GET /ingredients/${flour.id}`]: () => jsonResponse(200, dense),
      "GET /products": () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    renderApp(`/catalog/products?ingredient_id=${flour.id}`);
    const user = userEvent.setup();
    await waitFor(() => expect(within(screen.getByRole("dialog", { name: "Add product" })).getByTestId("new-product-ingredient-choice")).toBeInTheDocument());
    const dialog = screen.getByRole("dialog", { name: "Add product" });
    await user.selectOptions(within(dialog).getByLabelText("Pack unit"), "ml");
    expect(within(dialog).queryByTestId("density-reason")).not.toBeInTheDocument();
  });

  it("keeps the search field in step with ?q= when a link changes it", async () => {
    mount(() => jsonResponse(200, { items: [paidFlour], next_cursor: null }), "/catalog/products?q=oat");
    const field = await screen.findByLabelText("Search products");
    expect(field).toHaveValue("oat");
    const user = userEvent.setup();
    // The sidebar's Products link lands on the page with no search.
    await user.click(within(screen.getAllByRole("navigation", { name: "Main" })[0]).getByRole("link", { name: "Products" }));
    await waitFor(() => expect(screen.getByLabelText("Search products")).toHaveValue(""));
    await new Promise((r) => setTimeout(r, 400));
    expect(screen.getByLabelText("Search products")).toHaveValue("");
  });
});
