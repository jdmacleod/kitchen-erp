import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it } from "vitest";
import type { Purchase } from "../api/purchases";
import { hits, units } from "./catalog-fixtures";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { receiptPurchase, receiptPurchaseId, unmatchedLine } from "./purchase-fixtures";

const base = `/purchases/${receiptPurchaseId}`;

function routes(purchase: () => Purchase) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /inbox": () => jsonResponse(200, { items: [], reading: { count: 0, oldest_at: null, stalled: false } }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
    "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    [`GET ${base}`]: () => jsonResponse(200, purchase()),
  };
}

/** A window whose width the test controls; `resize(wide)` crosses lg and tells listeners. */
function controllableWidth(initiallyWide: boolean) {
  let wide = initiallyWide;
  const listeners = new Set<() => void>();
  Object.defineProperty(window, "matchMedia", {
    configurable: true,
    value: (query: string) => ({
      get matches() {
        return wide;
      },
      media: query,
      addEventListener: (_: string, fn: () => void) => listeners.add(fn),
      removeEventListener: (_: string, fn: () => void) => listeners.delete(fn),
    }),
  });
  return (next: boolean) => {
    wide = next;
    act(() => listeners.forEach((fn) => fn()));
  };
}

/** Pretend the window is narrower than lg (1024px), as a phone is. */
function phoneWidth() {
  controllableWidth(false);
}

afterEach(() => {
  delete (window as { matchMedia?: unknown }).matchMedia;
});

const seqs = () => screen.getAllByTestId("review-line").map((el) => el.getAttribute("aria-label")?.match(/^Line (\d+)/)?.[1]);

