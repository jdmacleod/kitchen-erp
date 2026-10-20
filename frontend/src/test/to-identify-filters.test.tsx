import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ToIdentifyGroup } from "../api/toIdentify";
import { filterGroups, matchesText, storeCounts } from "../components/purchases/identifyList";
import { units } from "./catalog-fixtures";
import { chainVendor, chainVendorId, marketVendor, marketVendorId } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

let seq = 0;
function group(
  vendor: "chain" | "market",
  text: string,
  lineCount = 1,
  raw?: string,
): ToIdentifyGroup {
  const v =
    vendor === "chain"
      ? { id: chainVendorId, name: chainVendor.name }
      : { id: marketVendorId, name: marketVendor.name };
  const lines = Array.from({ length: lineCount }, () => {
    seq += 1;
    const n = String(seq).padStart(4, "0");
    return {
      line_id: `0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f${n}`,
      purchase_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8004",
      raw_text: raw ?? text,
      purchased_at: "2026-09-20T18:05:00Z",
      line_total: "1.00",
      qty: "1",
      unit: "each",
    };
  });
  return { vendor: v, raw_text_norm: text, line_count: lineCount, lines };
}

const queue = [
  group("chain", "GRN ONIONS"),
  group("market", "PURPLE YAMS", 3),
  group("chain", "OAT BREAD", 2, "0042 OAT BREAD 24OZ"),
  group("market", "GRN ONIONS"),
];

function routes(items: ToIdentifyGroup[]) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    "GET /products/search": () => jsonResponse(200, { items: [] }),
    "GET /to-identify": () => jsonResponse(200, { items }),
  };
}

function cardNames() {
  return within(screen.getByRole("list", { name: "Lines to identify" }))
    .getAllByRole("group")
    .map((g) => g.getAttribute("aria-label"));
}

describe("to-identify filters", () => {
  it("matches typed words at word starts, in the wording or a line as printed", () => {
    expect(matchesText(queue[0], "grn on")).toBe(true);
    expect(matchesText(queue[0], "nions")).toBe(false);
    expect(matchesText(queue[2], "24oz")).toBe(true);
    expect(matchesText(queue[2], "")).toBe(true);
  });

  it("keeps one store's groups and orders them as asked", () => {
    const market = filterGroups(queue, { store: marketVendorId, q: "", sort: "newest" });
    expect(market.map((g) => g.raw_text_norm)).toEqual(["PURPLE YAMS", "GRN ONIONS"]);
    const az = filterGroups(queue, { store: "", q: "", sort: "name" });
    expect(az.map((g) => g.raw_text_norm)).toEqual([
      "GRN ONIONS",
      "GRN ONIONS",
      "OAT BREAD",
      "PURPLE YAMS",
    ]);
    const most = filterGroups(queue, { store: "", q: "", sort: "lines" });
    expect(most[0].raw_text_norm).toBe("PURPLE YAMS");
    expect(storeCounts(queue)).toEqual([
      { id: chainVendorId, name: chainVendor.name, groups: 2 },
      { id: marketVendorId, name: marketVendor.name, groups: 2 },
    ]);
  });

  it("filters the list by store and wording from the address", async () => {
    mockApi(routes(queue));
    renderApp(`/shop/receipts/identify?store=${marketVendorId}&q=grn`);
    await screen.findByRole("list", { name: "Lines to identify" });
    expect(cardNames()).toEqual([`${marketVendor.name}: GRN ONIONS`]);
    expect(screen.getByText("Showing 1 of 1 group (4 in all)")).toBeInTheDocument();
    expect(screen.getByLabelText("Wording")).toHaveValue("grn");
    expect(screen.getByLabelText("Store")).toHaveValue(marketVendorId);
  });

  it("narrows as the person picks a store, and clears back to everything", async () => {
    mockApi(routes(queue));
    const user = userEvent.setup();
    renderApp("/shop/receipts/identify");
    await screen.findByRole("list", { name: "Lines to identify" });
    expect(cardNames()).toHaveLength(4);

    await user.selectOptions(screen.getByLabelText("Store"), `${chainVendor.name} (2)`);
    expect(cardNames()).toEqual([
      `${chainVendor.name}: GRN ONIONS`,
      `${chainVendor.name}: OAT BREAD`,
    ]);

    await user.type(screen.getByLabelText("Wording"), "yams");
    expect(await screen.findByText("No lines match")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    expect(await screen.findByText("Showing 4 of 4 groups")).toBeInTheDocument();
    expect(screen.getByLabelText("Store")).toHaveValue("");
  });

  it("shows 25 groups at a time", async () => {
    const many = Array.from({ length: 30 }, (_, i) => group("chain", `ITEM ${i + 1}`));
    mockApi(routes(many));
    const user = userEvent.setup();
    renderApp("/shop/receipts/identify");
    await screen.findByRole("list", { name: "Lines to identify" });
    expect(cardNames()).toHaveLength(25);
    await user.click(screen.getByRole("button", { name: "Show 5 more (5 waiting)" }));
    expect(cardNames()).toHaveLength(30);
  });
});
