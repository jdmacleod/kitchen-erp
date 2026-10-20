import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ToIdentifyGroup } from "../api/toIdentify";
import {
  filterGroups,
  matchesText,
  searchHref,
  searchWords,
  storeCounts,
} from "../components/purchases/identifyList";
import { units } from "./catalog-fixtures";
import { chainVendor, chainVendorId, marketVendor, marketVendorId } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

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
    "POST /to-identify/apply": (call: RecordedCall) =>
      jsonResponse(200, { applied: (call.body as { line_ids?: string[] }).line_ids?.length ?? 1 }),
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

describe("to-identify card hints", () => {
  it("applies a product the wording's ingredient already has in one click", async () => {
    const onions: ToIdentifyGroup = {
      ...group("chain", "GRN ONIONS"),
      candidates: [
        {
          id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9a01",
          name: "Green onions",
          brand: null,
          pack_qty: "1",
          pack_unit: "bunch",
        },
      ],
    };
    const calls = mockApi(routes([onions]));
    const user = userEvent.setup();
    renderApp("/shop/receipts/identify");
    await user.click(await screen.findByRole("button", { name: "Use Green onions · 1 bunch" }));
    const post = await waitFor(() => {
      const found = calls.find((c) => c.method === "POST" && c.path === "/to-identify/apply");
      expect(found).toBeDefined();
      return found;
    });
    expect(post?.body).toEqual({
      vendor_id: chainVendorId,
      raw_text_norm: "GRN ONIONS",
      product_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9a01",
    });
  });

  it("names the other stores where the wording waits, and lists them together", async () => {
    const withOthers = queue.map((g) =>
      g.raw_text_norm !== "GRN ONIONS"
        ? g
        : {
            ...g,
            elsewhere: [
              g.vendor.id === chainVendorId
                ? { vendor_id: marketVendorId, vendor_name: marketVendor.name, line_count: 1 }
                : { vendor_id: chainVendorId, vendor_name: chainVendor.name, line_count: 1 },
            ],
          },
    );
    mockApi(routes(withOthers));
    const user = userEvent.setup();
    renderApp(`/shop/receipts/identify?store=${chainVendorId}`);
    const card = await screen.findByRole("group", { name: `${chainVendor.name}: GRN ONIONS` });
    expect(card).toHaveTextContent(`Also waiting at ${marketVendor.name} (1).`);
    await user.click(within(card).getByRole("button", { name: "Show them together" }));
    await waitFor(() =>
      expect(cardNames()).toEqual([
        `${chainVendor.name}: GRN ONIONS`,
        `${marketVendor.name}: GRN ONIONS`,
      ]),
    );
    expect(screen.getByLabelText("Store")).toHaveValue("");
    expect(screen.getByLabelText("Wording")).toHaveValue("GRN ONIONS");
  });

  it("builds a store search link from the suggested name, or the wording", () => {
    const named = { ...queue[0], suggested_name: "Green onions", suggested_brand: "Hilltop" };
    expect(searchWords(named)).toBe("Hilltop Green onions");
    expect(searchWords(queue[0])).toBe("grn onions");
    const page = "https://shop.example.test/search?q={q}";
    expect(searchHref(page, "Green & red onions")).toBe(
      "https://shop.example.test/search?q=Green%20%26%20red%20onions",
    );
    expect(searchHref(null, "onions")).toBeNull();
    expect(searchHref(page, " ")).toBeNull();
    expect(searchHref("javascript:alert({q})", "onions")).toBeNull();
  });

  it("links a card to its store's search page in a new tab", async () => {
    const page = "https://shop.example.test/search?q={q}";
    const onions: ToIdentifyGroup = {
      ...queue[0],
      vendor: { ...queue[0].vendor, search_url: page },
      suggested_name: "Grn onions",
    };
    mockApi(routes([onions, queue[1]]));
    renderApp("/shop/receipts/identify");
    const link = await screen.findByRole("link", {
      name: `Search ${chainVendor.name} for “Grn onions”`,
    });
    expect(link).toHaveAttribute("href", "https://shop.example.test/search?q=Grn%20onions");
    expect(link).toHaveAttribute("target", "_blank");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(screen.getAllByRole("link", { name: /^Search / })).toHaveLength(1);
  });
});
