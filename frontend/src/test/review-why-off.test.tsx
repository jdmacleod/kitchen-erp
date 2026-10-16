import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Purchase } from "../api/purchases";
import { units } from "./catalog-fixtures";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { receiptPurchaseId } from "./purchase-fixtures";
import { appleSaving, tortillaSaving, weightRow, youPayPurchase } from "./why-off-fixtures";

const base = `/purchases/${receiptPurchaseId}`;

function routes(purchase: () => Purchase, extra: Record<string, () => Response> = {}) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
    "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    [`GET ${base}`]: () => jsonResponse(200, purchase()),
    ...extra,
  };
}

async function panel() {
  await screen.findByTestId("review");
  return screen.getByRole("region", { name: /The lines are off by/ });
}

describe("why the lines are off (2S, U1–U3)", () => {
  it("shows the sums, what the receipt prints, and that the total is what was paid", async () => {
    mockApi(routes(() => youPayPurchase));
    renderApp(base);
    const why = await panel();
    expect(within(why).getByRole("heading")).toHaveTextContent("The lines are off by $1.65");
    expect(
      within(why).getByText("The total matches what was paid, so a line is off."),
    ).toBeVisible();
    expect(within(why).getByText("Lines add up to").nextSibling).toHaveTextContent("$4.92");
    const printed = within(why).getByRole("list", { name: "What the receipt prints about itself" });
    expect(printed).toHaveTextContent("Paid $6.57, the same as the total.");
    expect(printed).toHaveTextContent("The receipt prints $1.65 in savings; the lines have $1.65.");
  });

  it("counts every saving as already in the price in one click", async () => {
    const calls = mockApi(
      routes(() => youPayPurchase, {
        [`PATCH ${base}/lines/${tortillaSaving.id}`]: () => jsonResponse(200, youPayPurchase),
        [`PATCH ${base}/lines/${appleSaving.id}`]: () => jsonResponse(200, youPayPurchase),
      }),
    );
    const user = userEvent.setup();
    renderApp(base);
    const why = await panel();
    expect(within(why).getByText(/The gap is exactly the 2 savings \(\$1\.65\)/)).toBeVisible();
    await user.click(
      within(why).getByRole("button", { name: "Count them as already in the price" }),
    );
    await waitFor(() =>
      expect(calls.filter((c) => c.method === "PATCH").map((c) => [c.path, c.body])).toEqual([
        [`${base}/lines/${tortillaSaving.id}`, { in_price: true }],
        [`${base}/lines/${appleSaving.id}`, { in_price: true }],
      ]),
    );
  });

  it("joins a loose weight row to the item its weight at the rate comes to", async () => {
    const calls = mockApi(
      routes(() => youPayPurchase, {
        [`POST ${base}/lines/${weightRow.id}/merge`]: () => jsonResponse(200, youPayPurchase),
      }),
    );
    const user = userEvent.setup();
    renderApp(base);
    const why = await panel();
    await user.click(
      within(why).getByRole("button", { name: "Join line 4 to line 3 (0.07 lb = $0.63)" }),
    );
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST")?.body).toEqual({
        into_line_id: youPayPurchase.lines[2].id,
      }),
    );
  });

  it("shows the printed row under what the model read", async () => {
    mockApi(routes(() => youPayPurchase));
    renderApp(base);
    await panel();
    const rows = screen.getAllByTestId("review-line");
    expect(within(rows[0]).getByText("44112 MEADOW TORTILLAS 3.49 2.99 S")).toBeInTheDocument();
    // A row the model read in full adds nothing, so nothing is shown.
    expect(within(rows[3]).queryByText(/Printed/)).not.toBeInTheDocument();
  });
});

describe("fixing a saving in review (2S, U6, U8)", () => {
  it("asks which item a saving the reader could not attach belongs to", async () => {
    const calls = mockApi(
      routes(() => youPayPurchase, {
        [`PATCH ${base}/lines/${appleSaving.id}`]: () => jsonResponse(200, youPayPurchase),
      }),
    );
    const user = userEvent.setup();
    renderApp(base);
    await panel();
    const row = screen.getAllByTestId("review-line")[5];
    row.focus();
    expect(await within(row).findByText("Which item is this saving for?")).toBeVisible();
    await user.click(within(row).getByRole("button", { name: /^Line 5,/ }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
        parent_line_id: youPayPurchase.lines[4].id,
      }),
    );
  });

  it("drops the attachment chip when a saving sits right under its item", async () => {
    mockApi(routes(() => youPayPurchase));
    renderApp(base);
    await panel();
    const rows = screen.getAllByTestId("review-line");
    expect(within(rows[1]).queryByText("parent inferred")).not.toBeInTheDocument();
    expect(within(rows[5]).getByText("parent inferred")).toBeInTheDocument();
  });

  it("shows a saving in the price struck through, and counts it again on a click", async () => {
    const kept = { ...tortillaSaving, line_total: "0.0000", in_price_amount: "0.5000" };
    const purchase = {
      ...youPayPurchase,
      lines: youPayPurchase.lines.map((l) => (l.id === kept.id ? kept : l)),
    };
    const calls = mockApi(
      routes(() => purchase, {
        [`PATCH ${base}/lines/${kept.id}`]: () => jsonResponse(200, purchase),
      }),
    );
    const user = userEvent.setup();
    renderApp(base);
    await panel();
    const row = screen.getAllByTestId("review-line")[1];
    expect(within(row).getByText("already in the price, not counted")).toBeVisible();
    row.focus();
    await user.click(await within(row).findByRole("button", { name: "Count it again" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ in_price: false }),
    );
  });
});

