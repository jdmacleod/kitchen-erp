import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

describe("app shell", () => {
  it("logs out and returns to /login", async () => {
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
      "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
      "POST /auth/logout": () => jsonResponse(204),
    });
    const user = userEvent.setup();
    renderApp("/shop/purchases");

    expect(await screen.findByText("No purchases yet")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Log out" }));

    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/auth/logout")).toBe(true);
  });

  const shellApi = () =>
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
      "GET /inbox": () => jsonResponse(200, { items: [], reading: { count: 0, oldest_at: null, stalled: false } }),
      "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
    });
  const tabs = () => screen.getByRole("navigation", { name: "Tabs" });

  it("shows the five phone tabs, with Purchases current on its pages (UI-4.1)", async () => {
    shellApi();
    renderApp("/shop/purchases");
    await screen.findByText("No purchases yet");

    const bar = within(tabs());
    expect(bar.getAllByRole("link").map((l) => l.textContent)).toEqual(["Home", "Purchases"]);
    expect(bar.getAllByRole("button").map((b) => b.textContent)).toEqual(["Capture", "Search", "More"]);
    expect(bar.getByRole("link", { name: "Purchases" })).toHaveAttribute("aria-current", "page");
    expect(bar.getByRole("link", { name: "Home" })).not.toHaveAttribute("aria-current");
    expect(screen.queryByRole("button", { name: "Menu" })).toBeNull();
  });

  it("opens More as a sheet from the keyboard, and Escape returns to the tab", async () => {
    shellApi();
    const user = userEvent.setup();
    renderApp("/shop/purchases");
    await screen.findByText("No purchases yet");

    const more = within(tabs()).getByRole("button", { name: "More" });
    expect(more).toHaveAttribute("aria-expanded", "false");
    more.focus();
    await user.keyboard("{Enter}");
    const sheet = await screen.findByRole("dialog", { name: "More" });
    expect(more).toHaveAttribute("aria-expanded", "true");

    // Shop's other pages, Catalog and Settings (UI-4.9); Purchases has its own tab.
    const links = within(sheet).getAllByRole("link").map((l) => l.textContent);
    expect(links).toEqual(["Receipts", "Shelf prices", "Compare prices", "Ingredients", "Products", "Vendors", "Kitchens", "Users", "API tokens", "System"]);
    expect(within(sheet).getByRole("button", { name: "Log out" })).toBeInTheDocument();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "More" })).toBeNull());
    expect(more).toHaveFocus();
  });

  it("closes More when a page is chosen and lands on it", async () => {
    shellApi();
    const user = userEvent.setup();
    renderApp("/shop/purchases");
    await screen.findByText("No purchases yet");

    await user.click(within(tabs()).getByRole("button", { name: "More" }));
    await user.click(within(await screen.findByRole("dialog", { name: "More" })).getByRole("link", { name: "Receipts" }));

    expect(await screen.findByRole("heading", { level: 1, name: "Receipts" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "More" })).toBeNull();
    expect(within(tabs()).getByRole("link", { name: "Purchases" })).not.toHaveAttribute("aria-current");
  });

  it("opens Capture and Search from the tab bar", async () => {
    shellApi();
    const user = userEvent.setup();
    renderApp("/shop/purchases");
    await screen.findByText("No purchases yet");

    await user.click(within(tabs()).getByRole("button", { name: "Capture" }));
    expect(await screen.findByRole("dialog", { name: "Capture" })).toBeInTheDocument();

    // Opening another overlay replaces the one that is up.
    await user.click(within(tabs()).getByRole("button", { name: "Search" }));
    expect(await screen.findByRole("dialog", { name: "Search" })).toBeInTheDocument();
    expect(screen.queryByRole("dialog", { name: "Capture" })).toBeNull();

    // A phone has no Escape key, so the full-screen palette has its own Cancel.
    await user.click(within(screen.getByRole("dialog", { name: "Search" })).getByRole("button", { name: "Cancel" }));
    expect(screen.queryByRole("dialog", { name: "Search" })).toBeNull();
  });

  it("closes More when the page changes underneath it, as Back does", async () => {
    shellApi();
    const user = userEvent.setup();
    renderApp("/shop/purchases");
    await screen.findByText("No purchases yet");

    await user.click(within(tabs()).getByRole("button", { name: "More" }));
    expect(await screen.findByRole("dialog", { name: "More" })).toBeInTheDocument();
    // A navigation that did not come from the sheet: the Home tab, standing in
    // for the browser's Back button.
    await user.click(within(tabs()).getByRole("link", { name: "Home" }));
    await waitFor(() => expect(screen.queryByRole("dialog", { name: "More" })).toBeNull());

    // Opening again after the navigation still works.
    await user.click(within(tabs()).getByRole("button", { name: "More" }));
    expect(await screen.findByRole("dialog", { name: "More" })).toBeInTheDocument();
  });
});
