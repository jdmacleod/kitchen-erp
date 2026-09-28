import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import type { Observation } from "../api/purchases";
import { flourProduct, flourProductId, units } from "./catalog-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";
import { observationOk, purchaseId } from "./purchase-fixtures";

// #73: a shelf price can be voided from the product page; a price that came
// from a purchase is sent to that purchase to be corrected.
const shelf: Observation = { ...observationOk, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a01", price: "4.99", purchase_id: null };
const fromPurchase: Observation = {
  ...observationOk,
  id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a02",
  price: "5.25",
  source: "manual",
  purchase_line_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8a02",
  purchase_id: purchaseId,
};
const alreadyVoided: Observation = { ...shelf, id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f7a03", price: "0.49", voided: true, void_reason: "typed 0.49 for 4.99" };

function routes(state: { voided: Set<string> }) {
  const all = () => [shelf, fromPurchase, alreadyVoided].map((o) => (state.voided.has(o.id) ? { ...o, voided: true, void_reason: "wrong shelf" } : o));
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    [`GET /products/${flourProductId}`]: () => jsonResponse(200, flourProduct),
    [`GET /products/${flourProductId}/prices`]: () => jsonResponse(200, { points: [], latest: [] }),
    "GET /price-observations": (call: RecordedCall) =>
      jsonResponse(200, { items: call.query.get("include_voided") === "true" ? all() : all().filter((o) => !o.voided), next_cursor: null }),
    [`POST /price-observations/${shelf.id}/void`]: () => {
      state.voided.add(shelf.id);
      return jsonResponse(200, { ...shelf, voided: true, void_reason: "wrong shelf" });
    },
  };
}

describe("price records on the product page", () => {
  it("voids a shelf price with a reason and links a purchase price to its purchase", async () => {
    const calls = mockApi(routes({ voided: new Set() }));
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);

    const list = await screen.findByRole("list", { name: "Price records" });
    await waitFor(() => expect(within(list).getAllByTestId("price-record")).toHaveLength(2));
    expect(within(list).queryByText(/\$0\.49/)).not.toBeInTheDocument();
    expect(within(list).getByRole("link", { name: "Correct in its purchase" })).toHaveAttribute("href", `/shop/purchases/${purchaseId}`);
    // Only the shelf price offers Void; the purchase price is corrected in its purchase.
    const voidButtons = within(list).getAllByRole("button", { name: /^Void the/ });
    expect(voidButtons).toHaveLength(1);

    await user.click(voidButtons[0]);
    const form = screen.getByRole("form", { name: /Void the \$4\.99 price/ });
    const submit = within(form).getByRole("button", { name: "Void price" });
    expect(submit).toBeDisabled();
    await user.type(within(form).getByLabelText("Why is it wrong?"), "wrong shelf");
    await user.click(submit);

    await waitFor(() => expect(calls.find((c) => c.method === "POST" && c.path.endsWith("/void"))?.body).toEqual({ reason: "wrong shelf" }));
    await waitFor(() => expect(within(list).getAllByTestId("price-record")).toHaveLength(1));
  });

  it("shows voided prices, with their reason, only when asked", async () => {
    mockApi(routes({ voided: new Set() }));
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);

    const list = await screen.findByRole("list", { name: "Price records" });
    expect(within(list).queryByText(/typed 0\.49 for 4\.99/)).not.toBeInTheDocument();
    await user.click(screen.getByLabelText("Show voided"));
    expect(await screen.findByText("Voided: typed 0.49 for 4.99")).toBeInTheDocument();
    const voided = screen.getAllByTestId("price-record").find((r) => r.textContent?.includes("Voided"))!;
    expect(within(voided).queryByRole("button")).not.toBeInTheDocument();
  });

  it("returns focus to Void when the void form is cancelled", async () => {
    // Review of #79: Cancel removed the form and left keyboard focus nowhere.
    mockApi(routes({ voided: new Set() }));
    const user = userEvent.setup();
    renderApp(`/catalog/products/${flourProductId}`);

    const list = await screen.findByRole("list", { name: "Price records" });
    await user.click(await within(list).findByRole("button", { name: /^Void the \$4\.99/ }));
    expect(screen.getByLabelText("Why is it wrong?")).toHaveFocus();
    await user.click(screen.getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(within(list).getByRole("button", { name: /^Void the \$4\.99/ })).toHaveFocus());
  });
});
