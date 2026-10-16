import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { InboxItem } from "../api/inbox";
import type { OlderRules } from "../api/rereads";
import { adminUser, jsonResponse, mockApi, renderApp, type RouteHandler } from "./helpers";

// Spec 10, "Re-check older readings", and 09's inbox row (04, 2S; issue 382).
// Every store and wording is invented.

const purchaseA = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fa001";
const purchaseB = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fa002";

const before: OlderRules = { rules_version: 1, older: 3, ready: [], checked: null };

const after: OlderRules = {
  rules_version: 1,
  older: 0,
  ready: [
    {
      reread_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fb001",
      purchase_id: purchaseA,
      purchased_at: "2026-10-03T17:00:00Z",
      store: "Lantern Row Grocery",
      counts: { changed: 2, added: 1, removed: 0, kept: 0 },
      adds_up: true,
      off_by: null,
    },
    {
      reread_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4fb002",
      purchase_id: purchaseB,
      purchased_at: "2026-10-05T17:00:00Z",
      store: null,
      counts: { changed: 0, added: 0, removed: 1, kept: 2 },
      adds_up: false,
      off_by: "0.40",
    },
  ],
  checked: { ready: 2, no_changes: 1, skipped: 0 },
};

const olderRow: InboxItem = {
  kind: "older_rules",
  title: "3 drafts were read with older rules",
  detail: "Re-checking is instant and asks no model. Nothing changes until you apply it.",
  action_label: "Re-check them",
  action_route: "/shop/receipts/recheck",
  created_at: "2026-10-03T17:00:00Z",
};

function routes(more: Record<string, RouteHandler> = {}) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /inbox": () =>
      jsonResponse(200, {
        items: [olderRow],
        reading: { count: 0, oldest_at: null, stalled: false },
      }),
    ...more,
  };
}

describe("re-check drafts read with older rules (issue 382)", () => {
  it("shows one inbox row that opens the re-check page", async () => {
    mockApi(routes());
    renderApp("/");
    const [row] = await screen.findAllByTestId("inbox-item");
    expect(within(row).getByText("3 drafts were read with older rules")).toBeInTheDocument();
    expect(within(row).getByText("Re-read")).toBeInTheDocument();
    expect(within(row).getByRole("link", { name: "Re-check them" })).toHaveAttribute(
      "href",
      "/shop/receipts/recheck",
    );
  });

  it("re-checks them and lists the drafts whose lines would change", async () => {
    let state = before;
    const calls = mockApi(
      routes({
        "GET /rereads/older-rules": () => jsonResponse(200, state),
        "POST /rereads/older-rules/recheck": () => {
          state = after;
          return jsonResponse(200, after);
        },
      }),
    );
    const user = userEvent.setup();
    renderApp("/shop/receipts/recheck");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Re-check older readings" }),
    ).toBeInTheDocument();
    expect(await screen.findByText(/3 drafts were read with older rules/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Re-check 3 drafts" }));
    expect(
      calls.some((c) => c.method === "POST" && c.path === "/rereads/older-rules/recheck"),
    ).toBe(true);
    expect(
      await screen.findByText("Re-checked 3 drafts: 2 would change, 1 already matches."),
    ).toBeInTheDocument();

    const list = screen.getByRole("list", { name: "Drafts whose lines would change" });
    const rows = within(list).getAllByRole("listitem");
    expect(
      within(rows[0]).getByText("Lantern Row Grocery receipt from Oct 3, 2026"),
    ).toBeInTheDocument();
    expect(within(rows[0]).getByText("2 changed, 1 added · Adds up after")).toBeInTheDocument();
    expect(within(rows[0]).getByRole("link", { name: /Review/ })).toHaveAttribute(
      "href",
      `/shop/purchases/${purchaseA}/reread`,
    );
    expect(within(rows[1]).getByText("1 removed · Still off by $0.40 after")).toBeInTheDocument();
    // Nothing left to re-check: the button goes, the list stays.
    expect(screen.queryByRole("button", { name: /Re-check \d/ })).toBeNull();
  });

  it("says when nothing waits for a re-check", async () => {
    mockApi(
      routes({
        "GET /rereads/older-rules": () =>
          jsonResponse(200, { rules_version: 1, older: 0, ready: [], checked: null }),
      }),
    );
    renderApp("/shop/receipts/recheck");
    expect(await screen.findByText("No draft waits for a re-check")).toBeInTheDocument();
  });

  it("shows an error, never an empty page, when the count fails", async () => {
    mockApi(
      routes({
        "GET /rereads/older-rules": () =>
          jsonResponse(500, { error: { code: "internal", message: "Something went wrong." } }),
      }),
    );
    renderApp("/shop/receipts/recheck");
    expect(await screen.findByText("Something went wrong.")).toBeInTheDocument();
    expect(screen.queryByText("No draft waits for a re-check")).toBeNull();
  });
});
