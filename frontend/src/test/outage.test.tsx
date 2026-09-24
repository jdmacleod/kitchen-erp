import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp } from "./helpers";

/**
 * What the app says while the backend is not answering (issue #25).
 *
 * The error handling was never missing: RequireAuth renders the status and a
 * Try again button. The problem was the wait before them, which looked exactly
 * like an ordinary slow load.
 */
describe("a backend that is not answering", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("distinguishes a slow wait from an ordinary one", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    mockApi({ "GET /auth/me": () => new Promise<Response>(() => {}) });
    renderApp("/vendors");

    const status = await screen.findByRole("status");
    expect(status).toHaveTextContent("Loading…");
    expect(status).not.toHaveTextContent("Still waiting");

    await vi.advanceTimersByTimeAsync(2_000);
    // Announced through the same live region, so it reads as news about this
    // wait rather than a second, competing status.
    await waitFor(() => expect(status).toHaveTextContent("Still waiting for the server"));
  });

  it("reaches the error and its retry without a second attempt of its own", async () => {
    let attempts = 0;
    mockApi({
      "GET /auth/me": () => {
        attempts += 1;
        return errorResponse(502, "bad_gateway", "Request failed with status 502.");
      },
    });
    renderApp("/vendors");

    expect(await screen.findByRole("alert")).toHaveTextContent("Request failed with status 502.");
    // One attempt, not two: the guard's own Try again is the retry, and an
    // automatic one only doubles the wait before anything true is on screen.
    expect(attempts).toBe(1);
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });

  it("recovers through Try again when the backend comes back", async () => {
    let up = false;
    mockApi({
      "GET /auth/me": () => (up ? jsonResponse(200, adminUser) : errorResponse(502, "bad_gateway", "Request failed with status 502.")),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /vendors": () => jsonResponse(200, { items: [] }),
      "GET /home-bases": () => jsonResponse(200, { items: [] }),
    });
    const user = userEvent.setup();
    renderApp("/vendors");

    await screen.findByRole("alert");
    up = true;
    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(await screen.findByRole("heading", { name: "Vendors" })).toBeInTheDocument();
  });
});
