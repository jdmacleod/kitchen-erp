// Vendor import drawer (spec 03 §1F, UI-3.14; design D4, D6). Invented vendors only.
import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import type { ImportReport } from "../api/geo";
import { chainVendor, homeBase } from "./geo-fixtures";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

vi.mock("maplibre-gl", () => import("./maplibre-stub"));

const preview: ImportReport = {
  dry_run: true,
  mode: "household",
  counts: { created: 1, updated: 1, unchanged: 2, conflicts: 1, unmatched: 1 },
  items: [
    { target: "vendor", key: "invented-mart", name: "Invented Mart", vendor_key: null, outcome: "unchanged", changes: [], conflicts: [], reason: null },
    { target: "location", key: "invented-mart/elm-st", name: "Elm St", vendor_key: "invented-mart", outcome: "conflict", changes: [], conflicts: [{ field: "phone", current: "555-0142", file: "+1 555 0107" }], reason: null },
    { target: "location", key: "invented-mart/pier", name: "Pier", vendor_key: "invented-mart", outcome: "unmatched", changes: [], conflicts: [], reason: "Matches 2 locations within 150 m; add a key or link it to OpenStreetMap." },
    { target: "location", key: "invented-mart/harbor-rd", name: "Harbor Rd", vendor_key: "invented-mart", outcome: "updated", changes: [{ field: "phone", old: null, new: "+1 555 0100" }], conflicts: [], reason: null },
    { target: "location", key: "invented-mart/quay", name: "Quay", vendor_key: "invented-mart", outcome: "created", changes: [{ field: "name", old: null, new: "Quay" }], conflicts: [], reason: null },
    { target: "vendor", key: "blue-barn", name: "Blue Barn", vendor_key: null, outcome: "unchanged", changes: [], conflicts: [], reason: null },
  ],
  unresolved_home_bases: ["Cliff cottage"],
};

function render(answer: (call: RecordedCall) => Response) {
  const calls = mockApi({
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /home-bases": () => jsonResponse(200, { items: [homeBase] }),
    "GET /vendors": () => jsonResponse(200, { items: [{ ...chainVendor, location_count: 1, last_visit: null }] }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [] }),
    "POST /vendors/import": answer,
  });
  renderApp("/catalog/vendors");
  return calls;
}

async function open(user: ReturnType<typeof userEvent.setup>, name = "vendors.yaml", text = "format: kitchen-erp-vendors/1\n") {
  await user.click(await screen.findByRole("button", { name: "Import" }));
  const drawer = await screen.findByRole("dialog", { name: "Import vendors" });
  await user.upload(within(drawer).getByLabelText("Vendor file"), new File([text], name, { type: "application/yaml" }));
  return drawer;
}

describe("import vendors", () => {
  it("previews with what needs you first, then imports", async () => {
    const calls = render((call) => jsonResponse(200, call.query.get("dry_run") === "true" ? preview : { ...preview, dry_run: false }));
    const user = userEvent.setup();
    const drawer = await open(user);

    expect(await within(drawer).findByText("1 to create · 1 to update · 3 need you · 2 unchanged")).toBeInTheDocument();
    const dry = calls.find((c) => c.method === "POST");
    expect(dry?.query.get("dry_run")).toBe("true");
    expect(dry?.query.get("filename")).toBe("vendors.yaml");
    expect(dry?.headers.get("Content-Type")).toBe("application/yaml");

    const needs = within(drawer).getByRole("region", { name: "Needs you" });
    expect(needs).toHaveTextContent("Your edit is kept: phone (555-0142). The file says +1 555 0107.");
    expect(needs).toHaveTextContent("Matches 2 locations within 150 m");
    expect(needs).toHaveTextContent("No kitchen called Cliff cottage");
    const change = within(drawer).getByRole("region", { name: "Will change" });
    expect(change).toHaveTextContent("Invented Mart · Harbor Rd");
    expect(change).toHaveTextContent("phone: empty → +1 555 0100");
    // Needs you comes before Will change in reading order.
    expect(needs.compareDocumentPosition(change) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(within(drawer).getByText("2 unchanged")).toBeInTheDocument();

    await user.click(within(drawer).getByRole("button", { name: "Import" }));
    await waitFor(() => expect(calls.filter((c) => c.method === "POST").map((c) => c.query.get("dry_run"))).toEqual(["true", "false"]));
    expect(await screen.findByTestId("notice")).toHaveTextContent("Imported: 1 created, 1 updated, 2 left for you.");
    expect(screen.queryByRole("dialog", { name: "Import vendors" })).not.toBeInTheDocument();
  });

  it("says when the import differed from its preview", async () => {
    render((call) =>
      jsonResponse(200, call.query.get("dry_run") === "true" ? preview : { ...preview, dry_run: false, counts: { ...preview.counts, updated: 3 } }),
    );
    const user = userEvent.setup();
    const drawer = await open(user);
    await within(drawer).findByText(/to create/);
    await user.click(within(drawer).getByRole("button", { name: "Import" }));
    expect(await screen.findByTestId("notice")).toHaveTextContent("2 changed since the preview.");
  });

  it("keeps the file chosen and says why when it is refused", async () => {
    render(() => errorResponse(422, "bad_export", "vendors.0.locations.0.lat is a floating-point number; write it as a string."));
    const user = userEvent.setup();
    const drawer = await open(user);
    expect(await within(drawer).findByRole("alert")).toHaveTextContent("floating-point number");
    expect(within(drawer).getByLabelText("Choose another file")).toBeInTheDocument();
    expect(within(drawer).getByRole("button", { name: "Import" })).toBeDisabled();
  });

  it("says when there is nothing to import, and will not import it", async () => {
    const same: ImportReport = { ...preview, counts: { created: 0, updated: 0, unchanged: 2, conflicts: 0, unmatched: 0 }, items: preview.items.filter((i) => i.outcome === "unchanged"), unresolved_home_bases: [] };
    render(() => jsonResponse(200, same));
    const user = userEvent.setup();
    const drawer = await open(user);
    expect(await within(drawer).findByText("This file matches what you have. Nothing to import.")).toBeInTheDocument();
    expect(within(drawer).getByRole("button", { name: "Import" })).toBeDisabled();
  });

  it("asks before discarding a chosen file, and writes nothing on cancel (UI-3.14)", async () => {
    const calls = render(() => jsonResponse(200, preview));
    const user = userEvent.setup();
    const drawer = await open(user);
    await within(drawer).findByText(/to create/);
    await user.click(within(drawer).getByRole("button", { name: "Cancel" }));
    expect(within(drawer).getByRole("alert")).toHaveTextContent("Discard this import?");
    await user.click(within(drawer).getByRole("button", { name: "Discard" }));
    expect(calls.filter((c) => c.method === "POST").map((c) => c.query.get("dry_run"))).toEqual(["true"]);
  });
});
