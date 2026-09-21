import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { adminUser, errorResponse, jsonResponse, memberUser, mockApi, renderApp } from "./helpers";

describe("route guards", () => {
  it("sends an unauthenticated visitor to /login", async () => {
    const calls = mockApi({
      "GET /auth/me": () => errorResponse(401, "unauthenticated", "Not signed in."),
    });
    renderApp("/vendors");

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.queryByText("No vendors yet")).not.toBeInTheDocument();
    expect(calls.map((c) => `${c.method} ${c.path}`)).toEqual(["GET /auth/me"]);
    expect(calls[0]?.headers.get("Accept")).toBe("application/json");
  });

  it("redirects / to /ingredients when signed in and shows the health word", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "degraded" }),
      "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    renderApp("/");

    expect(await screen.findByRole("heading", { name: "Ingredients" })).toBeInTheDocument();
    expect(await screen.findByTestId("health-status")).toHaveTextContent("degraded");
  });

  it("shows members a not-permitted page for user management", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, memberUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
    });
    renderApp("/settings/users");

    expect(await screen.findByRole("heading", { name: "Not permitted" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "Users" })).not.toBeInTheDocument();
  });
});
