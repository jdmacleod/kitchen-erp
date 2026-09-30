import { screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { adminUser, jsonResponse, mainRegion, mockApi, renderApp } from "./helpers";

const id = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9a01";

describe("a page that fails to render", () => {
  it("keeps the app and says so, and navigating away starts clean", async () => {
    // A tab left on the previous build rendered the link page's answer as an
    // ingredient and went blank. Any answer of the wrong shape does the same.
    const quiet = vi.spyOn(console, "error").mockImplementation(() => {});
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      [`GET /ingredients/${id}`]: () => jsonResponse(200, { to_review: [], skipped: [] }),
      "GET /units": () => jsonResponse(200, { items: [] }),
      "GET /products": () => jsonResponse(200, { items: [], next_cursor: null }),
      "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    const user = userEvent.setup();
    renderApp(`/catalog/ingredients/${id}`);

    const message = await screen.findByText("This page couldn't be shown.");
    expect(mainRegion()).toContainElement(message);
    const alert = within(mainRegion()).getByRole("alert");
    expect(within(alert).getByRole("button", { name: "Reload" })).toBeInTheDocument();
    // The navigation is still there to leave by.
    const nav = screen.getAllByRole("navigation")[0];
    await user.click(within(nav).getAllByRole("link", { name: /Shop|Purchases/ })[0]);
    expect(await screen.findByRole("heading", { level: 1, name: /Purchases/ })).toBeInTheDocument();
    expect(within(mainRegion()).queryByText("This page couldn't be shown.")).not.toBeInTheDocument();
    quiet.mockRestore();
  });
});
