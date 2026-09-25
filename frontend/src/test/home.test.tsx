import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { chainLocation } from "./geo-fixtures";
import { manualPurchase } from "./purchase-fixtures";
import type { Inbox, InboxItem } from "../api/inbox";
import { ingestErrorText } from "../lib/ingestErrors";
import { adminUser, errorResponse, jsonResponse, mainRegion, mockApi, renderApp, type RecordedCall, type RouteHandler } from "./helpers";

/**
 * The landing route. Issue #15 reported that a new deployment lands on a form for
 * a thing there is no reason to add yet, and that the first thing a new owner
 * actually tries is a dead end.
 *
 * Two things these tests exist to hold down, beyond "the page renders":
 *
 *   - Only a *committed* purchase completes setup. Receipt parsing inserts a
 *     draft the moment a photo is read, so an unfiltered probe would mark a
 *     household as finished on the strength of an upload nobody reviewed.
 *   - Loading and errored are not empty. Neither may render the checklist, or an
 *     established household is told to go and add a shop it already has.
 */

const NEVER = () => new Promise<Response>(() => {}) as unknown as Response;

const CHECKLIST_STEP = "Add somewhere you shop";

interface Routes {
  locations?: RouteHandler;
  purchases?: RouteHandler;
  inbox?: RouteHandler;
}

const quietInbox = { items: [], reading: { count: 0, oldest_at: null, stalled: false } };

function mount({ locations, purchases, inbox }: Routes = {}): RecordedCall[] {
  const calls = mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /vendor-locations": locations ?? (() => jsonResponse(200, { items: [] })),
    "GET /purchases": purchases ?? (() => jsonResponse(200, { items: [], next_cursor: null })),
    "GET /inbox": inbox ?? (() => jsonResponse(200, quietInbox)),
  });
  renderApp("/");
  return calls;
}

/** A set-up household: a location and a committed purchase. */
function mountSetUp(inbox?: RouteHandler): RecordedCall[] {
  return mount({
    locations: () => jsonResponse(200, { items: [chainLocation] }),
    purchases: () => jsonResponse(200, { items: [manualPurchase], next_cursor: null }),
    inbox,
  });
}

function item(overrides: Partial<InboxItem> = {}): InboxItem {
  return {
    kind: "receipt",
    title: "Finish the Sep 24 receipt",
    detail: "4 lines ready to review and commit.",
    action_label: "Review",
    action_route: "/shop/purchases/0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8001",
    created_at: "2026-09-24T18:00:00Z",
    ...overrides,
  };
}

function inboxOf(items: InboxItem[], reading: Partial<Inbox["reading"]> = {}): RouteHandler {
  return () => jsonResponse(200, { items, reading: { count: 0, oldest_at: null, stalled: false, ...reading } });
}

