import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { IngestJob } from "../api/ingest";
import type { Purchase, Removal } from "../api/purchases";
import { units } from "./catalog-fixtures";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";
import { aliasLine, ingestJobId, manualPurchase, purchaseId, receiptDocumentId, receiptPurchase, receiptPurchaseId, unmatchedLine } from "./purchase-fixtures";

// Removing lines and purchases (#72, #74; spec 10, "Removing lines and purchases").

const voidPlan: Removal = { outcome: "void", prices: 2, photo: false, blocked: null };
const deletePlan: Removal = { outcome: "delete", prices: 0, photo: true, blocked: null };
const recorded = (p: Purchase): Purchase => ({ ...p, lines: p.lines.map((l) => ({ ...l, recorded: l.observation_id !== null })) });

function baseRoutes() {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
    "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
  };
}

describe("removing a purchase", () => {
  it("voids a recorded purchase after saying so, and shows it read-only", async () => {
    let purchase: Purchase = { ...recorded(manualPurchase), removal: voidPlan };
    const calls = mockApi({
      ...baseRoutes(),
      [`GET /purchases/${purchaseId}`]: () => jsonResponse(200, purchase),
      [`POST /purchases/${purchaseId}/remove`]: () => {
        purchase = { ...purchase, status: "voided", voided_at: "2026-09-28T12:00:00Z", voided_by_name: "Admin Example", voided_prices: 1, removal: null, lines: purchase.lines.map((l) => ({ ...l, observation_id: null })) };
        return jsonResponse(200, { outcome: "void", photo_deleted: false, purchase });
      },
    });
    const user = userEvent.setup();
    renderApp(`/shop/purchases/${purchaseId}`);

    const section = await screen.findByRole("region", { name: "Remove this purchase" });
    expect(section).toHaveTextContent("It's in the price book, so its 2 prices will be voided. This can't be undone yet.");
    await user.click(within(section).getByRole("button", { name: "Remove purchase" }));
    const confirm = within(section).getByRole("group", { name: /Remove the Pier Farmers Market purchase from/ });
    expect(within(confirm).getByRole("button", { name: "Keep it" })).toHaveFocus();
    expect(calls.some((c) => c.method === "POST")).toBe(false);

    await user.click(within(confirm).getByRole("button", { name: "Remove purchase" }));
    const notice = await screen.findByText(/^Removed .* by Admin Example\. Its 1 price no longer counts in the price book\.$/);
    await waitFor(() => expect(notice).toHaveFocus());
    expect(screen.queryByRole("region", { name: "Remove this purchase" })).not.toBeInTheDocument();
    for (const name of ["Edit", "Reopen"]) expect(screen.queryByRole("button", { name })).not.toBeInTheDocument();
    expect(screen.getAllByText("Voided").length).toBeGreaterThan(0);
    const rows = within(screen.getByRole("table", { name: "Lines" })).getAllByRole("row").slice(1);
    expect(rows[0]).toHaveTextContent("voided");
  });

  it("deletes a never-recorded receipt and says its photo went too", async () => {
    const draft: Purchase = { ...receiptPurchase, removal: deletePlan };
    mockApi({
      ...baseRoutes(),
      "GET /products/search": () => jsonResponse(200, { items: [] }),
      [`GET /purchases/${receiptPurchaseId}`]: () => jsonResponse(200, draft),
      [`POST /purchases/${receiptPurchaseId}/remove`]: () => jsonResponse(200, { outcome: "delete", photo_deleted: true, purchase: null }),
    });
    const user = userEvent.setup();
    renderApp(`/shop/purchases/${receiptPurchaseId}`);

    const section = await screen.findByRole("region", { name: "Remove this purchase" });
    expect(section).toHaveTextContent("Nothing from it reached the price book, so it will be deleted, along with its receipt photo.");
    await user.click(within(section).getByRole("button", { name: "Remove purchase" }));
    await user.click(within(section).getByRole("button", { name: "Remove purchase" }));

    expect(await screen.findByTestId("notice")).toHaveTextContent("Purchase removed. Its receipt photo was deleted.");
    expect(await screen.findByRole("heading", { name: "Purchases", level: 1 })).toBeInTheDocument();
  });

  it("can't be removed while its receipt is still being read", async () => {
    mockApi({
      ...baseRoutes(),
      "GET /products/search": () => jsonResponse(200, { items: [] }),
      [`GET /purchases/${receiptPurchaseId}`]: () => jsonResponse(200, { ...receiptPurchase, removal: { ...deletePlan, blocked: "still_reading" } }),
    });
    renderApp(`/shop/purchases/${receiptPurchaseId}`);
    const section = await screen.findByRole("region", { name: "Remove this purchase" });
    expect(section).toHaveTextContent("You can remove it once it's been read.");
    expect(within(section).getByRole("button", { name: "Remove purchase" })).toBeDisabled();
  });

  it("keeps the confirm open with the error, and Keep it goes back to the button", async () => {
    mockApi({
      ...baseRoutes(),
      [`GET /purchases/${purchaseId}`]: () => jsonResponse(200, { ...recorded(manualPurchase), removal: voidPlan }),
      [`POST /purchases/${purchaseId}/remove`]: () => errorResponse(500, "internal_error", "Something broke on the server."),
    });
    const user = userEvent.setup();
    renderApp(`/shop/purchases/${purchaseId}`);
    const section = await screen.findByRole("region", { name: "Remove this purchase" });
    await user.click(within(section).getByRole("button", { name: "Remove purchase" }));
    await user.click(within(section).getByRole("button", { name: "Remove purchase" }));

    const alert = await within(section).findByRole("alert");
    expect(alert).toHaveTextContent("Something broke on the server.");
    await waitFor(() => expect(alert).toHaveFocus());
    await user.click(within(section).getByRole("button", { name: "Keep it" }));
    await waitFor(() => expect(within(section).getByRole("button", { name: "Remove purchase" })).toHaveFocus());
  });

  it("keeps Remove usable when reading started again after the page loaded", async () => {
    // Review of #84: the error closed the confirm and left Remove disabled for good.
    mockApi({
      ...baseRoutes(),
      [`GET /purchases/${purchaseId}`]: () => jsonResponse(200, { ...recorded(manualPurchase), removal: voidPlan }),
      [`POST /purchases/${purchaseId}/remove`]: () => errorResponse(409, "still_reading", "You can remove it once it's been read."),
    });
    const user = userEvent.setup();
    renderApp(`/shop/purchases/${purchaseId}`);
    const section = await screen.findByRole("region", { name: "Remove this purchase" });
    await user.click(within(section).getByRole("button", { name: "Remove purchase" }));
    await user.click(within(section).getByRole("button", { name: "Remove purchase" }));
    expect(await within(section).findByText("You can remove it once it's been read.")).toBeInTheDocument();
    expect(within(section).getByRole("button", { name: "Remove purchase" })).toBeEnabled();
  });

  it("treats a 404 on removing as already removed", async () => {
    mockApi({
      ...baseRoutes(),
      [`GET /purchases/${purchaseId}`]: () => jsonResponse(200, { ...recorded(manualPurchase), removal: { ...deletePlan, photo: false } }),
      [`POST /purchases/${purchaseId}/remove`]: () => errorResponse(404, "not_found", "No such purchase."),
    });
    const user = userEvent.setup();
    renderApp(`/shop/purchases/${purchaseId}`);
    const section = await screen.findByRole("region", { name: "Remove this purchase" });
    expect(section).toHaveTextContent("Nothing from it reached the price book, so it will be deleted.");
    await user.click(within(section).getByRole("button", { name: "Remove purchase" }));
    await user.click(within(section).getByRole("button", { name: "Remove purchase" }));
    expect(await screen.findByTestId("notice")).toHaveTextContent(/^Purchase removed\.$/);
  });

  it("says an old link's purchase may have been removed", async () => {
    mockApi({ ...baseRoutes(), [`GET /purchases/${purchaseId}`]: () => errorResponse(404, "not_found", "No such purchase.") });
    renderApp(`/shop/purchases/${purchaseId}`);
    expect(await screen.findByText("This purchase doesn't exist. It may have been removed.")).toBeInTheDocument();
    const main = screen.getByRole("main");
    expect(within(main).getByRole("link", { name: "Purchases" })).toHaveAttribute("href", "/shop/purchases");
  });
});

