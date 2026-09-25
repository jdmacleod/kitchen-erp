import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useLocation } from "react-router";
import { describe, expect, it } from "vitest";
import { REDIRECTS } from "../App";
import type { SearchResults } from "../api/search";
import { DEFAULT_FEATURES, FOOTER_SECTIONS, MAIN_SECTIONS, visibleSections, type NavSection } from "../components/Nav";
import { RedirectTo } from "../components/RedirectTo";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp, type RouteHandler } from "./helpers";

const NEVER = () => new Promise<Response>(() => {}) as unknown as Response;
const quietInbox = { items: [], reading: { count: 0, oldest_at: null, stalled: false } };

/** Mount at Settings → System, the page with the fewest dependencies. */
function mount(routes: Record<string, RouteHandler> = {}, path = "/settings/system") {
  const calls = mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /inbox": () => jsonResponse(200, quietInbox),
    ...routes,
  });
  renderApp(path);
  return calls;
}

const sidebar = () => screen.getAllByRole("navigation", { name: "Main" })[0];

describe("navigation sections (UI-2.1, UI-2.2)", () => {
  it("renders the Phase 1–2 sections while /health has not answered (D11)", async () => {
    mount({ "GET /health": NEVER });
    await screen.findByRole("heading", { name: "System" });
    const nav = sidebar();
    for (const name of ["Home", "Shop", "Catalog", "Settings"]) {
      expect(within(nav).getByRole("link", { name: new RegExp(`^${name}`) })).toBeInTheDocument();
    }
  });

  it("expands only the active section", async () => {
    mount();
    await screen.findByRole("heading", { name: "System" });
    const nav = sidebar();
    expect(within(nav).getByRole("link", { name: "System" })).toHaveAttribute("aria-current", "page");
    expect(within(nav).getByRole("link", { name: "Kitchens" })).toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Receipts" })).not.toBeInTheDocument();
    expect(within(nav).queryByRole("link", { name: "Ingredients" })).not.toBeInTheDocument();
  });

  it("drops the retired nav items (UI-2.13, UI-2.14)", async () => {
    mount({}, "/shop/purchases");
    const nav = await waitFor(() => sidebar());
    await within(nav).findByRole("link", { name: "Receipts" });
    for (const name of ["New purchase", "Needs a bridge", "To identify", "Map", "Log out everywhere"]) {
      expect(within(nav).queryByRole("link", { name })).not.toBeInTheDocument();
      expect(within(nav).queryByRole("button", { name })).not.toBeInTheDocument();
    }
  });

  it("takes features from a 503 health body, and shows its status (D11)", async () => {
    mount({ "GET /health": () => jsonResponse(503, { status: "failed", features: ["catalog", "shop"] }) });
    await screen.findByRole("heading", { name: "System" });
    await waitFor(() => expect(screen.getAllByTestId("health-status")[0]).toHaveTextContent("failed"));
  });

  it("lets features add sections in their fixed place, never remove them", () => {
    const later: NavSection = { key: "cook", label: "Cook", to: "/cook/recipes", prefix: "/cook", feature: "cook", items: [] };
    const sections = [MAIN_SECTIONS[0], later, MAIN_SECTIONS[1]];
    expect(visibleSections(sections, undefined).map((s) => s.key)).toEqual(["home", "shop"]);
    expect(visibleSections(sections, []).map((s) => s.key)).toEqual(["home", "shop"]);
    expect(visibleSections(sections, ["cook"]).map((s) => s.key)).toEqual(["home", "cook", "shop"]);
    expect(visibleSections(FOOTER_SECTIONS, []).map((s) => s.key)).toEqual(["catalog", "settings"]);
    expect([...DEFAULT_FEATURES]).toEqual(["catalog", "shop"]);
  });

  it("moves Log out everywhere to Settings → System", async () => {
    const calls = mount({ "POST /auth/logout-all": () => jsonResponse(204) });
    await userEvent.click(await screen.findByRole("button", { name: "Log out everywhere" }));
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/auth/logout-all")).toBe(true);
  });
});

function Where() {
  const location = useLocation();
  return <p data-testid="where">{`${location.pathname}${location.search}${location.hash}`}</p>;
}

function redirectFrom(url: string, from: string, to: string, query?: Record<string, string>) {
  const { unmount } = render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path={from} element={<RedirectTo to={to} query={query} />} />
        <Route path="*" element={<Where />} />
      </Routes>
    </MemoryRouter>,
  );
  const where = screen.getByTestId("where").textContent;
  unmount();
  return where;
}

