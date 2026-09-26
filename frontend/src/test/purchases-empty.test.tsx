import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";
import { manualPurchase } from "./purchase-fixtures";

function routes() {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /inbox": () => jsonResponse(200, { items: [], reading: { count: 0, oldest_at: null, stalled: false } }),
    "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
    "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
  };
}

describe("purchases: empty states (G11, UI-3.11)", () => {
  it("offers both ways in when there are no purchases at all", async () => {
    mockApi(routes());
    renderApp("/shop/purchases");

    const empty = await screen.findByRole("region", { name: "No purchases yet" });
    expect(within(empty).getByRole("link", { name: "Scan a receipt" })).toHaveAttribute("href", "/shop/receipts");
    expect(within(empty).getByRole("link", { name: "New purchase" })).toHaveAttribute("href", "/shop/purchases/new");
  });

  it("says a filter found nothing, and clears it, rather than claiming there are no purchases", async () => {
    const calls = mockApi(routes());
    const user = userEvent.setup();
    renderApp("/shop/purchases");
    await screen.findByRole("region", { name: "No purchases yet" });

    await user.click(within(screen.getByRole("group", { name: "Status" })).getByRole("button", { name: /^Drafts/ }));
    const empty = await screen.findByRole("region", { name: "No draft purchases" });
    expect(screen.queryByRole("region", { name: "No purchases yet" })).toBeNull();
    expect(calls.some((c) => c.query.get("status") === "draft")).toBe(true);

    await user.click(within(empty).getByRole("button", { name: "Show all" }));
    expect(within(screen.getByRole("group", { name: "Status" })).getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByRole("region", { name: "No purchases yet" })).toBeInTheDocument();
  });

  it("brings the purchases back when Show all clears a filter that found nothing", async () => {
    mockApi({
      ...routes(),
      // Committed purchases exist; there are no drafts.
      "GET /purchases": (call: RecordedCall) =>
        call.query.get("status") === "draft" ? jsonResponse(200, { items: [], next_cursor: null }) : jsonResponse(200, { items: [manualPurchase], next_cursor: null }),
    });
    const user = userEvent.setup();
    renderApp("/shop/purchases");
    expect(await screen.findByRole("table", { name: "Purchases" })).toBeInTheDocument();

    await user.selectOptions(screen.getByLabelText("Status"), "draft");
    const empty = await screen.findByRole("region", { name: "No draft purchases" });
    expect(screen.queryByRole("table")).toBeNull();

    await user.click(within(empty).getByRole("button", { name: "Show all" }));
    const table = await screen.findByRole("table", { name: "Purchases" });
    expect(within(table).getByText("Pier Farmers Market")).toBeInTheDocument();
  });
});
