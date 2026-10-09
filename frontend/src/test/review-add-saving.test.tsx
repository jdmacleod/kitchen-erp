import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { units } from "./catalog-fixtures";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { receiptPurchase, receiptPurchaseId } from "./purchase-fixtures";

// A saving the reader missed is printed negative ("-3.75"). Review's "Add a line"
// refused it with "A line total is required." though a total was typed.

const base = `/purchases/${receiptPurchaseId}`;

function routes() {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
    "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    [`GET ${base}`]: () => jsonResponse(200, receiptPurchase),
    [`POST ${base}/lines`]: () => jsonResponse(201, receiptPurchase),
  };
}

async function newLine(user: ReturnType<typeof userEvent.setup>, kind: string, total: string) {
  await screen.findByTestId("review");
  await user.click(screen.getByText("Add a line"));
  const form = screen.getByRole("group", { name: "New line" });
  await user.selectOptions(within(form).getByLabelText("Kind"), kind);
  await user.type(within(form).getByLabelText("Line total"), total);
  await user.click(within(form).getByRole("button", { name: "Add line" }));
  return form;
}

describe("adding a line in review", () => {
  it("takes a saving typed as printed, negative, on a discount line", async () => {
    const calls = mockApi(routes());
    const user = userEvent.setup();
    renderApp(base);
    const form = await newLine(user, "discount", "-3.75");
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls.find((c) => c.method === "POST")?.body).toMatchObject({ line_kind: "discount", line_total: "-3.75" });
    expect(within(form).queryByRole("alert")).not.toBeInTheDocument();
  });

  it("says why a negative item is refused, rather than asking for a total", async () => {
    const calls = mockApi(routes());
    const user = userEvent.setup();
    renderApp(base);
    const form = await newLine(user, "item", "-3.75");
    expect(await within(form).findByText(/Only a discount can be negative/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST")).toBe(false);
  });
});
