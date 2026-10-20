// A page clipped for waiting receipt lines (04, 2D). Every store, item and address is invented.
import { act, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { ToIdentifyGroup } from "../api/toIdentify";
import { identifiedNote } from "../components/catalog/ProposalStatusNotice";
import { clearClipTarget, readClipTarget, setClipTarget } from "../lib/clipFor";
import { units } from "./catalog-fixtures";
import { chainVendor, chainVendorId } from "./geo-fixtures";
import { adminUser, jsonResponse, mockApi, renderApp } from "./helpers";

const STORE = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c10";
const OTHER = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c11";
const PAGE = "https://shop.example.test/p/rolled-oats-500g-77123";
const clip = {
  page_url: PAGE,
  canonical_url: PAGE,
  title: "Rolled Oats 500g",
  meta: {},
  structured_data: [],
  dom_text: "Rolled Oats 500g",
  image_urls: [],
  images: [],
};
const target = {
  vendor_id: STORE,
  vendor_name: "Juniper Market",
  raw_text_norm: "HC ROLLED OATS",
  line_count: 2,
};

let opener: { postMessage: ReturnType<typeof vi.fn> };
function setOpener(value: unknown) {
  Object.defineProperty(window, "opener", { value, configurable: true, writable: true });
}
function deliver(data: unknown, source: unknown) {
  const event = new MessageEvent("message", { data });
  Object.defineProperty(event, "source", { value: source });
  act(() => {
    window.dispatchEvent(event);
  });
}

function clipRoutes(storeId: string, known?: { product_id: string; name: string }) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "POST /product-captures/address": () =>
      jsonResponse(200, {
        vendor: { id: storeId, name: "Some Market" },
        canonical_url: PAGE,
        title: "Rolled oats 500g",
        item_number: "77123",
        known: known ? { ...known, reason: "listing" } : null,
      }),
    "POST /product-captures": () =>
      jsonResponse(201, { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c01" }),
    "POST /to-identify/apply": () => jsonResponse(200, { applied: 2 }),
  };
}

async function openClip(user: ReturnType<typeof userEvent.setup>) {
  renderApp("/capture/clip");
  await waitFor(() => expect(opener.postMessage).toHaveBeenCalled());
  deliver({ type: "kerp-clip", payload: clip }, opener);
  await screen.findByText("Rolled Oats 500g");
  return user;
}

beforeEach(() => {
  opener = { postMessage: vi.fn() };
  setOpener(opener);
  clearClipTarget();
});
afterEach(() => {
  setOpener(null);
  clearClipTarget();
});

describe("the clip target", () => {
  it("is read back while fresh, and not once stale or malformed", () => {
    setClipTarget(target);
    expect(readClipTarget()).toMatchObject(target);
    expect(readClipTarget(Date.now() + 3 * 60 * 60 * 1000)).toBeNull();
    localStorage.setItem("kerp.clipFor", '{"vendor_id": 3}');
    expect(readClipTarget()).toBeNull();
    localStorage.setItem("kerp.clipFor", "not json");
    expect(readClipTarget()).toBeNull();
  });
});

describe("a clip for marked lines", () => {
  it("is for them by default on their store's page, and says so in the capture", async () => {
    setClipTarget(target);
    const calls = mockApi(clipRoutes(STORE));
    const user = await openClip(userEvent.setup());
    const box = await screen.findByRole("checkbox", { name: /Use for HC ROLLED OATS/ });
    await waitFor(() => expect(box).toBeChecked());
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(/Saved ·/);
    const posted = calls.find((c) => c.path === "/product-captures");
    expect(posted?.body).toMatchObject({
      for_lines: { vendor_id: STORE, raw_text_norm: "HC ROLLED OATS" },
    });
    expect(readClipTarget()).toBeNull();
  });

  it("is not for them on another store's page unless ticked", async () => {
    setClipTarget(target);
    const calls = mockApi(clipRoutes(OTHER));
    const user = await openClip(userEvent.setup());
    await screen.findByText("From Some Market");
    expect(screen.getByRole("checkbox", { name: /Use for HC ROLLED OATS/ })).not.toBeChecked();
    await user.click(screen.getByRole("button", { name: "Save" }));
    await screen.findByText(/Saved ·/);
    const posted = calls.find((c) => c.path === "/product-captures");
    expect((posted?.body as { for_lines?: unknown }).for_lines).toBeUndefined();
    expect(readClipTarget()).not.toBeNull();
  });

  it("identifies the lines as a product the page already is, without saving", async () => {
    setClipTarget(target);
    const productId = "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c20";
    const calls = mockApi(clipRoutes(STORE, { product_id: productId, name: "Rolled oats" }));
    const user = await openClip(userEvent.setup());
    await user.click(
      await screen.findByRole("button", { name: "Use Rolled oats for HC ROLLED OATS" }),
    );
    expect(await screen.findByText("Identified 2 lines as Rolled oats.")).toBeInTheDocument();
    expect(calls.find((c) => c.path === "/to-identify/apply")?.body).toEqual({
      vendor_id: STORE,
      raw_text_norm: "HC ROLLED OATS",
      product_id: productId,
    });
    expect(calls.some((c) => c.path === "/product-captures")).toBe(false);
  });
});

function group(extra: Partial<ToIdentifyGroup> = {}): ToIdentifyGroup {
  return {
    vendor: { id: chainVendorId, name: chainVendor.name },
    raw_text_norm: "HC ROLLED OATS",
    line_count: 2,
    lines: [
      {
        line_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8202",
        purchase_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f8004",
        raw_text: "HC ROLLED OATS",
        purchased_at: "2026-09-20T18:05:00Z",
        line_total: "3.49",
        qty: "1",
        unit: "each",
      },
    ],
    ...extra,
  };
}

function queueRoutes(items: ToIdentifyGroup[]) {
  return {
    "GET /auth/me": () => jsonResponse(200, adminUser),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "GET /units": () => jsonResponse(200, { items: units }),
    "GET /ingredients": () => jsonResponse(200, { items: [], next_cursor: null }),
    "GET /products/search": () => jsonResponse(200, { items: [] }),
    "GET /to-identify": () => jsonResponse(200, { items }),
  };
}

describe("a card's clip", () => {
  it("marks the group the next clipped page is for", async () => {
    setOpener(null);
    mockApi(queueRoutes([group()]));
    const user = userEvent.setup();
    renderApp("/shop/receipts/identify");
    const card = await screen.findByRole("group", { name: `${chainVendor.name}: HC ROLLED OATS` });
    await user.click(
      within(card).getByRole("button", { name: "Clip a product page for these lines" }),
    );
    expect(
      within(card).getByText("The next product page you clip is for these lines."),
    ).toBeInTheDocument();
    expect(readClipTarget()).toMatchObject({
      vendor_id: chainVendorId,
      raw_text_norm: "HC ROLLED OATS",
      line_count: 2,
    });
  });

  it("links the page clipped for it that waits for review", async () => {
    setOpener(null);
    const waiting = { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c01", title: "Rolled Oats 500g" };
    mockApi(queueRoutes([group({ waiting_proposal: waiting })]));
    renderApp("/shop/receipts/identify");
    const card = await screen.findByRole("group", { name: `${chainVendor.name}: HC ROLLED OATS` });
    expect(card).toHaveTextContent("Clipped: Rolled Oats 500g, waiting for review");
    expect(within(card).getByRole("link", { name: "Review it" })).toHaveAttribute(
      "href",
      `/catalog/products/review/${waiting.id}`,
    );
    expect(within(card).queryByRole("button", { name: /Clip a product page/ })).toBeNull();
  });
});

describe("after accepting a page clipped for lines", () => {
  it("says how many receipt lines it identified", () => {
    expect(identifiedNote({ result: { identified_lines: 2 } })).toBe(
      " Identified 2 receipt lines.",
    );
    expect(identifiedNote({ result: { identified_lines: 1 } })).toBe(" Identified 1 receipt line.");
    expect(identifiedNote({ result: { identified_lines: 0 } })).toBe("");
    expect(identifiedNote({ result: null })).toBe("");
  });
});
