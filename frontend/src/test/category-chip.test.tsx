import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { CATEGORY_KEYS, CategoryChip, categoryClass } from "../components/CategoryChip";
import type { Product } from "../api/catalog";
import { flourProduct, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { manualPurchase, purchaseId } from "./purchase-fixtures";
import themeCss from "../theme.css?raw";

describe("CategoryChip (UI-1.6)", () => {
  it("colours the chip by the API's key and labels it with the free text", () => {
    render(<CategoryChip category=" Shellfish " categoryKey="seafood" />);
    const chip = screen.getByText("Shellfish");
    expect(chip).toHaveClass("cat", "cat-seafood", "cat-chip");
  });

  it("does no mapping of its own: text without a key gets the neutral chip", () => {
    // The backend owns the synonyms (D12). "Fish" would be seafood there, but a null
    // key from the API must stay neutral here rather than be re-derived.
    render(<CategoryChip category="Fish" categoryKey={null} />);
    const chip = screen.getByText("Fish");
    expect(chip).toHaveClass("cat", "cat-chip");
    for (const key of CATEGORY_KEYS) expect(chip).not.toHaveClass(`cat-${key}`);
  });

  it("renders nothing for an ingredient without a category", () => {
    const { container } = render(
      <>
        <CategoryChip category={null} categoryKey={null} />
        <CategoryChip category="   " categoryKey={null} />
      </>,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("has a theme colour for every key and a neutral class for none", () => {
    expect(categoryClass(null)).toBe("cat");
    for (const key of CATEGORY_KEYS) {
      expect(categoryClass(key)).toBe(`cat cat-${key}`);
      expect(themeCss).toMatch(new RegExp(`\\.cat-${key}\\s*\\{`));
    }
  });
});

describe("category chips on pages", () => {
  it("shows the ingredient's chip on each product row", async () => {
    const products: Product[] = [flourProduct];
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      "GET /products": () => jsonResponse(200, { items: products, next_cursor: null }),
      "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    renderApp("/catalog/products");
    const list = await screen.findByRole("table", { name: "Products" });
    expect(within(list).getByText("pantry")).toHaveClass("cat-pantry");
  });

  it("shows each purchase line's ingredient chip", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      [`GET /purchases/${purchaseId}`]: () => jsonResponse(200, manualPurchase),
    });
    renderApp(`/shop/purchases/${purchaseId}`);
    const table = await screen.findByRole("table", { name: "Lines" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(within(rows[0]).getByText("pantry")).toHaveClass("cat-pantry");
    // A synonym the backend resolved: the label is the text, the colour is the key.
    expect(within(rows[1]).getByText("Baking")).toHaveClass("cat-pantry");
  });
});
