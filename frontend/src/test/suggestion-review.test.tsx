// Reviewing vendor suggestions (spec 03 §1F, UI-3.15; design D3, D8, D9, D13, D15).
// Invented vendors, 555-01xx numbers.
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { Suggestion } from "../api/suggestions";
import { chainLocation, chainVendor, chainVendorId, homeBase } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

vi.mock("maplibre-gl", () => import("./maplibre-stub"));

const base: Omit<Suggestion, "id" | "field" | "expected" | "current" | "proposed" | "stale"> = {
  batch_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9000",
  target: "location",
  vendor_id: chainVendorId,
  vendor_name: chainVendor.name,
  location_id: chainLocation.id,
  location_name: "Elm St",
  status: "pending",
  source_url: "https://www.millstone.example/stores/elm",
  source_domain: "millstone.example",
  evidence: null,
  tool: "enrich-tool",
  tool_version: "0.3",
  created_at: "2026-09-29T12:00:00Z",
};
const phone: Suggestion = { ...base, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9001", field: "phone", expected: null, current: null, proposed: "+1 555 0100", stale: false, evidence: "<b>Listed</b> on the store page" };
const hours: Suggestion = { ...base, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9002", field: "opening_hours", expected: null, current: null, proposed: "Mo-Su 08:00-21:00", stale: false };
const stale: Suggestion = { ...base, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9003", location_name: "Harbor Rd", field: "phone", expected: null, current: "555-0142", proposed: "+1 555 0107", stale: true };
const website: Suggestion = { ...base, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9004", target: "vendor", location_id: null, location_name: null, field: "website", expected: null, current: null, proposed: "https://millstone.example", stale: false };

function render(initial: Suggestion[], path = "/catalog/vendors") {
  let rows = [...initial];
  const decide = (call: RecordedCall, action: string) => {
    const id = call.path.split("/")[2];
    const s = rows.find((r) => r.id === id)!;
    rows = rows.filter((r) => r.id !== id);
    return jsonResponse(200, { outcome: action === "accept" ? "accepted" : "rejected", suggestion: s });
  };
  const summary = () => ({
    count: rows.length,
    tools: rows.length ? ["enrich-tool"] : [],
    vendors: rows.length ? [{ vendor_id: chainVendorId, name: chainVendor.name, count: rows.length }] : [],
  });
  const calls = mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
    "GET /vendors": () => jsonResponse(200, { items: [{ ...chainVendor, location_count: 2, last_visit: null }] }),
    "GET /inbox": () => jsonResponse(200, { items: [], reading: { count: 0, oldest_at: null, stalled: false } }),
    "GET /vendor-suggestions/summary": () => jsonResponse(200, summary()),
    "GET /vendor-suggestions": () => jsonResponse(200, { items: rows }),
    ...Object.fromEntries(
      initial.flatMap((s) => [
        [`POST /vendor-suggestions/${s.id}/accept`, (c: RecordedCall) => decide(c, "accept")],
        [`POST /vendor-suggestions/${s.id}/reject`, (c: RecordedCall) => decide(c, "reject")],
      ]),
    ),
    [`POST /vendor-suggestions/vendors/${chainVendorId}/accept-all`]: () => {
      const accepted = rows.filter((r) => !r.stale).length;
      rows = rows.filter((r) => r.stale);
      return jsonResponse(200, { accepted, skipped_stale: rows.length });
    },
  });
  renderApp(path);
  return calls;
}

describe("vendor suggestion review", () => {
  it("shows readable values, a named source and plain-text evidence, one vendor at a time", async () => {
    render([website, phone, hours]);
    const user = userEvent.setup();
    expect(await screen.findByText("3 suggestions from enrich-tool to review")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Review" }));
    const drawer = await screen.findByRole("dialog", { name: "Vendor suggestions" });
    expect(within(drawer).getByLabelText("Vendor")).toHaveDisplayValue(`${chainVendor.name} · 3`);
    expect(await within(drawer).findByText("From enrich-tool 0.3. Nothing changes until you accept it.")).toBeInTheDocument();
    const elm = within(drawer).getByRole("region", { name: "Elm St" });
    expect(within(elm).getByText("every day 08:00–21:00")).toBeInTheDocument();
    const source = within(elm).getAllByRole("link", { name: "millstone.example ↗" })[0];
    expect(source).toHaveAttribute("target", "_blank");
    expect(source).toHaveAttribute("rel", "noopener noreferrer");
    await user.click(within(elm).getByText("Why"));
    expect(within(elm).getByText("<b>Listed</b> on the store page")).toBeInTheDocument();
    // The vendor's own facts come first, under its name.
    expect(within(drawer).getByRole("region", { name: chainVendor.name })).toHaveTextContent("Website");
  });

  it("accepts a row, moves focus to the next, and sums the session on close", async () => {
    const calls = render([phone, hours], "/catalog/vendors?suggestions=1");
    const user = userEvent.setup();
    const drawer = await screen.findByRole("dialog", { name: "Vendor suggestions" });
    await user.click(await within(drawer).findByRole("button", { name: "Accept phone for Elm St" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ override: false }));
    const next = await within(drawer).findByRole("button", { name: "Accept opening hours for Elm St" });
    await waitFor(() => expect(next).toHaveFocus());
    expect(within(drawer).getByText("Accepted phone for Elm St.")).toBeInTheDocument();
    await user.click(within(drawer).getByRole("button", { name: "Reject opening hours for Elm St" }));
    expect(await within(drawer).findByText("Nothing waits for review.")).toBeInTheDocument();
    await user.click(within(drawer).getByRole("button", { name: "Close" }));
    expect(await screen.findByTestId("notice")).toHaveTextContent("1 accepted, 1 rejected.");
  });

  it("shows a changed field's three values and makes keeping yours the main button", async () => {
    const calls = render([stale, phone], "/catalog/vendors?suggestions=1");
    const user = userEvent.setup();
    const drawer = await screen.findByRole("dialog", { name: "Vendor suggestions" });
    const harbor = await within(drawer).findByRole("region", { name: "Harbor Rd" });
    expect(harbor).toHaveTextContent("Changed since proposed");
    expect(harbor).toHaveTextContent("Was empty");
    expect(harbor).toHaveTextContent("Now 555-0142");
    expect(harbor).toHaveTextContent("Proposed +1 555 0107");
    expect(within(harbor).queryByRole("button", { name: /^Accept/ })).not.toBeInTheDocument();
    // Accept all leaves the changed row for a person.
    expect(within(drawer).queryByRole("button", { name: /Accept all/ })).not.toBeInTheDocument();

    await user.click(within(harbor).getByRole("button", { name: "Replace my phone for Harbor Rd with the proposed one" }));
    await waitFor(() => expect(calls.find((c) => c.method === "POST")?.body).toEqual({ override: true }));
  });

  it("keeps mine by rejecting, counted as kept", async () => {
    const calls = render([stale], "/catalog/vendors?suggestions=1");
    const user = userEvent.setup();
    const drawer = await screen.findByRole("dialog", { name: "Vendor suggestions" });
    await user.click(await within(drawer).findByRole("button", { name: "Keep my phone for Harbor Rd" }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/reject"))).toBe(true));
    await within(drawer).findByText("Nothing waits for review.");
    await user.click(within(drawer).getByRole("button", { name: "Close" }));
    expect(await screen.findByTestId("notice")).toHaveTextContent("1 kept yours.");
  });

  it("accepts all of a vendor's unchanged rows at once", async () => {
    const calls = render([website, phone, stale], "/catalog/vendors?suggestions=1");
    const user = userEvent.setup();
    const drawer = await screen.findByRole("dialog", { name: "Vendor suggestions" });
    expect(await within(drawer).findByText("Changed rows are left for you.")).toBeInTheDocument();
    await user.click(within(drawer).getByRole("button", { name: `Accept all for ${chainVendor.name}` }));
    await waitFor(() => expect(calls.some((c) => c.path.endsWith("/accept-all"))).toBe(true));
    await waitFor(() => expect(within(drawer).queryByRole("region", { name: "Elm St" })).not.toBeInTheDocument());
    expect(within(drawer).getByRole("region", { name: "Harbor Rd" })).toBeInTheDocument();
  });

  it("opens the same drawer from a vendor's page, on that vendor", async () => {
    const calls = mockApi({
      "GET /auth/me": () => jsonResponse(200, adminUser),
      "GET /health": () => jsonResponse(200, { status: "ok" }),
      "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
      [`GET /vendors/${chainVendorId}`]: () => jsonResponse(200, chainVendor),
      "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation] }),
      "GET /vendor-suggestions/summary": () =>
        jsonResponse(200, { count: 2, tools: ["enrich-tool"], vendors: [{ vendor_id: chainVendorId, name: chainVendor.name, count: 2 }] }),
      "GET /vendor-suggestions": () => jsonResponse(200, { items: [phone, hours] }),
    });
    const user = userEvent.setup();
    renderApp(`/catalog/vendors/${chainVendorId}`);
    expect(await screen.findByText("2 suggestions to review")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Review" }));
    await screen.findByRole("button", { name: "Accept phone for Elm St" });
    expect(calls.find((c) => c.path.startsWith("/vendor-suggestions?"))?.query.get("vendor_id")).toBe(chainVendorId);
  });
});