describe("home page, first run, with the inbox", () => {
  it("puts inbox rows above the checklist when there are any (G7)", async () => {
    mount({ inbox: inboxOf([item()]) });

    await screen.findByRole("heading", { name: "Set up your kitchen" });
    const row = await screen.findByTestId("inbox-item");
    const step = screen.getByText(CHECKLIST_STEP);
    expect(row.compareDocumentPosition(step) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it("says nothing about an empty inbox on a new kitchen", async () => {
    mount();
    await screen.findByRole("heading", { name: "Set up your kitchen" });
    expect(screen.queryByText("Nothing needs you")).not.toBeInTheDocument();
  });
});

describe("home page, first run", () => {
  it("shows both steps, neither done, on an empty deployment", async () => {
    mount();

    expect(await screen.findByRole("heading", { name: "Set up your kitchen" })).toBeInTheDocument();
    const steps = screen.getAllByRole("listitem").filter((li) => li.textContent?.includes("To do"));
    expect(steps).toHaveLength(2);
    expect(screen.getByText(CHECKLIST_STEP)).toBeInTheDocument();
    expect(screen.getByText("Record your first purchase")).toBeInTheDocument();
  });

  it("deep-links step one into placing mode", async () => {
    // MapView ignores clicks unless it is placing, so a bare /map would be the
    // same dead end one screen later.
    mount();

    expect(await screen.findByRole("link", { name: "Open the map" })).toHaveAttribute(
      "href",
      "/catalog/vendors?view=map&place=location",
    );
  });

  it("ticks step one once a location exists, and leaves step two open", async () => {
    mount({ locations: () => jsonResponse(200, { items: [chainLocation] }) });

    await screen.findByRole("heading", { name: "Set up your kitchen" });
    // Completion is not carried by colour: the word is in the accessible name.
    const done = screen.getAllByRole("listitem").filter((li) => li.textContent?.includes("Done"));
    expect(done).toHaveLength(1);
    expect(done[0].textContent).toContain(CHECKLIST_STEP);

    const open = screen.getAllByRole("listitem").filter((li) => li.textContent?.includes("To do"));
    expect(open).toHaveLength(1);
    expect(open[0].textContent).toContain("Record your first purchase");
  });

  it("asks only for committed purchases, so an unreviewed receipt does not finish setup", async () => {
    // The defect this replaced: GET /purchases with no status filter returns the
    // draft that receipt parsing inserts, and the checklist would vanish.
    const calls = mount();

    await screen.findByRole("heading", { name: "Set up your kitchen" });
    const purchaseCalls = calls.filter((c) => c.path.startsWith("/purchases"));
    expect(purchaseCalls).toHaveLength(1);
    expect(purchaseCalls[0].query.get("status")).toBe("committed");
  });
});

describe("home page, set up", () => {
  it("greets without a name, summarizes, and shows no checklist", async () => {
    mountSetUp(inboxOf([item(), item({ kind: "identify", title: "23 receipt lines to identify", action_label: "Review lines", action_route: "/shop/receipts/identify" })]));

    const heading = await within(await screen.findByRole("main")).findByRole("heading", { level: 1 });
    expect(heading.textContent).toMatch(/^Good (morning|afternoon|evening)$/);
    expect(await within(await screen.findByRole("main")).findByText("2 things need you")).toBeInTheDocument();
    expect(screen.queryByText(CHECKLIST_STEP)).not.toBeInTheDocument();
    expect(within(mainRegion()).getByRole("button", { name: "Capture" })).toBeInTheDocument();
  });

  it("lists inbox rows with a kind badge, the title, and an action that opens the fix", async () => {
    mountSetUp(inboxOf([item(), item({ kind: "identify", title: "23 receipt lines to identify", detail: "Match them once.", action_label: "Review lines", action_route: "/shop/receipts/identify" })]));

    const rows = await screen.findAllByTestId("inbox-item");
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent("Receipt");
    expect(rows[0]).toHaveTextContent("Finish the Sep 24 receipt");
    expect(within(rows[0]).getByRole("link", { name: "Review" })).toHaveAttribute("href", "/shop/purchases/0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8001");
    // An aggregate row carries its size in its title (G6).
    expect(rows[1]).toHaveTextContent("23 receipt lines to identify");
    expect(within(rows[1]).getByRole("link", { name: "Review lines" })).toHaveAttribute("href", "/shop/receipts/identify");
  });

  it("marks a failed read in tomato and says what went wrong (G5)", async () => {
    mountSetUp(
      inboxOf([
        item({ kind: "receipt_failed", title: "A receipt couldn't be read", detail: "Retry it, or enter it by hand.", action_label: "Open receipt", action_route: "/shop/receipts?job=j1", error_code: "no_ocr_text" }),
      ]),
    );

    const [row] = await screen.findAllByTestId("inbox-item");
    expect(row).toHaveTextContent(ingestErrorText("no_ocr_text").message);
    const badge = within(row).getByText("Couldn't read");
    expect(badge.className).toMatch(/bg-red-100/);
    expect(within(row).getByRole("link", { name: "Open receipt" })).toHaveAttribute("href", "/shop/receipts?job=j1");
  });

  it("says nothing needs you when the inbox is empty, with a way to capture", async () => {
    mountSetUp();

    expect(await within(await screen.findByRole("main")).findByText("Nothing needs you", { selector: "p.font-medium" })).toBeInTheDocument();
    expect(within(mainRegion()).getAllByRole("button", { name: "Capture" }).length).toBeGreaterThan(0);
  });

  it("shows an error, never an empty inbox, when the inbox fails (D6)", async () => {
    mountSetUp(() => errorResponse(500, "internal", "Something went wrong."));

    const alert = await within(await screen.findByRole("main")).findByRole("alert");
    expect(alert).toHaveTextContent("Couldn't load what needs you");
    expect(within(alert).getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(screen.queryByText("Nothing needs you")).not.toBeInTheDocument();
    expect(await screen.findAllByRole("img", { name: "Couldn't check what needs you" })).not.toHaveLength(0);
  });

  it("lists recent purchases in the right column", async () => {
    mountSetUp();
    const recent = await screen.findByRole("region", { name: "Recent purchases" });
    expect(within(recent).getByRole("link", { name: "All purchases" })).toHaveAttribute("href", "/shop/purchases");
  });

  it("shows receipts being read above Needs you, not as rows (G1)", async () => {
    mountSetUp(inboxOf([], { count: 2, oldest_at: "2026-09-25T10:00:00Z" }));
    expect(await within(await screen.findByRole("main")).findByText("Reading 2 receipts…")).toBeInTheDocument();
    expect(screen.queryAllByTestId("inbox-item")).toHaveLength(0);
  });

  it("turns the reading line squash with a way to System once it has stalled (D21)", async () => {
    mountSetUp(inboxOf([], { count: 1, oldest_at: "2026-09-25T10:00:00Z", stalled: true }));
    const line = await within(await screen.findByRole("main")).findByText(/Reading is taking longer than usual/);
    expect(within(line).getByRole("link", { name: "Check System" })).toHaveAttribute("href", "/settings/system");
  });

  it("shows three rows on a phone and expands the rest in place (G6)", async () => {
    const items = [1, 2, 3, 4, 5].map((n) => item({ title: `Finish receipt ${n}`, action_route: `/shop/purchases/${n}` }));
    mountSetUp(inboxOf(items));

    const rows = await screen.findAllByTestId("inbox-item");
    expect(rows.map((r) => r.className.includes("max-lg:hidden"))).toEqual([false, false, false, true, true]);
    await userEvent.click(screen.getByRole("button", { name: "See all 5" }));
    expect(screen.getAllByTestId("inbox-item").some((r) => r.className.includes("max-lg:hidden"))).toBe(false);
  });
});

describe("the Home nav badge", () => {
  it("counts inbox rows, an aggregate once (G6)", async () => {
    mountSetUp(inboxOf([item(), item({ kind: "identify", title: "23 receipt lines to identify" })]));
    expect((await screen.findAllByLabelText("2 things need you")).length).toBeGreaterThan(0);
  });

  it("stays hidden until the inbox answers (G16)", async () => {
    mountSetUp(NEVER);
    await within(await screen.findByRole("main")).findByRole("heading", { level: 1 });
    expect(screen.queryByLabelText(/things? needs? you/)).not.toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "Couldn't check what needs you" })).not.toBeInTheDocument();
  });
});

describe("home page, neither loading nor errored is empty", () => {
  it("shows no checklist while the answers are still coming", async () => {
    mount({ purchases: NEVER });

    const main = await screen.findByRole("main");
    expect(await within(main).findByRole("status")).toHaveTextContent("Loading…");
    expect(screen.queryByText(CHECKLIST_STEP)).not.toBeInTheDocument();
  });

  it("never claims a household is new when the purchase lookup fails", async () => {
    mount({ purchases: () => errorResponse(500, "unavailable", "down") });

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText(CHECKLIST_STEP)).not.toBeInTheDocument();
    expect(mainRegion()).not.toHaveTextContent("Set up your kitchen");
  });

  it("never claims a household is new when the location lookup fails", async () => {
    mount({ locations: () => errorResponse(500, "unavailable", "down") });

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText(CHECKLIST_STEP)).not.toBeInTheDocument();
  });
});