describe("receipt review: the Needs you filter (spec 10)", () => {
  it("counts and filters to the lines that need a person, in the table", async () => {
    mockApi(routes(() => receiptPurchase));
    const user = userEvent.setup();
    renderApp(base);
    await screen.findByTestId("review");

    // Seq 2 has no product and has suggestions; seq 4 is flagged. The alias
    // match and the discount need nobody.
    const filter = screen.getByRole("group", { name: "Show lines" });
    expect(within(filter).getByRole("button", { name: "All 4" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("table", { name: "Receipt lines" })).toBeInTheDocument();
    expect(seqs()).toEqual(["1", "2", "3", "4"]);

    await user.click(within(filter).getByRole("button", { name: "Needs you 2" }));
    expect(seqs()).toEqual(["2", "4"]);
  });
});

describe("receipt review on a phone (G18, UI-4.10)", () => {
  it("shows one card per line, the lines that need you first", async () => {
    phoneWidth();
    mockApi(routes(() => receiptPurchase));
    renderApp(base);
    await screen.findByTestId("review");

    expect(screen.queryByRole("table")).toBeNull();
    const list = screen.getByRole("list", { name: "Receipt lines" });
    expect(within(list).getAllByTestId("review-line")).toHaveLength(4);
    expect(seqs()).toEqual(["2", "4", "1", "3"]);
  });

  it("accepts a suggestion with its own full-width button", async () => {
    phoneWidth();
    let purchase = receiptPurchase;
    const calls = mockApi({
      ...routes(() => purchase),
      [`POST ${base}/lines/${unmatchedLine.id}/resolve`]: () => {
        purchase = {
          ...purchase,
          lines: purchase.lines.map((l) =>
            l.id === unmatchedLine.id
              ? { ...l, product: { id: hits[1].id, name: hits[1].name, brand: hits[1].brand, pack_qty: hits[1].pack_qty, pack_unit: hits[1].pack_unit, category: null, category_key: null }, resolution: "fuzzy", suggestions: [] }
              : l,
          ),
        };
        return jsonResponse(200, purchase);
      },
    });
    const user = userEvent.setup();
    renderApp(base);
    await screen.findByTestId("review");

    const card = screen.getAllByTestId("review-line")[0];
    const suggestions = within(within(card).getByRole("list", { name: "Suggestions for line 2" })).getAllByRole("button");
    expect(suggestions.map((b) => b.getAttribute("aria-label"))).toEqual(["Accept Riverbend Bread Flour", "Accept Millstone All-Purpose Flour"]);
    expect(suggestions[0].className).toContain("min-h-11");

    await user.click(suggestions[0]);
    const post = calls.find((c) => c.method === "POST" && c.path.endsWith("/resolve"));
    expect(post?.body).toEqual({ product_id: hits[1].id, accepted_kind: "fuzzy" });
    await waitFor(() => expect(within(screen.getByRole("group", { name: "Show lines" })).getByRole("button", { name: "Needs you 1" })).toBeInTheDocument());
  });

  it("gives the product link a 44px target on a card", async () => {
    phoneWidth();
    mockApi(routes(() => receiptPurchase));
    renderApp(base);
    await screen.findByTestId("review");

    const aliasCard = screen.getAllByTestId("review-line").find((el) => el.getAttribute("aria-label")?.startsWith("Line 1:"))!;
    expect(within(aliasCard).getByRole("link").className).toContain("min-h-11");
  });
});

describe("receipt review: review findings", () => {
  it("never lets a shortcut act on a line the filter has hidden", async () => {
    const calls = mockApi({
      ...routes(() => receiptPurchase),
      [`POST ${base}/lines/${receiptPurchase.lines[0].id}/resolve`]: () => jsonResponse(200, receiptPurchase),
      [`POST ${base}/lines/${unmatchedLine.id}/resolve`]: () => jsonResponse(200, receiptPurchase),
    });
    const user = userEvent.setup();
    renderApp(base);
    await screen.findByTestId("review");

    // Line 1 (a quiet alias match) is current, then the filter hides it.
    screen.getAllByTestId("review-line")[0].focus();
    await user.click(within(screen.getByRole("group", { name: "Show lines" })).getByRole("button", { name: "Needs you 2" }));
    await user.keyboard("i");

    const posts = calls.filter((c) => c.method === "POST");
    expect(posts.map((c) => c.path)).toEqual([`${base}/lines/${unmatchedLine.id}/resolve`]);
  });

  it("keeps what was typed in a line editor when the window crosses lg", async () => {
    const resize = controllableWidth(true);
    mockApi(routes(() => receiptPurchase));
    const user = userEvent.setup();
    renderApp(base);
    await screen.findByTestId("review");
    expect(screen.getByRole("table", { name: "Receipt lines" })).toBeInTheDocument();

    const row = screen.getAllByTestId("review-line")[1];
    await user.click(within(row).getByRole("button", { name: "Edit" }));
    const qty = await screen.findByLabelText("Qty");
    await user.clear(qty);
    await user.type(qty, "7");

    resize(false);
    expect(screen.getByLabelText("Qty")).toHaveValue("7");
    // Once the edit ends, the layout follows the window again.
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("table")).toBeNull());
  });
});

describe("receipt review: who resolved a line", () => {
  const byHand = (name: string | null): Purchase => ({
    ...receiptPurchase,
    lines: receiptPurchase.lines.map((l) =>
      l.id === unmatchedLine.id
        ? { ...l, product: { id: hits[1].id, name: hits[1].name, brand: hits[1].brand, pack_qty: hits[1].pack_qty, pack_unit: hits[1].pack_unit, category: null, category_key: null }, resolution: "manual", resolved_by: adminUser.id, resolved_by_name: name, suggestions: [] }
        : l,
    ),
  });
  const line2 = () => screen.getAllByTestId("review-line").find((el) => el.getAttribute("aria-label")?.startsWith("Line 2:"))!;

  it("names the person, never their id", async () => {
    mockApi(routes(() => byHand("Admin")));
    renderApp(base);
    await screen.findByTestId("review");

    expect(line2()).toHaveTextContent("by Admin");
    expect(line2()).not.toHaveTextContent(adminUser.id);
  });

  it("says nothing about who when the name is not known", async () => {
    mockApi(routes(() => byHand(null)));
    renderApp(base);
    await screen.findByTestId("review");

    expect(line2()).not.toHaveTextContent(" by ");
    expect(line2()).not.toHaveTextContent(adminUser.id);
  });
});
