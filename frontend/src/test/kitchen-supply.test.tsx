// Kitchen supply lines (spec 04, 2T; issue 262): a line marked a supply keeps
// its amount and a category, emits no price, and is learned like Ignore.
// Every store, wording and amount here is invented.
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Purchase, PurchaseLine } from "../api/purchases";
import type { ToIdentifyGroup } from "../api/toIdentify";
import { units } from "./catalog-fixtures";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";
import { receiptPurchase, receiptPurchaseId, unmatchedLine } from "./purchase-fixtures";

const base = `/purchases/${receiptPurchaseId}`;

const categories = [
  { key: "wraps", label: "Wraps and papers", examples: "foil, parchment, wax paper" },
  { key: "storage", label: "Bags and containers", examples: "zip-top and freezer bags" },
  { key: "cooking", label: "Cooking aids", examples: "twine, cheesecloth, skewers" },
  { key: "filters", label: "Coffee and tea filters", examples: "paper coffee filters" },
  { key: "fuel", label: "Grilling fuel", examples: "charcoal, wood chips" },
];

const foil: PurchaseLine = {
  ...unmatchedLine,
  raw_text: "BRIGHTWRAP FOIL 75SF",
  raw_text_norm: "BRIGHTWRAP FOIL 75SF",
  line_total: "4.29",
  suggestions: [],
};

function routes(purchase: () => Purchase) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
    "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    "GET /products/search": () => jsonResponse(200, { items: [] }),
    "GET /supply-categories": () => jsonResponse(200, { items: categories }),
    [`GET ${base}`]: () => jsonResponse(200, purchase()),
  };
}

function withLine(line: PurchaseLine, extra: Partial<Purchase> = {}): Purchase {
  return {
    ...receiptPurchase,
    ...extra,
    lines: receiptPurchase.lines.map((l) => (l.id === unmatchedLine.id ? line : l)),
  };
}

const resolvePath = `${base}/lines/${unmatchedLine.id}/resolve`;

describe("kitchen supply lines in review", () => {
  it("marks a line a kitchen supply of the chosen kind from review", async () => {
    let purchase = withLine(foil);
    const calls = mockApi({
      ...routes(() => purchase),
      [`POST ${resolvePath}`]: () => {
        purchase = withLine(
          { ...foil, resolution: "supply", supply_category: "wraps", resolved_by_name: "Admin" },
          { supply_total: "4.29" },
        );
        return jsonResponse(200, purchase);
      },
    });
    const user = userEvent.setup();
    renderApp(base);
    await screen.findByTestId("review");
    const row = screen.getAllByTestId("review-line")[1];
    await user.click(within(row).getByRole("button", { name: "Kitchen supply" }));

    const choices = await screen.findByRole("group", {
      name: "Kitchen supply: which kind is line 2?",
    });
    expect(choices).toHaveTextContent("Cleaning, serving and appliance supplies: use Ignore.");
    const wraps = await within(choices).findByRole("button", { name: /Wraps and papers/ });
    await waitFor(() => expect(wraps).toHaveFocus());
    await user.click(wraps);

    const post = await waitFor(() => {
      const found = calls.find((c) => c.method === "POST" && c.path === resolvePath);
      expect(found).toBeDefined();
      return found;
    });
    expect(post?.body).toEqual({ supply_category: "wraps" });
    await waitFor(() => {
      const line = screen.getAllByTestId("review-line")[1];
      expect(line).toHaveTextContent("Wraps and papers");
      expect(line).toHaveTextContent("kitchen supply");
    });
    expect(screen.queryByRole("group", { name: /which kind/ })).toBeNull();
  });

  it("opens the choices with s and closes them with Escape, sending nothing", async () => {
    const calls = mockApi(routes(() => withLine(foil)));
    const user = userEvent.setup();
    renderApp(base);
    await screen.findByTestId("review");
    const row = screen.getAllByTestId("review-line")[1];
    row.focus();
    await user.keyboard("s");
    const wraps = await screen.findByRole("button", { name: /Wraps and papers/ });
    await waitFor(() => expect(wraps).toHaveFocus());
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("group", { name: /which kind/ })).toBeNull());
    await waitFor(() => expect(screen.getAllByTestId("review-line")[1]).toHaveFocus());
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("offers a learned supply as a suggestion that Enter accepts", async () => {
    const hinted: PurchaseLine = {
      ...foil,
      suggestions: [
        {
          kind: "alias_unconfirmed",
          product_id: null,
          ignore: false,
          supply_category: "filters",
          label: "CONE FILTERS",
          score: "1",
        },
      ],
    };
    const purchase = withLine(hinted);
    const calls = mockApi({
      ...routes(() => purchase),
      [`POST ${resolvePath}`]: () => jsonResponse(200, purchase),
    });
    const user = userEvent.setup();
    renderApp(base);
    await screen.findByTestId("review");
    const row = screen.getAllByTestId("review-line")[1];
    await waitFor(() => expect(row).toHaveTextContent("Kitchen supply: Coffee and tea filters"));
    row.focus();
    await user.keyboard("{Enter}");
    const post = await waitFor(() => {
      const found = calls.find((c) => c.method === "POST" && c.path === resolvePath);
      expect(found).toBeDefined();
      return found;
    });
    expect(post?.body).toEqual({ supply_category: "filters", accepted_kind: "alias" });
  });

  it("shows a committed purchase's kitchen supplies and leaves them out of To identify", async () => {
    const supply: PurchaseLine = {
      ...foil,
      resolution: "supply",
      supply_category: "wraps",
      observation_id: null,
    };
    const committed: Purchase = {
      ...withLine(supply, { supply_total: "4.29" }),
      status: "committed",
      lines: [supply],
    };
    mockApi(routes(() => committed));
    renderApp(base);
    const total = await screen.findByText("Kitchen supplies");
    expect(total.parentElement).toHaveTextContent("Kitchen supplies$4.29");
    const lines = screen.getByRole("table", { name: "Lines" });
    await waitFor(() => expect(lines).toHaveTextContent("kitchen supply Wraps and papers"));
    expect(lines).not.toHaveTextContent("no observation");
    expect(screen.queryByText(/to-identify queue/)).toBeNull();
  });
});