describe("a total typed to match the lines (2S, U2)", () => {
  it("asks once before hiding the gap, then saves", async () => {
    const calls = mockApi(
      routes(() => youPayPurchase, {
        [`PATCH ${base}`]: () => jsonResponse(200, youPayPurchase),
      }),
    );
    const user = userEvent.setup();
    renderApp(base);
    await panel();
    const total = screen.getByLabelText("Total");
    await user.clear(total);
    await user.type(total, "4.92");
    await user.click(screen.getByRole("button", { name: "Save header" }));
    expect(
      screen.getByText(/This makes the lines add up by changing the total, not a line/),
    ).toBeVisible();
    expect(calls.some((c) => c.method === "PATCH")).toBe(false);
    await user.click(screen.getByRole("button", { name: "Save header" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ total: "4.92" }),
    );
  });
});

describe("correct the lines: room to read each row (2S, U5)", () => {
  it("shows the printed row, the saving's item, and a saving kept in the price", async () => {
    const kept = { ...tortillaSaving, line_total: "0.0000", in_price_amount: "0.5000" };
    const purchase = {
      ...youPayPurchase,
      lines: youPayPurchase.lines.map((l) => (l.id === kept.id ? kept : l)),
    };
    mockApi(routes(() => purchase));
    const user = userEvent.setup();
    renderApp(base);
    await panel();
    await user.click(screen.getByRole("button", { name: "Correct the lines" }));
    const table = screen.getByRole("group", { name: "Correct the lines" });
    const rows = within(table).getAllByRole("listitem");
    expect(rows[0]).toHaveTextContent("Printed: 44112 MEADOW TORTILLAS 3.49 2.99 S");
    expect(rows[1]).toHaveTextContent("On line 1");
    expect(rows[1]).toHaveTextContent("Already in the price: −$0.50, not counted.");
    expect(within(table).getByLabelText("Receipt text, row 2")).toHaveValue("Club Savings");
  });
});

describe("reading the photo and the words (2S, U4, U9)", () => {
  it("zooms the receipt in its frame and widens the image column on demand", async () => {
    mockApi(routes(() => youPayPurchase));
    const user = userEvent.setup();
    renderApp(base);
    await panel();
    const img = screen.getByRole("img", { name: "The receipt as photographed" });
    expect(img).toHaveAttribute("src", expect.stringContaining("width=1024"));
    await user.click(screen.getByRole("button", { name: "Zoom in" }));
    expect(screen.getByText("150%")).toBeInTheDocument();
    const zoomed = screen.getByRole("img", { name: "The receipt as photographed" });
    // The photo fills a box one and a half times the frame's width.
    expect(screen.getByTestId("receipt-photo")).toHaveStyle({ width: "150%" });
    expect(zoomed).toHaveClass("w-full");
    expect(zoomed.getAttribute("src")).not.toContain("width=");
    const wide = screen.getByRole("button", { name: "Side by side" });
    expect(wide).toHaveAttribute("aria-pressed", "false");
    await user.click(wide);
    expect(wide).toHaveAttribute("aria-pressed", "true");
  });

  it("offers the catalog's spelling for a word OCR misread, and changes only the text", async () => {
    const misread = { ...youPayPurchase.lines[2], spelling: [{ word: "FRESMO", maybe: "FRESNO" }] };
    const purchase = {
      ...youPayPurchase,
      lines: youPayPurchase.lines.map((l) =>
        l.id === misread.id ? { ...misread, raw_text: "PEPPERS FRESMO" } : l,
      ),
    };
    const calls = mockApi(
      routes(() => purchase, {
        [`PATCH ${base}/lines/${misread.id}`]: () => jsonResponse(200, purchase),
      }),
    );
    const user = userEvent.setup();
    renderApp(base);
    await panel();
    const row = screen.getAllByTestId("review-line")[2];
    row.focus();
    await user.click(await within(row).findByRole("button", { name: "Change FRESMO to FRESNO" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({
        raw_text: "PEPPERS FRESNO",
      }),
    );
  });
});

describe("reading down the lines (2S)", () => {
  it("shows each saving as a minus and the sum so far, ending beside the receipt's total", async () => {
    mockApi(routes(() => youPayPurchase));
    renderApp(base);
    await panel();
    const rows = screen.getAllByTestId("review-line");
    expect(within(rows[1]).getByText("−$0.50")).toBeInTheDocument();
    expect(within(rows[1]).getByText("so far $2.49")).toBeInTheDocument();
    expect(within(rows[5]).getByText("so far $4.92")).toBeInTheDocument();
    expect(
      screen.getByText((_, el) =>
        el?.tagName === "P"
          ? el.textContent === "The lines add up to $4.92; the receipt says $6.57, $1.65 short."
          : false,
      ),
    ).toBeInTheDocument();
    // The weight row is not an item of its own.
    expect(screen.getByText(/^3 items · /)).toBeInTheDocument();
  });

  it("offers today's line rules to a receipt read before printed rows", async () => {
    const before = {
      ...youPayPurchase,
      printed_checks: null,
      lines: youPayPurchase.lines.map((l) => ({ ...l, printed_text: null })),
      reread: { can_recheck: true, open: null },
    };
    const calls = mockApi(
      routes(() => before, {
        [`POST ${base}/rereads`]: () =>
          jsonResponse(201, { id: "r1", status: "pending", mode: "check" }),
      }),
    );
    const user = userEvent.setup();
    renderApp(base);
    const why = await panel();
    await user.click(within(why).getByRole("button", { name: "Re-check the lines" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "POST")?.body).toEqual({ mode: "check" }),
    );
  });

  it("does not offer it once the reading has printed rows", async () => {
    mockApi(routes(() => ({ ...youPayPurchase, reread: { can_recheck: true, open: null } })));
    renderApp(base);
    const why = await panel();
    expect(within(why).queryByRole("button", { name: "Re-check the lines" })).toBeNull();
  });
});
