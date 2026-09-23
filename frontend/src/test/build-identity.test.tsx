import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { buildIdentity } from "../components/HealthStatus";
import type { Health } from "../api/types";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp } from "./helpers";

function health(extra: Partial<Health> = {}): Health {
  return { status: "ok", ...extra };
}

function mountWith(healthBody: unknown) {
  mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () =>
      healthBody === null ? errorResponse(503, "unavailable", "down") : jsonResponse(200, healthBody),
    "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
  });
  renderApp("/purchases");
}

describe("build identity line", () => {
  it("shows the sha first and the version second on a release build", async () => {
    mountWith(health({ version: "v0.2.0", commit: "a1b2c3d", is_dev: false }));

    const line = await screen.findByTestId("build-identity");
    expect(line).toHaveTextContent("a1b2c3d · v0.2.0");
    // The sha identifies the build and is the half that survives a wrap, so it
    // leads. Order is the point of this assertion, not the text.
    expect(line.textContent!.indexOf("a1b2c3d")).toBeLessThan(line.textContent!.indexOf("v0.2.0"));
    expect(screen.queryByText("dev")).not.toBeInTheDocument();
  });

  it("badges a dev cut and does not print the sha twice", async () => {
    // `git describe --always --dirty` on an untagged tree returns the sha itself,
    // so the version restates it and is dropped.
    mountWith(health({ version: "4b4d8fc-dirty", commit: "4b4d8fc", is_dev: true }));

    const line = await screen.findByTestId("build-identity");
    expect(screen.getByTestId("build-dev-badge")).toHaveTextContent("dev");
    expect(line).toHaveTextContent("4b4d8fc-dirty");
    expect(line.textContent).not.toContain("·");
  });

  it("renders nothing when the API does not send the fields", async () => {
    mountWith(health());

    await screen.findByTestId("health-status");
    expect(screen.queryByTestId("build-identity")).not.toBeInTheDocument();
  });

  it("renders nothing while the check is pending", async () => {
    mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => new Promise<Response>(() => {}),
      "GET /purchases": () => jsonResponse(200, { items: [], next_cursor: null }),
    });
    renderApp("/purchases");

    expect(await screen.findByTestId("health-status")).toHaveTextContent("checking");
    expect(screen.queryByTestId("build-identity")).not.toBeInTheDocument();
  });

  it("renders nothing when the health request fails", async () => {
    mountWith(null);

    const status = await screen.findByTestId("health-status");
    // The query retries once, so the error state is not the first render.
    await waitFor(() => expect(status).toHaveTextContent("unreachable"));
    expect(screen.queryByTestId("build-identity")).not.toBeInTheDocument();
  });

  it("announces the status but not the build identity", async () => {
    mountWith(health({ version: "v0.2.0", commit: "a1b2c3d", is_dev: false }));

    // Scoped through the status word: the app has many role="status" regions and
    // this test is about which one wraps the build identity.
    await screen.findByTestId("build-identity");
    const live = screen.getByTestId("health-status").closest('[role="status"]')!;
    expect(live).toHaveTextContent("System: ok");
    // Re-announcing a build sha on every 60s poll would train people to ignore
    // the region that carries the real alert.
    expect(live).not.toHaveTextContent("a1b2c3d");
    expect(live).not.toContainElement(screen.getByTestId("build-identity"));
  });

  it("keeps the contrast token the design review measured", async () => {
    // A class assertion proves a string, not a ratio. neutral-600 was measured at
    // 7.5:1 on the #fafafa sidebar; neutral-400, the next step down, is 2.5:1 and
    // fails AA. This is a regression guard on that choice, not a contrast proof.
    mountWith(health({ version: "v0.2.0", commit: "a1b2c3d", is_dev: false }));

    const line = await screen.findByTestId("build-identity");
    expect(line.parentElement).toHaveClass("text-neutral-600");
  });

  it("carries the whole identity in the title, matching `kerp --version`", async () => {
    mountWith(health({ version: "v0.2.0", commit: "a1b2c3d", is_dev: false }));

    expect(await screen.findByTestId("build-identity")).toHaveAttribute(
      "title",
      "kitchen-erp v0.2.0 (a1b2c3d)",
    );
  });
});

describe("buildIdentity", () => {
  it("keeps both halves once a tag exists past the sha", () => {
    const result = buildIdentity(health({ version: "v0.2.0-5-gabc1234", commit: "abc1234" }));
    expect(result).toMatchObject({ primary: "abc1234", secondary: "v0.2.0-5-gabc1234" });
  });

  it("reports the sentinels honestly rather than hiding them", () => {
    // What a bare `docker compose up --build` produces: compose has no command
    // substitution, so neither arg is set and the image says so.
    const result = buildIdentity(health({ version: "dev", commit: "unknown", is_dev: true }));
    expect(result).toMatchObject({ primary: "unknown", secondary: "dev", isDev: true });
  });

  it("returns null when either field is missing", () => {
    expect(buildIdentity(health({ version: "v0.2.0" }))).toBeNull();
    expect(buildIdentity(health({ commit: "a1b2c3d" }))).toBeNull();
  });
});
