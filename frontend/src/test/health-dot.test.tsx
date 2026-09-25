import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Health } from "../api/types";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp } from "./helpers";

function mountWith(body: Health | null) {
  mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => (body === null ? errorResponse(503, "unavailable", "down") : jsonResponse(200, body)),
    "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
  });
  renderApp("/shop/purchases");
}

/** The dot beside the status word; it is aria-hidden, so it is found by position. */
function dot(): HTMLElement {
  const live = screen.getByTestId("health-status").closest('[role="status"]')!;
  return live.querySelector("span[aria-hidden='true']") as HTMLElement;
}

/**
 * The shades are pinned because they were measured, not picked (issue #21).
 * Every -500 was under the 3:1 that WCAG 1.4.11 asks of non-text UI against the
 * page it sits on; the table in HealthStatus.tsx records what each one measures.
 * A revert to a lighter step should fail here rather than pass quietly.
 */
describe("health status dot", () => {
  it("uses a shade that clears the contrast floor in each theme", async () => {
    mountWith({ status: "ok" });
    await waitFor(() => expect(screen.getByTestId("health-status")).toHaveTextContent("ok"));
    expect(dot()).toHaveClass("bg-green-600", "dark:bg-green-500");
  });

  it("keeps degraded and failed apart, both above the floor", async () => {
    mountWith({ status: "degraded" });
    await waitFor(() => expect(screen.getByTestId("health-status")).toHaveTextContent("degraded"));
    // One step darker than the others: amber-600 measures 3.2 on the sidebar,
    // too close to the 3:1 floor to survive a palette tweak.
    expect(dot()).toHaveClass("bg-amber-700", "dark:bg-amber-500");
  });

  it("marks an unreachable API without relying on colour alone", async () => {
    mountWith(null);
    // The word carries the state; the dot is what people scan for it.
    await waitFor(() => expect(screen.getByTestId("health-status")).toHaveTextContent("unreachable"));
    expect(dot()).toHaveClass("bg-neutral-500", "dark:bg-neutral-400");
    expect(dot()).toHaveAttribute("aria-hidden", "true");
  });
});