describe("redirects from the retired paths (UI-2.3)", () => {
  it.each(REDIRECTS)("sends $from to $to", ({ from, to, query }) => {
    const url = from.replace(":id", "abc-123");
    const expected = to.replace(":id", "abc-123") + (query ? `?${new URLSearchParams(query)}` : "");
    expect(redirectFrom(url, from, to, query)).toBe(expected);
  });

  it("keeps the query string and hash, after any it adds", () => {
    const map = REDIRECTS.find((r) => r.from === "/map")!;
    expect(redirectFrom("/map?location=L1&place=location", map.from, map.to, map.query)).toBe(
      "/catalog/vendors?view=map&location=L1&place=location",
    );
    const ingredient = REDIRECTS.find((r) => r.from === "/ingredients/:id")!;
    expect(redirectFrom("/ingredients/i9#density-heading", ingredient.from, ingredient.to)).toBe(
      "/catalog/ingredients/i9#density-heading",
    );
  });

  it("covers every retired path the spec lists", () => {
    expect(REDIRECTS.map((r) => r.from)).toEqual(
      expect.arrayContaining(["/map", "/compare", "/to-identify", "/price-book/needs-bridge", "/settings/home-bases"]),
    );
  });

  it("lands on the page in the real app", async () => {
    mount({}, "/settings/home-bases");
    expect(await screen.findByRole("heading", { name: "Kitchens" })).toBeInTheDocument();
  });
});

const results: SearchResults = {
  ingredients: [
    { kind: "ingredient", id: "i1", label: "Basil", detail: "Herbs", route: "/settings/system?from=basil", category_key: "produce" },
  ],
  products: [
    { kind: "product", id: "p1", label: "Basil bunch", detail: "Leafwise · Basil", route: "/settings/system?from=bunch", category_key: "produce" },
  ],
  vendors: [],
};

describe("the search palette (UI-2.8)", () => {
  it("opens with ⌘K or Ctrl+K and shows the hint before typing", async () => {
    mount();
    await screen.findByRole("heading", { name: "System" });
    const user = userEvent.setup();
    await user.keyboard("{Control>}k{/Control}");
    const dialog = await screen.findByRole("dialog", { name: "Search" });
    expect(within(dialog).getByText("Type a product, ingredient, vendor or barcode.")).toBeInTheDocument();
    expect(within(dialog).getByRole("combobox")).toHaveFocus();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await user.keyboard("{Meta>}k{/Meta}");
    expect(await screen.findByRole("dialog", { name: "Search" })).toBeInTheDocument();
  });

  it("groups results with ingredients first, hides empty groups, and opens the chosen one by keyboard", async () => {
    const calls = mount({ "GET /search": () => jsonResponse(200, results) });
    await screen.findByRole("heading", { name: "System" });
    const user = userEvent.setup();
    const opener = screen.getAllByRole("button", { name: /^Search/ })[0];
    await user.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "Search" });
    await user.type(within(dialog).getByRole("combobox"), "basil");

    const listbox = await within(dialog).findByRole("listbox");
    const groups = within(listbox).getAllByRole("group");
    // Ingredients first, and the empty Vendors group is not rendered at all.
    expect(groups).toHaveLength(2);
    expect(groups[0]).toHaveAccessibleName("Ingredients");
    expect(groups[1]).toHaveAccessibleName("Products");
    const options = within(listbox).getAllByRole("option");
    expect(options[0]).toHaveAttribute("aria-selected", "true");
    expect(within(options[0]).getByText("Herbs")).toHaveClass("cat-produce");

    await user.keyboard("{ArrowDown}");
    expect(options[1]).toHaveAttribute("aria-selected", "true");
    expect(within(dialog).getByRole("combobox")).toHaveAttribute("aria-activedescendant", options[1].id);
    await user.keyboard("{Enter}");

    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    const search = calls.find((c) => c.path.startsWith("/search"));
    expect(search?.query.get("q")).toBe("basil");
  });

  it("says when nothing matches, and offers to add a product", async () => {
    mount({ "GET /search": () => jsonResponse(200, { ingredients: [], products: [], vendors: [] }) });
    await screen.findByRole("heading", { name: "System" });
    const user = userEvent.setup();
    await user.keyboard("{Control>}k{/Control}");
    const dialog = await screen.findByRole("dialog", { name: "Search" });
    await user.type(within(dialog).getByRole("combobox"), "zzqx");
    expect(await within(dialog).findByText(/No matches for ‘zzqx’/)).toBeInTheDocument();
    expect(within(dialog).getByRole("link", { name: "Add product" })).toHaveAttribute("href", "/catalog/products");
  });

  it("says search isn't working, never 'no matches', when it fails", async () => {
    let up = false;
    mount({ "GET /search": () => (up ? jsonResponse(200, results) : errorResponse(500, "internal", "boom")) });
    await screen.findByRole("heading", { name: "System" });
    const user = userEvent.setup();
    await user.keyboard("{Control>}k{/Control}");
    const dialog = await screen.findByRole("dialog", { name: "Search" });
    await user.type(within(dialog).getByRole("combobox"), "basil");
    const alert = await within(dialog).findByRole("alert");
    expect(alert).toHaveTextContent("Search isn't working right now.");
    expect(within(dialog).queryByText(/No matches/)).not.toBeInTheDocument();
    up = true;
    await user.click(within(alert).getByRole("button", { name: "Try again" }));
    expect(await within(dialog).findByRole("listbox")).toBeInTheDocument();
  });

  it("keeps focus inside, and returns it to the opener on close", async () => {
    mount();
    await screen.findByRole("heading", { name: "System" });
    const user = userEvent.setup();
    const opener = screen.getAllByRole("button", { name: /^Search/ })[0];
    await user.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "Search" });
    await user.tab();
    expect(dialog.contains(document.activeElement)).toBe(true);
    await user.keyboard("{Escape}");
    expect(opener).toHaveFocus();
  });
});

