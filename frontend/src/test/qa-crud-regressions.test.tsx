// Regression: ISSUE-010 and ISSUE-012 — what the page says after a deactivate or a revoke
// Found by /qa on 2026-09-28
// Report: .gstack/qa-reports/qa-report-localhost-2026-09-28.md
import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { VendorLocation } from "../api/geo";
import type { ApiToken } from "../api/types";
import { homeBase, marketLocation, marketVendor } from "./geo-fixtures";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

describe("after a deactivate or a revoke", () => {
  it("does not call a vendor with only a deactivated location one with none yet", async () => {
    const inactive: VendorLocation = { ...marketLocation, active: false };
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
      [`GET /vendors/${marketVendor.id}`]: () => jsonResponse(200, marketVendor),
      "GET /vendor-locations": (call: RecordedCall) =>
        jsonResponse(200, { items: call.query.get("include_inactive") === "true" ? [inactive] : [] }),
    });
    renderApp(`/catalog/vendors/${marketVendor.id}`);

    expect(await screen.findByText(/No active locations, so this vendor cannot be chosen for a purchase\. One is deactivated/)).toBeInTheDocument();
    expect(screen.queryByText(/No locations yet/)).not.toBeInTheDocument();
  });

  it("shows the lookup's error rather than a wrong empty state", async () => {
    // Review of #76: while the inactive lookup failed, the page fell back to
    // "No locations yet" for a vendor that has a deactivated one.
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
      [`GET /vendors/${marketVendor.id}`]: () => jsonResponse(200, marketVendor),
      "GET /vendor-locations": (call: RecordedCall) =>
        call.query.get("include_inactive") === "true" ? errorResponse(500, "internal", "Lookup failed.") : jsonResponse(200, { items: [] }),
    });
    renderApp(`/catalog/vendors/${marketVendor.id}`);

    expect(await screen.findByText(/Lookup failed/)).toBeInTheDocument();
    expect(screen.queryByText(/No locations yet/)).not.toBeInTheDocument();
  });

  it("takes a new token's plaintext off the screen when that token is revoked", async () => {
    const created: ApiToken = { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5acc", name: "tablet", created_at: "2026-02-03T04:05:06Z", last_used_at: null, revoked_at: null };
    let items: ApiToken[] = [];
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /api-tokens": () => jsonResponse(200, { items }),
      "POST /api-tokens": () => {
        items = [created];
        return jsonResponse(201, { token: created, plaintext: "kerp_example_plaintext" });
      },
      [`POST /api-tokens/${created.id}/revoke`]: () => {
        items = [{ ...created, revoked_at: "2026-02-03T05:00:00Z" }];
        return jsonResponse(200, items[0]);
      },
    });
    const user = userEvent.setup();
    renderApp("/settings/tokens");

    await user.type(await screen.findByLabelText("Name"), "tablet");
    await user.click(screen.getByRole("button", { name: "Create token" }));
    expect(await screen.findByTestId("token-plaintext")).toHaveTextContent("kerp_example_plaintext");

    await user.click(await screen.findByRole("button", { name: "Revoke tablet" }));
    await user.click(screen.getByRole("button", { name: "Confirm revoke" }));
    await waitFor(() => expect(screen.queryByTestId("token-plaintext")).not.toBeInTheDocument());
  });
});
