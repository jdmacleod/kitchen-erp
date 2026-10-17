import { fireEvent, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { RowBoxes } from "../api/rowBoxes";
import { units } from "./catalog-fixtures";
import { chainLocation, marketLocation } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";
import { receiptPurchaseId } from "./purchase-fixtures";
import { peppers, tortillas, weightRow, youPayPurchase } from "./why-off-fixtures";

// Review outlines the current line's printed row on the receipt photo, scrolls
// the photo to it, and a click on a row picks its line (issue 380).

const base = `/purchases/${receiptPurchaseId}`;

const boxes: RowBoxes = {
  width: 400,
  height: 1000,
  lines: [
    { line_id: tortillas.id, left: 20, top: 100, width: 360, height: 20 },
    { line_id: peppers.id, left: 20, top: 600, width: 360, height: 20 },
  ],
};

function routes(rowBoxes: () => Response) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /vendor-locations": () => jsonResponse(200, { items: [chainLocation, marketLocation] }),
    "GET /ingest-jobs": () => jsonResponse(200, { items: [] }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    [`GET ${base}`]: () => jsonResponse(200, youPayPurchase),
    [`GET ${base}/row-boxes`]: rowBoxes,
  };
}

async function photoLoaded() {
  await screen.findByTestId("review");
  fireEvent.load(screen.getByRole("img", { name: "The receipt as photographed" }));
}

function lineRow(id: string): HTMLElement {
  const row = document.getElementById(`review-line-${id}`);
  if (!row) throw new Error(`no line ${id}`);
  return row;
}
const isCurrent = (el: HTMLElement) =>
  el.getAttribute("aria-selected") === "true" || el.getAttribute("aria-current") === "true";
const outline = (id: string) =>
  screen.getByTestId("receipt-photo").querySelector<HTMLElement>(`[data-line-id="${id}"]`);
const outlined = () =>
  screen.getByTestId("receipt-photo").querySelector<HTMLElement>("[data-current=true]");

afterEach(() => {
  vi.restoreAllMocks();
});

describe("the current line's row on the receipt photo (2S, issue 380)", () => {
  it("outlines the current line's row, in the photo's proportions", async () => {
    mockApi(routes(() => jsonResponse(200, boxes)));
    const user = userEvent.setup();
    renderApp(base);
    await photoLoaded();
    await user.click(lineRow(peppers.id));
    await waitFor(() => expect(outlined()).toHaveAttribute("data-line-id", peppers.id));
    expect(outlined()).toHaveStyle({ left: "5%", top: "60%", width: "90%", height: "2%" });
    expect(outlined()?.closest("[aria-hidden=true]")).not.toBeNull();

    await user.click(lineRow(tortillas.id));
    expect(outlined()).toHaveAttribute("data-line-id", tortillas.id);
  });

  it("draws no outline for a line whose row wasn't found, or before the photo loads", async () => {
    mockApi(routes(() => jsonResponse(200, boxes)));
    const user = userEvent.setup();
    renderApp(base);
    await screen.findByTestId("review");
    await user.click(lineRow(peppers.id));
    expect(outline(peppers.id)).toBeNull();

    fireEvent.load(screen.getByRole("img", { name: "The receipt as photographed" }));
    await waitFor(() => expect(outlined()).not.toBeNull());
    await user.click(lineRow(weightRow.id));
    expect(outlined()).toBeNull();
    expect(outline(weightRow.id)).toBeNull();
  });

  it("scrolls the zoomed photo to the current line's row", async () => {
    mockApi(routes(() => jsonResponse(200, boxes)));
    const user = userEvent.setup();
    renderApp(base);
    await photoLoaded();
    await user.click(screen.getByRole("button", { name: "Zoom in" }));
    fireEvent.load(screen.getByRole("img", { name: "The receipt as photographed" }));
    const frame = screen.getByRole("region", { name: "Receipt image" });
    // The frame shows 0–400px of the page; a row's place is read from its box.
    vi.spyOn(HTMLElement.prototype, "getBoundingClientRect").mockImplementation(function (
      this: HTMLElement,
    ) {
      if (this === frame) return new DOMRect(0, 0, 300, 400);
      const id = this.dataset.lineId;
      const top = id === peppers.id ? 900 - frame.scrollTop : 150 - frame.scrollTop;
      return new DOMRect(10, top, 280, 30);
    });
    await user.click(lineRow(peppers.id));
    await waitFor(() => expect(frame.scrollTop).toBe(900));
    // Already in view: the frame stays where it is.
    await user.click(lineRow(tortillas.id));
    expect(frame.scrollTop).toBe(900 + 150 - 900);
  });

  it("makes a row's line current when the row is clicked, not when the photo is dragged", async () => {
    mockApi(routes(() => jsonResponse(200, boxes)));
    renderApp(base);
    await photoLoaded();
    await waitFor(() => expect(outline(peppers.id)).not.toBeNull());

    const row = outline(peppers.id);
    if (!row) throw new Error("no outline");
    fireEvent.pointerDown(row, { clientX: 50, clientY: 50, pointerId: 1 });
    fireEvent.pointerUp(row, { clientX: 90, clientY: 80, pointerId: 1 });
    expect(isCurrent(lineRow(peppers.id))).toBe(false);

    fireEvent.pointerDown(row, { clientX: 50, clientY: 50, pointerId: 1 });
    fireEvent.pointerUp(row, { clientX: 51, clientY: 52, pointerId: 1 });
    await waitFor(() => expect(isCurrent(lineRow(peppers.id))).toBe(true));
    expect(lineRow(peppers.id)).toHaveFocus();
    expect(outlined()).toHaveAttribute("data-line-id", peppers.id);
  });

  it("shows the photo without outlines when no boxes were found or the request fails", async () => {
    mockApi(routes(() => jsonResponse(200, { width: null, height: null, lines: [] })));
    const { unmount } = renderApp(base);
    await photoLoaded();
    expect(screen.getByTestId("receipt-photo").querySelector("[data-line-id]")).toBeNull();
    unmount();

    mockApi(routes(() => jsonResponse(500, { error: { code: "internal", message: "x" } })));
    renderApp(base);
    await photoLoaded();
    expect(screen.getByRole("img", { name: "The receipt as photographed" })).toBeVisible();
    expect(screen.getByTestId("receipt-photo").querySelector("[data-line-id]")).toBeNull();
  });
});
