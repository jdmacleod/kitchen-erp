import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { SearchResult, SearchResults } from "../api/search";
import { readRecents, rememberRecent } from "../lib/searchRecents";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

const KEY = "kerp.searchRecents";

const basil: SearchResult = { kind: "ingredient", id: "i1", label: "Basil", detail: "Herbs", route: "/settings/system?from=basil", category_key: "produce" };
const results: SearchResults = { ingredients: [basil], products: [], vendors: [] };

function mount() {
  mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /inbox": () => jsonResponse(200, { items: [], reading: { count: 0, oldest_at: null, stalled: false } }),
    "GET /search": () => jsonResponse(200, results),
  });
  renderApp("/settings/system");
}

afterEach(() => {
  localStorage.clear();
  vi.restoreAllMocks();
});

describe("search recents (G15)", () => {
  it("remembers an opened result and offers it before typing next time, by keyboard", async () => {
    mount();
    await screen.findByRole("heading", { name: "System" });
    const user = userEvent.setup();

    await user.keyboard("{Control>}k{/Control}");
    let dialog = await screen.findByRole("dialog", { name: "Search" });
    await user.type(within(dialog).getByRole("combobox"), "basil");
    await within(dialog).findByRole("option", { name: /Basil/ });
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());

    await user.keyboard("{Control>}k{/Control}");
    dialog = await screen.findByRole("dialog", { name: "Search" });
    const recent = within(dialog).getByRole("group", { name: "Recent" });
    expect(within(recent).getByRole("option", { name: /Basil/ })).toHaveAttribute("aria-selected", "true");
    expect(within(dialog).queryByText("Type a product, ingredient, vendor or barcode.")).toBeNull();
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  });

  it("keeps five, newest first, each once", () => {
    for (const n of [1, 2, 3, 4, 5, 6]) rememberRecent({ ...basil, id: `i${n}`, label: `Item ${n}` });
    rememberRecent({ ...basil, id: "i3", label: "Item 3" });
    expect(readRecents().map((r) => r.id)).toEqual(["i3", "i6", "i5", "i4", "i2"]);
  });

  it("drops malformed entries and any route that leaves the app", () => {
    localStorage.setItem(
      KEY,
      JSON.stringify([
        { ...basil, route: "//elsewhere.example/phish" },
        { ...basil, route: "https://elsewhere.example" },
        { ...basil, kind: "script" },
        { label: "no id" },
        { ...basil, id: "ok" },
      ]),
    );
    expect(readRecents().map((r) => r.id)).toEqual(["ok"]);
    localStorage.setItem(KEY, JSON.stringify([basil, { ...basil, label: "Basil again" }, { ...basil, id: "i2" }]));
    // Each result once, even if storage holds it twice.
    expect(readRecents().map((r) => r.id)).toEqual(["i1", "i2"]);
    localStorage.setItem(KEY, "not json");
    expect(readRecents()).toEqual([]);
  });

  it("falls back to the hint when storage fails", async () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("denied");
    });
    mount();
    await screen.findByRole("heading", { name: "System" });
    const user = userEvent.setup();
    await user.keyboard("{Control>}k{/Control}");
    const dialog = await screen.findByRole("dialog", { name: "Search" });
    expect(within(dialog).getByText("Type a product, ingredient, vendor or barcode.")).toBeInTheDocument();
  });
});
