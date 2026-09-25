import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

function routes() {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /inbox": () => jsonResponse(200, { items: [], reading: { count: 0, oldest_at: null, stalled: false } }),
    "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
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

    await user.selectOptions(screen.getByLabelText("Status"), "draft");
    const empty = await screen.findByRole("region", { name: "No draft purchases" });
    expect(screen.queryByRole("region", { name: "No purchases yet" })).toBeNull();
    expect(calls.some((c) => c.query.get("status") === "draft")).toBe(true);

    await user.click(within(empty).getByRole("button", { name: "Show all" }));
    expect(screen.getByLabelText("Status")).toHaveValue("");
    expect(await screen.findByRole("region", { name: "No purchases yet" })).toBeInTheDocument();
  });
});
