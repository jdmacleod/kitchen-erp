import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";
import { manualPurchase } from "./purchase-fixtures";

// Spec 10, Shop: purchases, and UI-3.22: find a store or item, and sort by Date,
// Where and Total, both on the server and kept in the URL (issue 292).

function routes(list: (call: RecordedCall) => Response) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /inbox": () => jsonResponse(200, { items: [], reading: { count: 0, oldest_at: null, stalled: false } }),
    "GET /purchases": list,
    "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
  };
}

const listCalls = (calls: RecordedCall[]) => calls.filter((c) => c.method === "GET" && c.path.split("?")[0] === "/purchases" && !c.query.get("status"));

describe("purchases: find and sort (UI-3.22)", () => {
  it("searches on the server, says how many match, and clears a search that found nothing", async () => {
    const calls = mockApi(
      routes((call) =>
        call.query.get("q") === "blueberries"
          ? jsonResponse(200, { items: [manualPurchase], next_cursor: null })
          : call.query.get("q")
            ? jsonResponse(200, { items: [], next_cursor: null })
            : jsonResponse(200, { items: [manualPurchase], next_cursor: null }),
      ),
    );
    const user = userEvent.setup();
    renderApp("/shop/purchases");
    await screen.findByRole("table", { name: "Purchases" });

    await user.type(screen.getByLabelText("Find a store or item"), "blueberries");
    expect(await screen.findByText("1 purchase matches “blueberries”.")).toBeInTheDocument();
    expect(listCalls(calls).some((c) => c.query.get("q") === "blueberries")).toBe(true);

    await user.clear(screen.getByLabelText("Find a store or item"));
    await user.type(screen.getByLabelText("Find a store or item"), "kumquat");
    const empty = await screen.findByRole("region", { name: "No purchases match “kumquat”" });
    expect(screen.queryByRole("region", { name: "No purchases yet" })).toBeNull();
    await user.click(within(empty).getByRole("button", { name: "Clear search" }));
    expect(await screen.findByRole("table", { name: "Purchases" })).toBeInTheDocument();
    expect(screen.getByLabelText("Find a store or item")).toHaveValue("");
  });

  it("sorts by a heading on the server, marks it with aria-sort, and reverses on a second click", async () => {
    const calls = mockApi(routes(() => jsonResponse(200, { items: [manualPurchase], next_cursor: null })));
    const user = userEvent.setup();
    renderApp("/shop/purchases");
    await screen.findByRole("table", { name: "Purchases" });
    const heading = (name: string) => within(screen.getByRole("table", { name: "Purchases" })).getByRole("columnheader", { name: new RegExp(`^${name}`) });
    expect(heading("Date")).toHaveAttribute("aria-sort", "descending");
    expect(heading("Total")).toHaveAttribute("aria-sort", "none");
    // The default order sends no sort at all.
    expect(listCalls(calls).every((c) => !c.query.get("sort"))).toBe(true);

    await user.click(within(heading("Total")).getByRole("button", { name: /Total/ }));
    await waitFor(() => expect(listCalls(calls).some((c) => c.query.get("sort") === "total" && c.query.get("dir") === "desc")).toBe(true));
    expect(heading("Total")).toHaveAttribute("aria-sort", "descending");
    expect(heading("Date")).toHaveAttribute("aria-sort", "none");

    await user.click(within(heading("Total")).getByRole("button", { name: /Total/ }));
    await waitFor(() => expect(listCalls(calls).some((c) => c.query.get("sort") === "total" && c.query.get("dir") === "asc")).toBe(true));
    expect(heading("Total")).toHaveAttribute("aria-sort", "ascending");

    // Where starts A to Z.
    await user.click(within(heading("Where")).getByRole("button", { name: /Where/ }));
    await waitFor(() => expect(listCalls(calls).some((c) => c.query.get("sort") === "where" && c.query.get("dir") === "asc")).toBe(true));
  });

  it("opens with the sort and search kept in the address", async () => {
    const calls = mockApi(routes(() => jsonResponse(200, { items: [manualPurchase], next_cursor: null })));
    renderApp("/shop/purchases?q=honey&sort=where&dir=desc");
    const table = await screen.findByRole("table", { name: "Purchases" });
    expect(within(table).getByRole("columnheader", { name: /^Where/ })).toHaveAttribute("aria-sort", "descending");
    expect(screen.getByLabelText("Find a store or item")).toHaveValue("honey");
    expect(listCalls(calls).some((c) => c.query.get("q") === "honey" && c.query.get("sort") === "where" && c.query.get("dir") === "desc")).toBe(true);
  });
});
