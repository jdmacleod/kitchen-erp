// Weekly flyers 2U-4 (spec 18; UI-9.5): a vendor location's Circular card, with its
// switches, last reads and "Check now". Every store and id here is invented.
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Circular, CircularSource } from "../api/circulars";
import { chainLocation, chainVendor, chainVendorId, homeBase } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

vi.mock("maplibre-gl", () => import("./maplibre-stub"));

const circularPath = `/vendor-locations/${chainLocation.id}/circular`;

const source: CircularSource = {
  mechanism: "hosted_circular",
  adapter: "example_hosted",
  store_ref: { merchant: "larkspur", store: "0042" },
  publish_weekday: 3,
  enabled: true,
  require_confirmation: false,
  failures: 0,
  paused_until: null,
};

const read: Circular = {
  source,
  runs: [
    {
      id: "01000000-0000-7000-8000-00000000c002",
      status: "failed",
      reason: "The site answered 503.",
      ad_week_from: null,
      ad_week_to: null,
      fetched_at: "2026-11-11T12:00:00Z",
      offered_count: 0,
      relevant_count: 0,
    },
    {
      id: "01000000-0000-7000-8000-00000000c001",
      status: "ok",
      reason: null,
      ad_week_from: "2026-11-04",
      ad_week_to: "2026-11-10",
      fetched_at: "2026-11-04T12:00:00Z",
      offered_count: 312,
      relevant_count: 23,
    },
  ],
  checking: false,
};

function routes(circular: () => Circular, extra: Parameters<typeof mockApi>[0] = {}) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
    [`GET /vendors/${chainVendorId}`]: () => jsonResponse(200, chainVendor),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation] }),
    [`GET ${circularPath}`]: () => jsonResponse(200, circular()),
    ...extra,
  };
}

async function card() {
  return screen.findByRole("region", { name: `Circular for ${chainLocation.name}` });
}

describe("a location's circular", () => {
  it("shows how it's read and its last reads, with their counts or why not", async () => {
    mockApi(routes(() => read));
    renderApp(`/catalog/vendors/${chainVendorId}`);

    const section = await card();
    expect(
      await within(section).findByText(
        "Hosted circular · example_hosted · merchant larkspur, store 0042 · new each Wednesday",
      ),
    ).toBeInTheDocument();
    const reads = within(section).getByRole("list", { name: "Last reads" });
    expect(
      within(reads)
        .getAllByRole("listitem")
        .map((li) => li.textContent),
    ).toEqual([
      "Nov 11, 2026: couldn't be read: The site answered 503",
      "Week of Nov 4, 2026: 312 offers · 23 relevant",
    ]);
    expect(within(section).getByRole("switch", { name: "Enabled" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("turns asking first on, and checks now", async () => {
    let circular: Circular = read;
    const calls = mockApi(
      routes(() => circular, {
        [`PATCH ${circularPath}`]: (call) => {
          circular = {
            ...circular,
            source: { ...source, ...(call.body as object) },
          };
          return jsonResponse(200, circular);
        },
        [`POST ${circularPath}/check`]: () => {
          circular = { ...circular, checking: true };
          return jsonResponse(200, circular);
        },
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/vendors/${chainVendorId}`);

    const section = await card();
    await user.click(await within(section).findByRole("switch", { name: "Ask before using" }));
    await waitFor(() =>
      expect(within(section).getByRole("switch", { name: "Ask before using" })).toHaveAttribute(
        "aria-checked",
        "true",
      ),
    );
    expect(calls.find((c) => c.method === "PATCH")?.body).toEqual({ require_confirmation: true });

    await user.click(within(section).getByRole("button", { name: "Check now" }));
    expect(
      await within(section).findByRole("button", { name: "Waiting for the helper…" }),
    ).toBeDisabled();
  });

  it("is set up from the card, with its store ids one per line", async () => {
    let circular: Circular = { source: null, runs: [], checking: false };
    const calls = mockApi(
      routes(() => circular, {
        [`PUT ${circularPath}`]: (call) => {
          circular = {
            source: {
              ...(call.body as Circular["source"] & object),
              require_confirmation: false,
              failures: 0,
              paused_until: null,
            },
            runs: [],
            checking: false,
          };
          return jsonResponse(200, circular);
        },
      }),
    );
    const user = userEvent.setup();
    renderApp(`/catalog/vendors/${chainVendorId}`);

    const section = await card();
    expect(
      await within(section).findByText("No weekly circular is read for this location."),
    ).toBeInTheDocument();
    await user.click(within(section).getByRole("button", { name: "Set up circular" }));
    await user.type(within(section).getByLabelText("Adapter"), "example_hosted");
    await user.type(within(section).getByLabelText("Store ids"), "store 0042");
    await user.click(within(section).getByRole("button", { name: "Set up circular" }));
    expect(within(section).getByRole("alert")).toHaveTextContent(
      'Write each store id as "name: value", one per line.',
    );

    await user.clear(within(section).getByLabelText("Store ids"));
    await user.type(within(section).getByLabelText("Store ids"), "store: 0042");
    await user.selectOptions(within(section).getByLabelText("New each"), "Thursday");
    await user.click(within(section).getByRole("button", { name: "Set up circular" }));
    await waitFor(() =>
      expect(calls.find((c) => c.method === "PUT")?.body).toEqual({
        mechanism: "hosted_circular",
        adapter: "example_hosted",
        store_ref: { store: "0042" },
        publish_weekday: 4,
        enabled: false,
      }),
    );
    expect(await within(section).findByRole("switch", { name: "Enabled" })).toHaveAttribute(
      "aria-checked",
      "false",
    );
  });
});