describe("the purchases list and removed purchases", () => {
  const voidedOne: Purchase = { ...manualPurchase, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8009", status: "voided" };

  it("links to the voided purchases only when there are some, and comes back", async () => {
    mockApi({
      ...baseRoutes(),
      "GET /purchases": (call: RecordedCall) =>
        call.query.get("status") === "voided" ? jsonResponse(200, { items: [voidedOne], next_cursor: null }) : jsonResponse(200, { items: [manualPurchase], next_cursor: null }),
    });
    const user = userEvent.setup();
    renderApp("/shop/purchases");
    await user.click(await screen.findByRole("link", { name: "Show voided (1)" }));

    expect(await screen.findByRole("heading", { name: "Voided purchases" })).toBeInTheDocument();
    const row = within(await screen.findByRole("table", { name: "Purchases" })).getAllByRole("row")[1];
    expect(row).toHaveTextContent("Voided");
    expect(within(row).getByText("$14.21").tagName).toBe("S");
    await user.click(screen.getByRole("button", { name: "Back to all" }));
    expect(await screen.findByRole("heading", { name: "All purchases" })).toBeInTheDocument();
  });

  it("Back to all clears a status chosen before opening Voided", async () => {
    // Review of #84: the title said All while Drafts stayed selected underneath.
    mockApi({
      ...baseRoutes(),
      "GET /purchases": (call: RecordedCall) => {
        const status = call.query.get("status");
        if (status === "voided") return jsonResponse(200, { items: [voidedOne], next_cursor: null });
        if (status === "draft") return jsonResponse(200, { items: [], next_cursor: null });
        return jsonResponse(200, { items: [manualPurchase], next_cursor: null });
      },
    });
    const user = userEvent.setup();
    renderApp("/shop/purchases");
    await user.click(await screen.findByRole("button", { name: "Drafts" }));
    await user.click(await screen.findByRole("link", { name: "Show voided (1)" }));
    await user.click(await screen.findByRole("button", { name: "Back to all" }));
    const table = await screen.findByRole("table", { name: "Purchases" });
    expect(within(table).getByText("Pier Farmers Market")).toBeInTheDocument();
  });

  it("keeps a way to Voided when its count can't be checked", async () => {
    mockApi({
      ...baseRoutes(),
      "GET /purchases": (call: RecordedCall) =>
        call.query.get("status") === "voided" ? errorResponse(500, "internal_error", "Boom.") : jsonResponse(200, { items: [manualPurchase], next_cursor: null }),
    });
    renderApp("/shop/purchases");
    expect(await screen.findByRole("link", { name: "Show voided" })).toHaveAttribute("href", "/shop/purchases?status=voided");
  });

  it("hides the link when nothing was removed", async () => {
    mockApi({
      ...baseRoutes(),
      "GET /purchases": (call: RecordedCall) => jsonResponse(200, { items: call.query.get("status") === "voided" ? [] : [manualPurchase], next_cursor: null }),
    });
    renderApp("/shop/purchases");
    await screen.findByRole("table", { name: "Purchases" });
    expect(screen.queryByRole("link", { name: /Show voided/ })).not.toBeInTheDocument();
  });
});

describe("removing lines", () => {
  const base = `/purchases/${receiptPurchaseId}`;

  it("deletes an unrecorded line in one click, and asks first for a recorded one", async () => {
    const reopened: Purchase = {
      ...receiptPurchase,
      status: "reviewed",
      lines: [
        { ...aliasLine, recorded: true },
        { ...unmatchedLine, recorded: false },
      ],
      removed_line_count: 1,
    };
    const calls = mockApi({
      ...baseRoutes(),
      "GET /products/search": () => jsonResponse(200, { items: [] }),
      [`GET ${base}`]: () => jsonResponse(200, reopened),
      [`DELETE ${base}/lines/${aliasLine.id}`]: () => jsonResponse(200, reopened),
      [`DELETE ${base}/lines/${unmatchedLine.id}`]: () => jsonResponse(200, reopened),
    });
    const user = userEvent.setup();
    renderApp(`/shop/purchases/${receiptPurchaseId}`);
    await screen.findByTestId("review");
    expect(screen.getByText("1 line removed")).toBeInTheDocument();
    // The quiet alias line is listed under All, not under Needs you.
    await user.click(screen.getByRole("button", { name: "All 2" }));

    const line = (i: number) => screen.getAllByTestId("review-line")[i];
    line(0).focus();
    await user.click(await within(line(0)).findByRole("button", { name: "Delete line 1" }));
    const ask = screen.getByRole("group", { name: "Delete line 1" });
    expect(ask).toHaveTextContent("Delete line 1? Its price is voided.");
    expect(within(ask).getByRole("button", { name: "Keep it" })).toHaveFocus();
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    await user.click(within(ask).getByRole("button", { name: "Keep it" }));
    expect(screen.getAllByTestId("review-line")[0]).toHaveFocus();

    await user.click(within(line(0)).getByRole("button", { name: "Delete line 1" }));
    await user.click(within(screen.getByRole("group", { name: "Delete line 1" })).getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path.endsWith(aliasLine.id))).toBe(true));

    line(1).focus();
    await user.click(await within(line(1)).findByRole("button", { name: "Delete line 2" }));
    await waitFor(() => expect(calls.some((c) => c.method === "DELETE" && c.path.endsWith(unmatchedLine.id))).toBe(true));
  });

  it("warns that saving voids the prices of removed lines, then says it did", async () => {
    const purchase: Purchase = { ...recorded(manualPurchase), removal: voidPlan };
    mockApi({
      ...baseRoutes(),
      [`GET /purchases/${purchaseId}`]: () => jsonResponse(200, purchase),
      [`PUT /purchases/${purchaseId}`]: () => jsonResponse(200, { ...purchase, lines: [purchase.lines[1]] }),
    });
    const user = userEvent.setup();
    renderApp(`/shop/purchases/${purchaseId}`);
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const form = await screen.findByRole("form", { name: "Edit purchase" });
    expect(within(form).queryByText(/Saving voids/)).not.toBeInTheDocument();

    // Line 2 never reached the price book: removing it voids nothing.
    await user.click(within(form).getByRole("button", { name: "Remove line 2" }));
    expect(within(form).queryByText(/Saving voids/)).not.toBeInTheDocument();
    await user.click(within(form).getByRole("button", { name: "Remove line 1" }));
    expect(within(form).getByText("Saving voids 1 price from removed lines.")).toBeInTheDocument();
  });
});

