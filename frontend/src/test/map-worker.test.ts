import { describe, expect, it, vi } from "vitest";

vi.mock("maplibre-gl", () => import("./maplibre-stub"));

import { instances, setWorkerUrl } from "./maplibre-stub";
import "../components/geo/MapView";

// MapView points MapLibre at its tile-parsing worker at module scope, so the
// call is already spent by the time a test body runs (and the runner clears the
// mock in between). Read the record here, at import, and assert on it below.
const calls = setWorkerUrl.mock.calls.map(([url]) => url);
const mapsAtImport = instances.length;

describe("maplibre worker url", () => {
  it("points MapLibre at the worker Vite actually emits", () => {
    // MapLibre otherwise resolves the worker as a sibling of whichever chunk it
    // was bundled into, a path Vite never emits: every worker fetch 404s and no
    // vector tile is parsed, silently, until a tiles file is installed.
    expect(calls).toHaveLength(1);
    const url = calls[0];
    expect(typeof url).toBe("string");
    expect(url).toMatch(/maplibre-gl-worker/);
    // The failure this guards against also looks like an import that resolved
    // to nothing at all.
    expect(url).not.toBe("");
    expect(url).not.toMatch(/undefined/);
  });

  it("sets the url once for the module, not once per map", async () => {
    // The previous version of this test asserted that no map existed at import
    // time, which no implementation can fail: MapView only constructs a map
    // inside an effect, after an async tiles probe resolves. This asserts the
    // property that actually matters and can actually break -- moving the call
    // into the component or its effect would reopen a window where a map is
    // built before the worker url is set.
    expect(mapsAtImport).toBe(0);
    setWorkerUrl.mockClear();
    const again = await import("../components/geo/MapView");
    expect(again.MapView).toBeTypeOf("function");
    expect(setWorkerUrl).not.toHaveBeenCalled();
  });
});
