import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { ApiToken } from "../api/types";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/;

describe("api tokens", () => {
  it("shows a new token's plaintext exactly once and never in the list", async () => {
    const created: ApiToken = {
      id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5aaa",
      name: "phone",
      scopes: ["*"],
      created_at: "2026-02-03T04:05:06Z",
      last_used_at: null,
      revoked_at: null,
    };
    let items: ApiToken[] = [];
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /api-tokens": () => jsonResponse(200, { items }),
      "POST /api-tokens": () => {
        items = [created];
        return jsonResponse(201, { token: created, plaintext: "kerp_secret_plaintext_value" });
      },
    });
    const user = userEvent.setup();
    renderApp("/settings/tokens");

    expect(await screen.findByText("No tokens yet")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Name"), "phone");
    await user.click(screen.getByRole("button", { name: "Create token" }));

    const output = await screen.findByTestId("token-plaintext");
    expect(output).toHaveTextContent("kerp_secret_plaintext_value");

    const post = calls.find((c) => c.method === "POST" && c.path === "/api-tokens");
    expect(post?.body).toEqual({ name: "phone", scopes: ["*"] });
    expect(post?.headers.get("Idempotency-Key")).toMatch(UUID);

    // The list refreshes and shows the token without its plaintext.
    const list = await screen.findByRole("list", { name: "Your tokens" });
    expect(within(list).getByText("phone")).toBeInTheDocument();
    expect(within(list).queryByText(/kerp_secret/)).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Dismiss" }));
    await waitFor(() => expect(screen.queryByTestId("token-plaintext")).not.toBeInTheDocument());
    expect(screen.queryByText(/kerp_secret/)).not.toBeInTheDocument();
    expect(document.body.innerHTML).not.toContain("kerp_secret");
  });

  it("revokes a token after confirmation", async () => {
    const token: ApiToken = {
      id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5abb",
      name: "laptop",
      scopes: ["*"],
      created_at: "2026-02-03T04:05:06Z",
      last_used_at: "2026-02-04T04:05:06Z",
      revoked_at: null,
    };
    let items: ApiToken[] = [token];
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /api-tokens": () => jsonResponse(200, { items }),
      [`POST /api-tokens/${token.id}/revoke`]: () => {
        const revoked = { ...token, revoked_at: "2026-02-05T00:00:00Z" };
        items = [revoked];
        return jsonResponse(200, revoked);
      },
    });
    const user = userEvent.setup();
    renderApp("/settings/tokens");

    await user.click(await screen.findByRole("button", { name: "Revoke laptop" }));
    await user.click(screen.getByRole("button", { name: "Confirm revoke" }));

    await waitFor(() => expect(screen.getByText(/Revoked/)).toBeInTheDocument());
    expect(screen.queryByRole("button", { name: "Revoke laptop" })).not.toBeInTheDocument();
    expect(calls.some((c) => c.path === `/api-tokens/${token.id}/revoke`)).toBe(true);
  });

  it("makes a narrow token for an enrichment tool and says what each can do (D16)", async () => {
    const tool: ApiToken = {
      id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5acd",
      name: "enricher",
      scopes: ["vendors:read", "vendors:suggest"],
      created_at: "2026-02-03T04:05:06Z",
      last_used_at: null,
      revoked_at: null,
    };
    const old: ApiToken = { ...tool, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f5ace", name: "capture app", scopes: ["*"] };
    let items: ApiToken[] = [old];
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /api-tokens": () => jsonResponse(200, { items }),
      "POST /api-tokens": () => {
        items = [old, tool];
        return jsonResponse(201, { token: tool, plaintext: "kerp_tool_value" });
      },
    });
    const user = userEvent.setup();
    renderApp("/settings/tokens");

    const list = await screen.findByRole("list", { name: "Your tokens" });
    expect(within(list).getByText(/Full access/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /Full access/ })).toBeChecked();
    const suggest = screen.getByRole("radio", { name: /Read and suggest vendor facts/ });
    expect(suggest).toHaveAccessibleDescription(/Cannot see notes, home bases, purchases or receipts/);
    await user.type(screen.getByLabelText("Name"), "enricher");
    await user.click(suggest);
    await user.click(screen.getByRole("button", { name: "Create token" }));
    await screen.findByTestId("token-plaintext");
    const post = calls.find((c) => c.method === "POST" && c.path === "/api-tokens");
    expect(post?.body).toEqual({ name: "enricher", scopes: ["vendors:read", "vendors:suggest"] });
    expect(await within(list).findByText(/Read and suggest vendor facts/)).toBeInTheDocument();
  });
});
