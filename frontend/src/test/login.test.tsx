import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp } from "./helpers";

describe("login", () => {
  it("submits email and password to the API and lands on the catalog", async () => {
    const calls = mockApi({
      "GET /auth/me": () => errorResponse(401, "unauthenticated", "Not signed in."),
      "POST /auth/login": () => jsonResponse(200, { user: adminUser }),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    const user = userEvent.setup();
    renderApp("/login");

    await user.type(await screen.findByLabelText("Email"), "admin@example.com");
    await user.type(screen.getByLabelText("Password"), "correct horse");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("heading", { name: "Ingredients" })).toBeInTheDocument();
    expect(await screen.findByText("No ingredients yet")).toBeInTheDocument();

    const login = calls.find((c) => c.path === "/auth/login");
    expect(login).toBeDefined();
    expect(login?.method).toBe("POST");
    expect(login?.body).toEqual({ email: "admin@example.com", password: "correct horse" });
    expect(login?.headers.get("Content-Type")).toBe("application/json");
  });

  it("shows the server's message inline on 401", async () => {
    mockApi({
      "GET /auth/me": () => errorResponse(401, "unauthenticated", "Not signed in."),
      "POST /auth/login": () => errorResponse(401, "invalid_credentials", "Invalid email or password."),
    });
    const user = userEvent.setup();
    renderApp("/login");

    await user.type(await screen.findByLabelText("Email"), "admin@example.com");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password.");
    // Still on the login page.
    expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("redirects a signed-in visitor away from /login", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    renderApp("/login");
    await waitFor(() => expect(screen.getByRole("heading", { name: "Ingredients" })).toBeInTheDocument());
  });
});
