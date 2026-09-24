import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { chainLocation } from "./geo-fixtures";
import { manualPurchase } from "./purchase-fixtures";
import { adminUser, errorResponse, jsonResponse, mainRegion, memberUser, mockApi, renderApp } from "./helpers";

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

  /**
   * The regression contract for issue #15's landing change.
   *
   * `/` used to redirect to /ingredients; it is now a page that decides. Every
   * existing household's post-login destination therefore moves, and the one
   * thing that must never happen is a set-up household being shown the first-run
   * checklist and told to add a shop it has had for a year.
   */
  it("renders the home page at / for a set-up household, never the checklist", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "degraded" }),
      "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation] }),
      "GET /purchases": () => jsonResponse(200, { items: [manualPurchase], next_cursor: null }),
      "GET /to-identify": () => jsonResponse(200, { items: [] }),
      "GET /price-book/needs-bridge": () => jsonResponse(200, { items: [] }),
    });
    renderApp("/");

    expect(await screen.findByLabelText("Lately")).toBeInTheDocument();
    expect(within(mainRegion()).getByRole("heading", { name: "Home" })).toBeInTheDocument();
    expect(screen.queryByText("Add somewhere you shop")).not.toBeInTheDocument();
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
