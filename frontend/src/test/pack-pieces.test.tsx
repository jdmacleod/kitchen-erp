import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { formatPack, formatPieces, type Product } from "../api/catalog";
import { flourProduct, flourProductId, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

// Pieces in a pack (2026-10-05): a weighed or measured pack may say how many pieces it holds.

describe("pieces in a pack", () => {
  it("shows a pack with its pieces", () => {
    expect(formatPack("14", "oz", 4, "link")).toBe("14 oz · 4 links");
    expect(formatPack("1.500", "kg", null, null)).toBe("1.5 kg");
    expect(formatPieces(1, "link")).toBe("1 link");
    expect(formatPieces(6, null)).toBe("6 pieces");
    expect(formatPieces(3, "patty")).toBe("3 patties");
    expect(formatPieces(2, "box")).toBe("2 boxes");
  });

  it("offers pieces only for a weighed or measured pack, and saves them", async () => {
    let product: Product = { ...flourProduct, pack_qty: "14", pack_unit: "oz", pack_count: null, piece_name: null };
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      [`GET /products/${flourProductId}`]: () => jsonResponse(200, product),
      [`GET /products/${flourProductId}/prices`]: () => jsonResponse(200, { points: [], latest: [] }),
      [`PATCH /products/${flourProductId}`]: () => {
        product = { ...product, pack_count: 4, piece_name: "link", updated_at: "2026-03-03T00:00:00Z" };
        return jsonResponse(200, product);
      },
    });
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);
    const form = await screen.findByRole("form", { name: "Edit product" });

    await user.type(await within(form).findByLabelText("Pieces in the pack (optional)"), "4");
    await user.type(within(form).getByLabelText("Piece name (optional)"), "Link");
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH")).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ pack_count: 4, piece_name: "link" });

    // A pack counted in pieces already says how many: the fields go away.
    const saved = await screen.findByRole("form", { name: "Edit product" });
    await waitFor(() => expect(within(saved).getByLabelText("Pieces in the pack (optional)")).toHaveValue("4"));
    await user.selectOptions(within(saved).getByLabelText("Pack unit"), "each");
    expect(within(saved).queryByLabelText("Pieces in the pack (optional)")).not.toBeInTheDocument();
  });

  it("asks for a whole number of pieces", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      [`GET /products/${flourProductId}`]: () => jsonResponse(200, flourProduct),
      [`GET /products/${flourProductId}/prices`]: () => jsonResponse(200, { points: [], latest: [] }),
    });
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);
    const form = await screen.findByRole("form", { name: "Edit product" });
    await user.type(await within(form).findByLabelText("Pieces in the pack (optional)"), "4.5");
    await user.click(within(form).getByRole("button", { name: "Save changes" }));
    expect(await within(form).findByText("Pieces must be a whole number, like 5.")).toBeInTheDocument();
  });
});
