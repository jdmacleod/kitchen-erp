import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

function mountMap(path: string) {
  mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [] }),
    "GET /home-bases": () => jsonResponse(200, { items: [] }),
    "GET /price-book/cheapest": () => jsonResponse(200, { unit: null, items: [] }),
  });
  renderApp(path);
}

describe("map placing deep-link", () => {
  it("arrives ready to place when sent from a blocked entry form", async () => {
    // MapView ignores map clicks unless it is placing, so a link to a bare /map
    // would land someone on a screen that swallows their first click.
    mountMap("/map?place=location");

    expect(await screen.findByRole("button", { name: "Add location here" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("opens in browse mode without the parameter", async () => {
    mountMap("/map");

    expect(await screen.findByRole("button", { name: "Add location here" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
  });

  it("does not name a button the visitor never had to press", async () => {
    // Caught by looking at the rendered page: arriving deep-linked, placing mode
    // is already on and the status line already says where to click, so telling
    // them to "use Add location here" describes a step that did not happen.
    mountMap("/map?place=location");

    expect(await screen.findByText(/Naming the pin creates the vendor/)).toBeInTheDocument();
    expect(screen.queryByText(/Use .Add location here., then click the map/)).not.toBeInTheDocument();
  });

  it("leads with what to do when the household has no locations yet", async () => {
    mountMap("/map");

    // Not "click anywhere to drop a pin": that instruction is false in browse mode.
    expect(await screen.findByText(/Use .Add location here., then click the map/)).toBeInTheDocument();
  });
});
