import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Product } from "../api/catalog";
import type { Purchase } from "../api/purchases";
import { flourProduct, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mainRegion, mockApi, renderApp, type RecordedCall } from "./helpers";
import { manualPurchase, purchaseId } from "./purchase-fixtures";

/**
 * Small things a person notices (issues 246 and 247, QA 2026-10-08): touch
 * targets on a purchase's page, the Voided sentence, Needs you rows, Home's
 * Capture, a purchase's status wording, and an empty No category list.
 */

const voidedPurchase: Purchase = { ...manualPurchase, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8099", status: "voided" };

function mountPurchases(withVoided: boolean) {
  mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /purchases": (call: RecordedCall) => {
      const status = call.query.get("status");
      if (status === "voided") return jsonResponse(200, { items: withVoided ? [voidedPurchase] : [], next_cursor: null });
      if (status === "draft") return jsonResponse(200, { items: [], next_cursor: null });
      return jsonResponse(200, { items: [manualPurchase], next_cursor: null });
    },
    "GET /ingest-jobs": () => jsonResponse(200, { items: [], next_cursor: null }),
  });
  renderApp("/shop/purchases");
}

describe("Purchases mentions removed purchases only when it links to them", () => {
  it("says nothing about Voided when nothing was removed", async () => {
    mountPurchases(false);
    await screen.findByRole("heading", { level: 1, name: "Purchases" });
    await screen.findByText(/Everything you've bought/);
    expect(screen.queryByText(/Removed purchases/)).not.toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Show voided/ })).not.toBeInTheDocument();
  });

  it("points to Show voided when there is something to show", async () => {
    mountPurchases(true);
    await screen.findByRole("link", { name: /Show voided/ });
    expect(screen.getByText(/Removed purchases are under Show voided\./)).toBeInTheDocument();
  });
});

describe("a committed purchase's page", () => {
  function mountPurchase(purchase: Purchase) {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      [`GET /purchases/${purchaseId}`]: () => jsonResponse(200, purchase),
    });
    renderApp(`/shop/purchases/${purchaseId}`);
  }

  it("says how it was entered, and what Edit and Reopen each do", async () => {
    mountPurchase(manualPurchase);
    await screen.findByRole("table", { name: "Lines" });
    const main = mainRegion();
    expect(within(main).getByText("· entered by hand")).toBeInTheDocument();
    expect(within(main).queryByText("Manual")).not.toBeInTheDocument();
    expect(within(main).getByText(/Edit corrects it here/)).toBeInTheDocument();
    expect(within(main).getByText(/Reopen sends it back to review/)).toBeInTheDocument();
  });

  it("names a receipt's origin and only explains Reopen", async () => {
    mountPurchase({ ...manualPurchase, source: "receipt" });
    await screen.findByRole("table", { name: "Lines" });
    const main = mainRegion();
    expect(within(main).getByText("· read from a receipt")).toBeInTheDocument();
    expect(within(main).queryByText(/Edit corrects it here/)).not.toBeInTheDocument();
    expect(within(main).getByText(/Reopen sends it back to review, where you can correct its lines/)).toBeInTheDocument();
  });

  it("gives the location and product links a 44px hit area below lg (issue 246)", async () => {
    mountPurchase(manualPurchase);
    const table = await screen.findByRole("table", { name: "Lines" });
    const vendor = within(mainRegion()).getByRole("link", { name: manualPurchase.vendor_location!.vendor.name });
    expect(vendor).toHaveClass("min-h-11", "lg:min-h-0");
    for (const link of within(table).getAllByRole("link")) {
      if (link.getAttribute("href")?.startsWith("/catalog/products/")) expect(link).toHaveClass("min-h-11", "lg:min-h-0");
    }
  });
});

describe("Home", () => {
  it("has one primary Capture: the header's is secondary", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /vendor-locations": () => jsonResponse(200, { items: [] }),
      "GET /purchases": () => jsonResponse(200, { items: [manualPurchase], next_cursor: null }),
      "GET /inbox": () =>
        jsonResponse(200, {
          items: [
            {
              kind: "link",
              title: "3 ingredients to link",
              detail: "Standard names are waiting.",
              action_label: "Review",
              action_route: "/catalog/ingredients/link",
              created_at: "2026-10-01T00:00:00Z",
              error_code: null,
            },
          ],
          reading: { count: 0, oldest_at: null, stalled: false },
        }),
    });
    renderApp("/");
    const header = (await screen.findByRole("heading", { level: 1 })).closest("header") ?? mainRegion();
    const capture = within(header).getByRole("button", { name: "Capture" });
    expect(capture.className).not.toMatch(/bg-blue-600/);
    // Needs you rows size the badge column to the badges shown, with no fixed slot (UI-6.1).
    const list = await screen.findByRole("list", { name: "Needs you" });
    expect(list.className).toContain("sm:grid-cols-[max-content_1fr_auto]");
    const row = within(list).getByTestId("inbox-item");
    expect(row.className).not.toContain("7rem");
    expect(row.className).toContain("sm:grid-cols-subgrid");
  });
});

describe("Products, filtered to No category with nothing in it", () => {
  it("says every product has a category", async () => {
    const labelled: Product = flourProduct;
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /units": () => jsonResponse(200, { items: units }),
      "GET /products": (call: RecordedCall) =>
        jsonResponse(200, { items: call.query.get("category") === "none" ? [] : [labelled], next_cursor: null }),
      "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    renderApp("/catalog/products?category=none");
    expect(await screen.findByText("Every product has a category")).toBeInTheDocument();
    expect(screen.queryByText(/No products match without a category/)).not.toBeInTheDocument();
  });
});
