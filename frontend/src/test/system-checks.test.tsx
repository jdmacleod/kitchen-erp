import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { formatSince } from "../lib/format";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

describe("System says how the lookup helper is doing", () => {
  it("shows each check's summary under its name", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () =>
        jsonResponse(200, {
          status: "degraded",
          checks: {
            database: { status: "ok" },
            lookup_helper: { status: "degraded", summary: "last checked in 3 h ago" },
          },
        }),
    });
    renderApp("/settings/system");
    const checks = await screen.findByRole("list", { name: "Checks" });
    const helper = within(checks).getByText("lookup helper").closest("li");
    expect(helper).toHaveTextContent("last checked in 3 h ago");
    expect(helper).toHaveTextContent("degraded");
  });
});

describe("since when something has waited", () => {
  const now = new Date(2026, 9, 20, 14, 0);

  it("is the time alone today", () => {
    const today = new Date(2026, 9, 20, 10, 50).toISOString();
    expect(formatSince(today, now)).not.toMatch(/Oct/);
    expect(formatSince(today, now)).toMatch(/10:50/);
  });

  it("carries the date on another day", () => {
    const lastWeek = new Date(2026, 9, 13, 10, 50).toISOString();
    expect(formatSince(lastWeek, now)).toMatch(/Oct 13/);
    expect(formatSince(lastWeek, now)).toMatch(/10:50/);
  });
});
