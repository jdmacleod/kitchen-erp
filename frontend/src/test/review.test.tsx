import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Purchase } from "../api/purchases";
import { flourProductId, hits, units } from "./catalog-fixtures";
import { chainLocation, chainLocationId, marketLocation, marketLocationId } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";
import { discountLine, ingestJobId, receiptDocumentId, receiptPurchase, receiptPurchaseId, unmatchedLine } from "./purchase-fixtures";

const base = `/purchases/${receiptPurchaseId}`;

function baseRoutes(purchase: () => Purchase) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
    "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    "GET /products/search": (call: RecordedCall) => jsonResponse(200, { items: call.query.get("q")?.includes("flour") ? hits : [] }),
    [`GET ${base}`]: () => jsonResponse(200, purchase()),
  };
}

async function openReview() {
  await screen.findByTestId("review");
  return screen.getAllByTestId("review-line");
}

describe("receipt review", () => {
  it("moves between lines with j/k and the arrows, accepts the top suggestion with Enter, and ignores with i", async () => {
    let purchase = receiptPurchase;
    const calls = mockApi({
      ...baseRoutes(() => purchase),
      [`POST ${base}/lines/${unmatchedLine.id}/resolve`]: () => {
        purchase = {
          ...purchase,
          lines: purchase.lines.map((l) =>
            l.id === unmatchedLine.id
              ? { ...l, product: { id: hits[1].id, name: hits[1].name, brand: hits[1].brand, pack_qty: hits[1].pack_qty, pack_unit: hits[1].pack_unit, category: hits[1].ingredient.category, category_key: hits[1].ingredient.category_key }, resolution: "fuzzy", resolved_by: adminUser.id, suggestions: [] }
              : l,
          ),
        };
        return jsonResponse(200, purchase);
      },
      [`POST ${base}/lines/${receiptPurchase.lines[3].id}/resolve`]: () => {
        purchase = { ...purchase, lines: purchase.lines.map((l) => (l.seq === 4 ? { ...l, product: null, resolution: "ignored", flags: [] } : l)) };
        return jsonResponse(200, purchase);
      },
    });
    const user = userEvent.setup();
    renderApp(base);
    const rows = await openReview();
    expect(rows).toHaveLength(4);

    // The confirmed-alias line is quiet; the first line needing attention is current.
    expect(rows[0]).toHaveAttribute("data-quiet", "true");
    expect(rows[1]).toHaveAttribute("aria-selected", "true");
    expect(rows[1]).toHaveAttribute("tabindex", "0");
    expect(rows[0]).toHaveAttribute("tabindex", "-1");
    // Flags and suggestions draw the eye; the outlier is not quiet.
    expect(rows[3]).not.toHaveAttribute("data-quiet");
    expect(within(rows[3]).getByText("price outlier")).toBeInTheDocument();
    expect(screen.getByText(/The receipt says \$12\.00 but the lines add up to \$11\.50/)).toBeInTheDocument();
    expect(screen.getByRole("img", { name: "The receipt as photographed" })).toHaveAttribute("src", `/api/v1/receipts/${receiptDocumentId}/image`);
    expect(screen.getByLabelText("Keyboard shortcuts")).toHaveTextContent("accept the top suggestion");

    rows[1].focus();
    await user.keyboard("j");
    expect(screen.getAllByTestId("review-line")[2]).toHaveFocus();
    await user.keyboard("k");
    expect(screen.getAllByTestId("review-line")[1]).toHaveFocus();
    await user.keyboard("{ArrowUp}");
    expect(screen.getAllByTestId("review-line")[0]).toHaveFocus();
    await user.keyboard("{ArrowDown}");
    expect(screen.getAllByTestId("review-line")[1]).toHaveFocus();

    // Enter accepts the top suggestion and says which rung it came from.
    await user.keyboard("{Enter}");
    const accept = await waitFor(() => {
      const found = calls.find((c) => c.method === "POST" && c.path === `${base}/lines/${unmatchedLine.id}/resolve`);
      expect(found).toBeDefined();
      return found;
    });
    expect(accept?.body).toEqual({ product_id: hits[1].id, accepted_kind: "fuzzy" });
    const second = await waitFor(() => {
      const row = screen.getAllByTestId("review-line")[1];
      expect(within(row).getByRole("link", { name: "Riverbend Bread Flour" })).toBeInTheDocument();
      return row;
    });
    expect(within(second).getByText("fuzzy alias")).toBeInTheDocument();

    // i marks the current line ignored, with no accepted_kind.
    screen.getAllByTestId("review-line")[3].focus();
    await user.keyboard("i");
    const ignore = await waitFor(() => {
      const found = calls.find((c) => c.method === "POST" && c.path === `${base}/lines/${receiptPurchase.lines[3].id}/resolve`);
      expect(found).toBeDefined();
      return found;
    });
    expect(ignore?.body).toEqual({ ignore: true });
    await waitFor(() => expect(within(screen.getAllByTestId("review-line")[3]).getByText("ignored", { selector: "span.italic" })).toBeInTheDocument());
  });

  it("opens the product typeahead with / and resolves the line with the chosen product", async () => {
    let purchase = receiptPurchase;
    const calls = mockApi({
      ...baseRoutes(() => purchase),
      [`POST ${base}/lines/${unmatchedLine.id}/resolve`]: () => {
        purchase = {
          ...purchase,
          lines: purchase.lines.map((l) => (l.id === unmatchedLine.id ? { ...l, product: { id: flourProductId, name: "All-Purpose Flour", brand: "Millstone", pack_qty: "5", pack_unit: "lb", category: "pantry", category_key: "pantry" }, resolution: "manual", resolved_by: adminUser.id, suggestions: [] } : l)),
        };
        return jsonResponse(200, purchase);
      },
    });
    const user = userEvent.setup();
    renderApp(base);
    const rows = await openReview();

    rows[1].focus();
    await user.keyboard("/");
    const box = await screen.findByRole("combobox", { name: "Product for line 2" });
    await waitFor(() => expect(box).toHaveFocus());
    await user.keyboard("flour");
    // Scoped to the typeahead: the discount line's "Attach to" select also has an option naming this product.
    await user.click(await within(screen.getByRole("listbox", { name: "Products" })).findByRole("option", { name: /All-Purpose Flour/ }));

    const post = await waitFor(() => {
      const found = calls.find((c) => c.method === "POST" && c.path === `${base}/lines/${unmatchedLine.id}/resolve`);
      expect(found).toBeDefined();
      return found;
    });
    expect(post?.body).toEqual({ product_id: flourProductId });
    await waitFor(() => expect(screen.queryByRole("combobox", { name: "Product for line 2" })).not.toBeInTheDocument());
    expect(within(screen.getAllByTestId("review-line")[1]).getByRole("link", { name: "Millstone All-Purpose Flour" })).toBeInTheDocument();
  });

  it("reattaches a discount to another item with a PATCH of parent_line_id, and detaches with clear_parent", async () => {
    let purchase = receiptPurchase;
    const calls = mockApi({
      ...baseRoutes(() => purchase),
      [`PATCH ${base}/lines/${discountLine.id}`]: (call) => {
        const body = call.body as { parent_line_id?: string; clear_parent?: boolean };
        purchase = { ...purchase, lines: purchase.lines.map((l) => (l.id === discountLine.id ? { ...l, parent_line_id: body.clear_parent ? null : (body.parent_line_id ?? null) } : l)) };
        return jsonResponse(200, purchase);
      },
    });
    const user = userEvent.setup();
    renderApp(base);
    await openReview();

    const attach = screen.getByRole("combobox", { name: "Attach line 3 to" });
    expect(attach).toHaveValue("");
    await user.selectOptions(attach, unmatchedLine.id);
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(1));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ parent_line_id: unmatchedLine.id });
    await waitFor(() => expect(screen.getByRole("combobox", { name: "Attach line 3 to" })).toHaveValue(unmatchedLine.id));

    await user.selectOptions(screen.getByRole("combobox", { name: "Attach line 3 to" }), "");
    await waitFor(() => expect(calls.filter((c) => c.method === "PATCH")).toHaveLength(2));
    expect(calls.filter((c) => c.method === "PATCH")[1].body).toEqual({ clear_parent: true });
  });

  it("commits with c then Enter, shows the committed purchase with Reopen, and reopens into review", async () => {
    let purchase = receiptPurchase;
    const calls = mockApi({
      ...baseRoutes(() => purchase),
      [`POST ${base}/commit`]: () => {
        purchase = { ...purchase, status: "committed", lines: purchase.lines.map((l) => (l.seq === 1 || l.seq === 4 ? { ...l, observation_id: `obs-${l.seq}` } : l)) };
        return jsonResponse(200, purchase);
      },
      [`POST ${base}/reopen`]: () => {
        purchase = { ...purchase, status: "reviewed" };
        return jsonResponse(200, purchase);
      },
    });
    const user = userEvent.setup();
    renderApp(base);
    const rows = await openReview();

    rows[1].focus();
    await user.keyboard("c");
    const dialog = await screen.findByRole("dialog", { name: "Commit this purchase?" });
    expect(dialog).toHaveTextContent("2 lines emit a price observation now. 1 unidentified line waits in the to-identify queue.");
    expect(within(dialog).getByRole("button", { name: "Commit" })).toHaveFocus();
    await user.keyboard("{Enter}");

    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === `${base}/commit`)).toBe(true));
    expect(await screen.findByRole("button", { name: "Reopen" })).toBeInTheDocument();
    expect(screen.queryByTestId("review")).not.toBeInTheDocument();
    const table = screen.getByRole("table", { name: "Lines" });
    const lines = within(table).getAllByRole("row").slice(1);
    expect(lines[0]).toHaveTextContent("observed");
    expect(lines[1]).toHaveTextContent("unresolved");
    expect(lines[1]).toHaveTextContent("no observation");
    expect(screen.getByRole("link", { name: "to-identify queue" })).toHaveAttribute("href", "/to-identify");
    // A receipt purchase has no entry-form Edit; the review screen is its editor.
    expect(screen.queryByRole("button", { name: "Edit" })).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Reopen" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === `${base}/reopen`)).toBe(true));
    expect(await screen.findByTestId("review")).toBeInTheDocument();
  });

  it("Escape closes the commit dialog without committing", async () => {
    const calls = mockApi(baseRoutes(() => receiptPurchase));
    const user = userEvent.setup();
    renderApp(base);
    const rows = await openReview();
    rows[1].focus();
    await user.keyboard("c");
    await screen.findByRole("dialog", { name: "Commit this purchase?" });
    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });

  it("offers the ingest job's ranked location candidates and saves the chosen one in the header", async () => {
    let purchase: Purchase = { ...receiptPurchase, vendor_location: null };
    const calls = mockApi({
      ...baseRoutes(() => purchase),
      "GET /ingest-jobs": () => jsonResponse(200, { items: [{ id: ingestJobId, receipt_document_id: receiptDocumentId, stage: "review", status: "needs_review", attempts: 1, last_error: null, purchase_id: receiptPurchaseId, created_at: "2026-09-20T18:05:30Z", updated_at: "2026-09-20T18:06:00Z" }] }),
      [`GET /ingest-jobs/${ingestJobId}`]: () =>
        jsonResponse(200, {
          id: ingestJobId,
          stage: "review",
          status: "needs_review",
          purchase_id: receiptPurchaseId,
          last_error: null,
          stage_results: [
            {
              id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9201",
              stage: "header",
              adapter: "llm",
              adapter_version: "1",
              duration_ms: 12,
              created_at: "2026-09-20T18:05:40Z",
              output: {
                parsed: true,
                location: {
                  matched: false,
                  vendor_location_id: null,
                  vendor_id: null,
                  candidates: [
                    { vendor_location_id: marketLocationId, vendor_id: marketLocation.vendor.id, vendor_name: marketLocation.vendor.name, location_name: marketLocation.name, score: "1.200", evidence: {} },
                    { vendor_location_id: chainLocationId, vendor_id: chainLocation.vendor.id, vendor_name: chainLocation.vendor.name, location_name: chainLocation.name, score: "0.900", evidence: {} },
                  ],
                },
              },
            },
          ],
        }),
      [`PATCH ${base}`]: (call) => {
        const body = call.body as { vendor_location_id: string };
        purchase = { ...purchase, vendor_location: { id: body.vendor_location_id, name: chainLocation.name, vendor: { id: chainLocation.vendor.id, name: chainLocation.vendor.name, kind: "chain" } } };
        return jsonResponse(200, purchase);
      },
    });
    const user = userEvent.setup();
    renderApp(base);
    await openReview();

    const candidates = await screen.findByRole("list", { name: "Location candidates" });
    const buttons = within(candidates).getAllByRole("button");
    expect(buttons.map((b) => b.textContent)).toEqual(["Pier Farmers Market · 1.200", "Millstone Market — Millstone Harbour · 0.900"]);
    expect(screen.getByLabelText("Location")).toHaveValue("");

    await user.click(buttons[1]);
    expect(screen.getByLabelText("Location")).toHaveValue(chainLocationId);
    await user.click(screen.getByRole("button", { name: "Save header" }));
    await waitFor(() => expect(calls.some((c) => c.method === "PATCH" && c.path === base)).toBe(true));
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ vendor_location_id: chainLocationId });
    expect(await screen.findByRole("heading", { name: /Millstone Market/ })).toBeInTheDocument();
  });
});
