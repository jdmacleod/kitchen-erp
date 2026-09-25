import { QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { describe, expect, it } from "vitest";
import { NewPurchasePage } from "../pages/purchases/NewPurchasePage";
import { PurchaseDetailPage } from "../pages/purchases/PurchaseDetailPage";
import { createQueryClient } from "../lib/queryClient";
import { chainLocation } from "./geo-fixtures";
import { manualPurchase } from "./purchase-fixtures";
import { adminUser, jsonResponse, mainRegion, mockApi, renderApp } from "./helpers";

/**
 * `renderApp` mounts a MemoryRouter, which keeps its history in memory and never
 * touches `window.history`. Asserting on `window.history.state` there passes
 * whether or not the flag was ever stripped, so the strip is checked by reading
 * the router's own location through a probe mounted beside the page.
 */
function StateProbe() {
  return <div data-testid="router-state">{JSON.stringify(useLocation().state)}</div>;
}

function renderWithProbe(entry: string, element: React.ReactNode, pattern = entry) {
  const client = createQueryClient({ retry: false });
  return render(
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[{ pathname: entry, state: { firstPurchase: true } }]}>
        <Routes>
          <Route
            path={pattern}
            element={
              <>
                {element}
                <StateProbe />
              </>
            }
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
}

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
    expect(action).toHaveAttribute("href", "/shop/purchases/new");
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

  it("clears the flag off the detail page's own entry once shown", async () => {
    // A reload or a back-navigation replays this entry. If the flag were still
    // on it, the same purchase would be congratulated again.
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      [`GET /purchases/${manualPurchase.id}`]: () => jsonResponse(200, manualPurchase),
    });
    renderWithProbe(`/purchases/${manualPurchase.id}`, <PurchaseDetailPage />, "/purchases/:id");

    expect(await screen.findByText(ACK)).toBeInTheDocument();
    await waitFor(() => expect(screen.getByTestId("router-state")).toHaveTextContent("null"));
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
    renderApp("/shop/purchases/new");

    await screen.findByRole("button", { name: "Save purchase" });
    await waitFor(() => expect(calls.some((c) => c.path.startsWith("/vendor-locations"))).toBe(true));
    expect(calls.filter((c) => c.path.startsWith("/purchases"))).toHaveLength(0);
  });

  it("clears the flag off the entry form's own entry too", async () => {
    // Saving pushes a new entry for the purchase, so Back lands on this form
    // again. If the flag were still on this entry, the next purchase entered
    // here would be congratulated as though it were the first.
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation] }),
      "GET /units": () => jsonResponse(200, { items: [] }),
    });
    renderWithProbe("/purchases/new", <NewPurchasePage />);

    await screen.findByRole("button", { name: "Save purchase" });
    await waitFor(() => expect(screen.getByTestId("router-state")).toHaveTextContent("null"));
  });

  it("does not return when a reopened purchase is committed again", async () => {
    // The acknowledgement belongs to the first commit, not to every commit of
    // this purchase. Without retiring it on reopen, `firstPurchase` stays true
    // for the life of the mount and the recommit congratulates twice.
    let status: "committed" | "reviewed" = "committed";
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      [`GET /purchases/${manualPurchase.id}`]: () => jsonResponse(200, { ...manualPurchase, status }),
      [`POST /purchases/${manualPurchase.id}/reopen`]: () => {
        status = "reviewed";
        return jsonResponse(200, { ...manualPurchase, status: "reviewed" });
      },
    });
    const user = userEvent.setup();
    const { client } = renderApp({
      pathname: `/purchases/${manualPurchase.id}`,
      state: { firstPurchase: true },
    });

    expect(await screen.findByText(ACK)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Reopen" }));
    await waitFor(() => expect(screen.queryByText(ACK)).not.toBeInTheDocument());

    // Commit it again. The household has not earned a second congratulation.
    status = "committed";
    await act(async () => {
      await client.invalidateQueries();
    });
    expect(await screen.findByRole("button", { name: "Reopen" })).toBeInTheDocument();
    expect(screen.queryByText(ACK)).not.toBeInTheDocument();
  });

  it("names the milestone rather than the record", async () => {
    mountDetail({ firstPurchase: true });

    await screen.findByText(ACK);
    expect(mainRegion()).toHaveTextContent(/price book/i);
  });
});
