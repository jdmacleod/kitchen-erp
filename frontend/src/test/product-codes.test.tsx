import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ProductCode } from "../api/productCodes";
import { codeLabel } from "../components/catalog/ProductCodesCard";
import { flourProduct, flourProductId, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

// Issue 448: a remembered store code can be forgotten from the product page; the
// barcode is changed in Edit product. The store and codes are invented.

const barcode: ProductCode = {
  id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f6c01",
  scheme: "gtin",
  value: "012345678905",
  vendor_id: null,
  vendor_name: null,
  source: "manual",
  is_barcode: true,
  created_at: "2026-10-01T12:00:00Z",
};
const counterCode: ProductCode = {
  id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f6c02",
  scheme: "rw_item",
  value: "12345",
  vendor_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f6c03",
  vendor_name: "Juniper Market",
  source: "receipt",
  is_barcode: false,
  created_at: "2026-10-02T12:00:00Z",
};

function routes(codes: () => ProductCode[], extra = {}) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    [`GET /products/${flourProductId}`]: () => jsonResponse(200, flourProduct),
    [`GET /products/${flourProductId}/prices`]: () => jsonResponse(200, { points: [], latest: [] }),
    [`GET /products/${flourProductId}/photos`]: () => jsonResponse(200, { items: [] }),
    [`GET /products/${flourProductId}/codes`]: () => jsonResponse(200, { items: codes() }),
    "GET /price-observations": () => jsonResponse(200, { items: [], next_cursor: null }),
    ...extra,
  };
}

describe("a product's codes (issue 448)", () => {
  it("names what each code is and where it reads", () => {
    expect(codeLabel(barcode)).toBe("Barcode");
    expect(codeLabel(counterCode)).toBe("Weighed-item code at Juniper Market");
    expect(codeLabel(counterCode, true)).toBe("weighed-item code at Juniper Market");
  });

  it("forgets a store code after asking, with focus on Cancel", async () => {
    let codes = [barcode, counterCode];
    let forgotten = 0;
    mockApi(
      routes(() => codes, {
        [`DELETE /products/${flourProductId}/codes/${counterCode.id}`]: () => {
          forgotten += 1;
          codes = [barcode];
          return new Response(null, { status: 204 });
        },
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);
    await screen.findByRole("heading", { name: "Codes" });
    expect(screen.getByText("Changed in Edit product")).toBeInTheDocument();
    // The barcode has no Forget; the counter code has one.
    const forgetButtons = screen.getAllByRole("button", { name: "Forget" });
    expect(forgetButtons).toHaveLength(1);

    await user.click(forgetButtons[0]);
    const ask = screen.getByRole("group", { name: "Forget 12345" });
    expect(within(ask).getByText(/Lines it already identified keep this product/)).toBeVisible();
    expect(within(ask).getByRole("button", { name: "Cancel" })).toHaveFocus();
    expect(forgotten).toBe(0);

    await user.click(within(ask).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("group", { name: "Forget 12345" })).not.toBeInTheDocument();
    // Focus goes back to the row's Forget.
    expect(screen.getByRole("button", { name: "Forget" })).toHaveFocus();

    await user.click(screen.getByRole("button", { name: "Forget" }));
    await user.click(
      within(screen.getByRole("group", { name: "Forget 12345" })).getByRole("button", {
        name: "Forget",
      }),
    );
    await waitFor(() => expect(forgotten).toBe(1));
    expect(await screen.findByText("Forgot 12345.")).toBeInTheDocument();
    await waitFor(() => expect(screen.queryByText("12345")).not.toBeInTheDocument());
    // The row is gone, so focus goes to the card's heading.
    expect(screen.getByRole("heading", { name: "Codes" })).toHaveFocus();
  });

  it("keeps the card and its confirmation after the last code is forgotten", async () => {
    let codes = [counterCode];
    mockApi(
      routes(() => codes, {
        [`DELETE /products/${flourProductId}/codes/${counterCode.id}`]: () => {
          codes = [];
          return new Response(null, { status: 204 });
        },
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);
    await user.click(await screen.findByRole("button", { name: "Forget" }));
    await user.click(
      within(screen.getByRole("group", { name: "Forget 12345" })).getByRole("button", {
        name: "Forget",
      }),
    );
    expect(await screen.findByText("This product has no codes now.")).toBeInTheDocument();
    expect(screen.getByText("Forgot 12345.")).toHaveAttribute("role", "status");
  });

  it("shows no card for a product with no codes", async () => {
    mockApi(routes(() => []));
    renderApp(`/catalog/products/${flourProductId}`);
    await screen.findByRole("heading", { name: "Edit product" });
    expect(screen.queryByRole("heading", { name: "Codes" })).not.toBeInTheDocument();
  });
});
