import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

describe("app shell", () => {
  it("logs out and returns to /login", async () => {
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
      "POST /auth/logout": () => jsonResponse(204),
    });
    const user = userEvent.setup();
    renderApp("/shop/purchases");

    expect(await screen.findByText("No purchases yet")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Log out" }));

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/auth/logout")).toBe(true);
  });

  it("opens and closes the phone menu from the keyboard", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    const user = userEvent.setup();
    renderApp("/shop/purchases");
    await screen.findByText("No purchases yet");

    const button = screen.getByRole("button", { name: "Menu" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    button.focus();
    await user.keyboard("{Enter}");
    expect(screen.getByRole("button", { name: "Close" })).toHaveAttribute("aria-expanded", "true");
    expect(document.getElementById("phone-menu")).not.toBeNull();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(document.getElementById("phone-menu")).toBeNull());
    expect(screen.getByRole("button", { name: "Menu" })).toHaveFocus();
  });
});
