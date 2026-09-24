import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { chainLocation } from "./geo-fixtures";
import { manualPurchase } from "./purchase-fixtures";
import { adminUser, jsonResponse, mainRegion, mockApi, renderApp } from "./helpers";

/**
 * Design decision 9C: the first purchase is acknowledged where it is earned,
 * because a new owner may not return to the home page for days.
 *
 * The flag is a literal on the checklist's step-two link, not an answer from the
 * server. That link renders only while no committed purchase exists, so it is
 * true by construction — and the weekly entry form pays nothing for it. It has to
 * travel, because `NewPurchasePage` unmounts on success, and it has to be
 * consumed once, or a reload congratulates the same purchase again.
 *
 *   checklist step 2 --state--> NewPurchasePage --state--> PurchaseDetailPage
 */

const ACK = /that is your kitchen set up/i;

function mountDetail(state?: Record<string, unknown>) {
  mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    [`GET /purchases/${manualPurchase.id}`]: () => jsonResponse(200, manualPurchase),
  });
  return renderApp({ pathname: `/purchases/${manualPurchase.id}`, state });
}

describe("first-purchase acknowledgement", () => {
  it("rides the checklist's step-two link into the entry form", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation] }),
      "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
      "GET /to-identify": () => jsonResponse(200, { items: [] }),
      "GET /price-book/needs-bridge": () => jsonResponse(200, { items: [] }),
      "GET /units": () => jsonResponse(200, { items: [] }),
    });
    const user = userEvent.setup();
    renderApp("/");

    // Step two, on a deployment that has a location but no committed purchase.
    const action = await screen.findByRole("link", { name: "New purchase" });
    expect(action).toHaveAttribute("href", "/purchases/new");
    await user.click(action);

    // The flag rides in history state, not the URL, so there is nothing to read
    // off the address bar. What the form must do is render, unchanged.
    expect(await screen.findByRole("button", { name: "Save purchase" })).toBeInTheDocument();
  });

  it("shows on the purchase it was earned on", async () => {
    mountDetail({ firstPurchase: true });

    expect(await screen.findByText(ACK)).toBeInTheDocument();
  });

  it("does not show on an ordinary visit", async () => {
    mountDetail();

    await screen.findByRole("heading", { level: 1 });
    expect(screen.queryByText(ACK)).not.toBeInTheDocument();
  });

  it("is stripped from history once shown, so a reload does not repeat it", async () => {
    mountDetail({ firstPurchase: true });
    expect(await screen.findByText(ACK)).toBeInTheDocument();

    // The effect clears the entry immediately. A reload or a back-navigation
    // replays this history entry, and it must come up clean.
    await waitFor(() => expect(window.history.state?.usr ?? null).toBeNull());
  });

  it("adds no request to the weekly entry form", async () => {
    // D12 refused to let a one-time onboarding fix slow this screen down. The
    // acknowledgement is held to the same rule: nothing asks "is this the first".
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation] }),
      "GET /units": () => jsonResponse(200, { items: [] }),
    });
    renderApp("/purchases/new");

    await screen.findByRole("button", { name: "Save purchase" });
    await waitFor(() => expect(calls.some((c) => c.path.startsWith("/vendor-locations"))).toBe(true));
    expect(calls.filter((c) => c.path.startsWith("/purchases"))).toHaveLength(0);
  });

  it("names the milestone rather than the record", async () => {
    mountDetail({ firstPurchase: true });

    await screen.findByText(ACK);
    expect(mainRegion()).toHaveTextContent(/price book/i);
  });
});