describe("Capture (UI-2.10)", () => {
  it("offers the three modes and closes on Escape", async () => {
    mount();
    await screen.findByRole("heading", { name: "System" });
    const user = userEvent.setup();
    const opener = screen.getAllByRole("button", { name: "Capture" })[0];
    await user.click(opener);
    const dialog = await screen.findByRole("dialog", { name: "Capture" });
    expect(within(dialog).getByRole("link", { name: /Log a shelf price/ })).toHaveAttribute("href", "/shop/shelf-prices");
    expect(within(dialog).getByRole("link", { name: /Scan a receipt/ })).toHaveAttribute("href", "/shop/receipts");
    expect(within(dialog).getByRole("link", { name: /Enter a purchase/ })).toHaveAttribute("href", "/shop/purchases/new");
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(opener).toHaveFocus();
  });
});

describe("review follow-ups on the shell", () => {
  it("does not open a result from an earlier search on Enter", async () => {
    let answer: (r: Response) => void = () => {};
    let calls = 0;
    const navCalls = mount({
      "GET /search": () => {
        calls += 1;
        // The first search answers at once; the second never does.
        return calls === 1 ? jsonResponse(200, results) : new Promise<Response>((r) => (answer = r));
      },
    });
    await screen.findByRole("heading", { name: "System" });
    const user = userEvent.setup();
    await user.keyboard("{Control>}k{/Control}");
    const dialog = await screen.findByRole("dialog", { name: "Search" });
    const box = within(dialog).getByRole("combobox");
    await user.type(box, "basil");
    await within(dialog).findByRole("listbox");

    await user.type(box, "x");
    await user.keyboard("{Enter}");
    // Still searching for "basilx": nothing opened, and the old list is not offered.
    expect(screen.getByRole("dialog", { name: "Search" })).toBeInTheDocument();
    expect(within(dialog).queryByRole("listbox")).not.toBeInTheDocument();
    await waitFor(() => expect(navCalls.filter((c) => c.path.startsWith("/search")).length).toBe(2));
    answer(jsonResponse(200, results));
    expect(await within(dialog).findByRole("listbox")).toBeInTheDocument();
  });

  it("sends focus to the new page after a result is chosen, not back to Search", async () => {
    mount({ "GET /search": () => jsonResponse(200, results) });
    await screen.findByRole("heading", { name: "System" });
    const user = userEvent.setup();
    await user.click(screen.getAllByRole("button", { name: /^Search/ })[0]);
    const dialog = await screen.findByRole("dialog", { name: "Search" });
    await user.type(within(dialog).getByRole("combobox"), "basil");
    await within(dialog).findByRole("listbox");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
    expect(screen.getByRole("main")).toHaveFocus();
  });

  it("shows ! when a refresh fails, even with an older count cached (D6)", async () => {
    let ok = true;
    const { client } = (() => {
      mockApi({
        "GET /auth/me": () => jsonResponse(200, adminUser),
        "GET /health": () => jsonResponse(200, { status: "ok" }),
        "GET /inbox": () =>
          ok
            ? jsonResponse(200, { items: [{ kind: "identify", title: "1 receipt line to identify", detail: "", action_label: "Review lines", action_route: "/shop/receipts/identify", created_at: "2026-09-24T18:00:00Z" }], reading: { count: 0, oldest_at: null, stalled: false } })
            : errorResponse(500, "internal", "boom"),
      });
      return renderApp("/settings/system");
    })();
    expect((await screen.findAllByLabelText("1 thing needs you")).length).toBeGreaterThan(0);
    ok = false;
    await act(async () => {
      await client.refetchQueries({ queryKey: ["inbox"] });
    });
    expect((await screen.findAllByRole("img", { name: "Couldn't check what needs you" })).length).toBeGreaterThan(0);
    expect(screen.queryByLabelText("1 thing needs you")).not.toBeInTheDocument();
  });
});
