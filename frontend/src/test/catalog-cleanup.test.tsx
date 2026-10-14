import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { CleanupSuggestion } from "../api/catalogCleanup";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

// Invented suggestions (issue 349).
const name = (n: number): CleanupSuggestion => ({
  key: `product_name:p${n}:abc`,
  kind: "product_name",
  subject_id: `0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a0${n}`,
  subject: `HARBOR LIGHT Lantern Jam ${n}, 8 oz`,
  before: `HARBOR LIGHT Lantern Jam ${n}, 8 oz`,
  after: `Lantern jam ${n}`,
  reason: "brand and pack have fields of their own",
  ready: true,
});
const unguessed: CleanupSuggestion = {
  key: "category:i1:x",
  kind: "category",
  subject_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7b01",
  subject: "zzyzx",
  before: "no category",
  after: "choose one",
  reason: "no likely category",
  ready: false,
};

function routes(items: () => CleanupSuggestion[], onPost: (call: RecordedCall) => void) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /catalog/cleanup": () => jsonResponse(200, { items: items() }),
    "POST /catalog/cleanup/apply": (call: RecordedCall) => {
      onPost(call);
      return jsonResponse(200, {
        applied: (call.body as { keys: string[] }).keys.length,
        gone: [],
      });
    },
    "POST /catalog/cleanup/skip": (call: RecordedCall) => {
      onPost(call);
      return new Response(null, { status: 204 });
    },
  };
}

describe("tidy the catalog (issue 349)", () => {
  it("groups suggestions and applies one, a whole group, or a skip", async () => {
    let items = [name(1), name(2), unguessed];
    const posts: RecordedCall[] = [];
    mockApi(
      routes(
        () => items,
        (call) => {
          posts.push(call);
          const keys = (call.body as { keys: string[] }).keys;
          items = items.filter((s) => !keys.includes(s.key));
        },
      ),
    );
    const user = userEvent.setup();
    renderApp("/catalog/cleanup");
    const names = await screen.findByRole("list", { name: "Product names" });
    expect(within(names).getAllByTestId("cleanup-row")).toHaveLength(2);
    expect(within(names).getAllByTestId("cleanup-row")[0]).toHaveTextContent(
      "HARBOR LIGHT Lantern Jam 1, 8 oz → Lantern jam 1",
    );
    // A category nobody could guess is chosen on its page, not accepted here.
    const cats = screen.getByRole("list", { name: "Categories" });
    expect(within(cats).getByRole("link", { name: "Choose on its page" })).toBeInTheDocument();
    expect(within(cats).queryByRole("button", { name: /Accept/ })).not.toBeInTheDocument();

    await user.click(within(cats).getByRole("button", { name: "Skip: zzyzx" }));
    await waitFor(() => expect(screen.queryByRole("list", { name: "Categories" })).toBeNull());
    expect(posts[0].path).toBe("/catalog/cleanup/skip");

    await user.click(screen.getByRole("button", { name: "Accept these 2" }));
    await waitFor(() => expect(posts).toHaveLength(2));
    expect(posts[1].body).toEqual({ keys: [name(1).key, name(2).key] });
    expect(await screen.findByText("Nothing to tidy")).toBeInTheDocument();
  });
});