describe("kitchen supply on To identify", () => {
  it("marks a To identify group a kitchen supply", async () => {
    const group: ToIdentifyGroup = {
      vendor: { id: chainLocation.vendor.id, name: chainLocation.vendor.name },
      raw_text_norm: "ZIP FREEZER BAGS",
      line_count: 2,
      lines: [
        {
          line_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8a01",
          purchase_id: receiptPurchaseId,
          raw_text: "ZIP FREEZER BAGS",
          purchased_at: "2026-09-20T18:05:00Z",
          line_total: "5.25",
          qty: null,
          unit: null,
        },
        {
          line_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8a02",
          purchase_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8004",
          raw_text: "ZIP FREEZER BAGS",
          purchased_at: "2026-09-06T17:00:00Z",
          line_total: "5.50",
          qty: null,
          unit: null,
        },
      ],
    };
    const calls = mockApi({
      ...routes(() => receiptPurchase),
      "GET /to-identify": () => jsonResponse(200, { items: [group] }),
      "POST /to-identify/apply": (call: RecordedCall) =>
        jsonResponse(200, {
          applied: (call.body as { line_ids?: string[] }).line_ids?.length ?? 2,
        }),
    });
    const user = userEvent.setup();
    renderApp("/shop/receipts/identify");
    const card = await screen.findByRole("group", {
      name: `${chainLocation.vendor.name}: ZIP FREEZER BAGS`,
    });
    await user.click(within(card).getByRole("button", { name: "Kitchen supply" }));
    await user.click(await within(card).findByRole("button", { name: /Bags and containers/ }));
    const post = await waitFor(() => {
      const found = calls.find((c) => c.method === "POST" && c.path === "/to-identify/apply");
      expect(found).toBeDefined();
      return found;
    });
    expect(post?.body).toEqual({
      vendor_id: chainLocation.vendor.id,
      raw_text_norm: "ZIP FREEZER BAGS",
      supply_category: "storage",
    });
    expect(await screen.findByText("Applied to 2 lines.")).toBeInTheDocument();
  });
});