describe("receipts", () => {
  const failedJob: IngestJob = { id: ingestJobId, receipt_document_id: receiptDocumentId, stage: "ocr", status: "failed", attempts: 5, last_error: "ocr_unavailable", purchase_id: null, created_at: "2026-09-20T18:05:30Z", updated_at: "2026-09-20T18:06:00Z" };

  it("removes a failed read after asking", async () => {
    let jobs = [failedJob];
    const calls = mockApi({
      ...baseRoutes(),
      "GET /ingest-jobs": () => jsonResponse(200, { items: jobs }),
      [`POST /ingest-jobs/${ingestJobId}/remove`]: () => {
        jobs = [];
        return jsonResponse(200, { photo_deleted: true });
      },
    });
    const user = userEvent.setup();
    renderApp("/shop/receipts");
    const row = await screen.findByTestId("ingest-job");
    await user.click(within(row).getByRole("button", { name: "Remove" }));
    const ask = within(row).getByRole("group", { name: "Remove this receipt" });
    expect(ask).toHaveTextContent("Remove this receipt? Its photo is deleted.");
    expect(within(ask).getByRole("button", { name: "Keep it" })).toHaveFocus();
    await user.click(within(ask).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path.endsWith("/remove"))).toBe(true));
    expect(await screen.findByText("No receipts yet")).toBeInTheDocument();
  });

  it("says a receipt pinned from the inbox was removed, not an error", async () => {
    mockApi({
      ...baseRoutes(),
      [`GET /ingest-jobs/${ingestJobId}`]: () => errorResponse(404, "receipt_removed", "This receipt was removed."),
    });
    renderApp(`/shop/receipts?job=${ingestJobId}`);
    expect(await screen.findByText("This receipt was removed.")).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  });

  it("offers no Remove on a failed read that already has a draft", async () => {
    mockApi({ ...baseRoutes(), "GET /ingest-jobs": () => jsonResponse(200, { items: [{ ...failedJob, purchase_id: receiptPurchaseId }] }) });
    renderApp("/shop/receipts");
    const row = await screen.findByTestId("ingest-job");
    expect(within(row).queryByRole("button", { name: "Remove" })).not.toBeInTheDocument();
  });

  it("says when an upload brings back a removed receipt", async () => {
    mockApi({
      ...baseRoutes(),
      "POST /receipts": () => jsonResponse(200, { document: { id: receiptDocumentId }, job: { ...failedJob, status: "pending", stage: "captured" }, revived: true }),
    });
    const user = userEvent.setup();
    renderApp("/shop/receipts");
    await screen.findByText("No receipts yet");
    const file = new File([new Uint8Array([0x89, 0x50, 0x4e, 0x47])], "slip.png", { type: "image/png" });
    await user.upload(screen.getByLabelText("Photo"), file);
    await user.click(screen.getByRole("button", { name: "Upload" }));
    expect(await screen.findByTestId("notice")).toHaveTextContent("This receipt was removed before; it's being read again.");
  });
});
