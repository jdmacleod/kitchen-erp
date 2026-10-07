import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CLIP_VERSION, bookmarkletCode, bookmarkletHref } from "../lib/bookmarklet";
import { readClip } from "../pages/capture/ClipPage";
import { adminUser, errorResponse, jsonResponse, mockApi, renderApp, type RecordedCall } from "./helpers";

// 2M, slice M2: the clip window (10; PD9, PD21; criterion 85 in the browser tests),
// Settings → Capture (PD19) and Add product's web address (PD20).

const PAGE = "https://shop.example.test/p/rolled-oats-500g-77123";
const clip = {
  page_url: PAGE,
  canonical_url: "https://shop.example.test/p/rolled-oats-500g-77123",
  title: "Rolled Oats 500g – Juniper Market",
  meta: { "og:title": "Rolled Oats" },
  structured_data: ['{"@type":"Product","name":"Rolled Oats 500g"}'],
  dom_text: "Rolled Oats 500g",
  image_urls: ["https://shop.example.test/img/oats.jpg"],
  images: [],
};
const proposal = { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c01" };

let opener: { postMessage: ReturnType<typeof vi.fn> };

function setOpener(value: unknown) {
  Object.defineProperty(window, "opener", { value, configurable: true, writable: true });
}

/** A message as the browser delivers it, from `source`. */
function deliver(data: unknown, source: unknown) {
  const event = new MessageEvent("message", { data });
  Object.defineProperty(event, "source", { value: source });
  act(() => {
    window.dispatchEvent(event);
  });
}

function routes(extra: Record<string, (c: RecordedCall) => Response> = {}, signedIn = true) {
  return {
    "GET /auth/me": () => (signedIn ? jsonResponse(200, adminUser) : errorResponse(401, "unauthenticated", "Sign in.")),
    "GET /health": () => jsonResponse(200, { status: "ok" }),
    "POST /product-captures/address": () =>
      jsonResponse(200, { vendor: { id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c10", name: "Juniper Market" }, canonical_url: PAGE, title: "Rolled oats 500g", item_number: "77123" }),
    "POST /product-captures": () => jsonResponse(201, proposal),
    ...extra,
  };
}

beforeEach(() => {
  opener = { postMessage: vi.fn() };
  setOpener(opener);
});
afterEach(() => setOpener(null));

describe("the clip window", () => {
  it("says it is ready, shows what will be saved, and saves only on a click", async () => {
    const calls = mockApi(routes());
    const user = userEvent.setup();
    renderApp("/capture/clip");
    expect(await screen.findByText("Waiting for the page…")).toBeInTheDocument();
    await waitFor(() => expect(opener.postMessage).toHaveBeenCalledWith({ type: "kerp-clip-ready" }, "*"));

    deliver({ type: "kerp-clip", payload: clip }, opener);
    expect(await screen.findByText("Rolled Oats 500g – Juniper Market")).toBeInTheDocument();
    expect(screen.getByText(PAGE)).toBeInTheDocument();
    expect(await screen.findByText("From Juniper Market")).toBeInTheDocument();
    expect(screen.getByText(/1 image came along/)).toBeInTheDocument();
    expect(calls.some((c) => c.method === "POST" && c.path === "/product-captures")).toBe(false);

    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Saved ·/)).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Review it in Needs you" })).toHaveAttribute("href", `/catalog/products/review/${proposal.id}`);
    const posted = calls.filter((c) => c.path === "/product-captures");
    expect(posted).toHaveLength(1);
    expect(posted[0].headers.get("Idempotency-Key")).toBeTruthy();
    expect(posted[0].body).toMatchObject({ page_url: PAGE, channel: "clip", vendor_id: "0192a1b2-3c4d-7e5f-8a6b-1c2d3e4f9c10" });
  });

  it("ignores a message from any window but its opener", async () => {
    mockApi(routes());
    renderApp("/capture/clip");
    await screen.findByText("Waiting for the page…");
    deliver({ type: "kerp-clip", payload: clip }, { postMessage: vi.fn() });
    deliver({ type: "kerp-clip", payload: { ...clip, page_url: "javascript:alert(1)" } }, opener);
    expect(screen.getByText("Waiting for the page…")).toBeInTheDocument();
  });

  it("says it isn't connected to a store page when the opener was cut off", async () => {
    setOpener(null);
    mockApi(routes());
    renderApp("/capture/clip");
    expect(await screen.findByText(/This window isn't connected to a store page\. Open the product's page and click Save to Kitchen ERP there/)).toBeInTheDocument();
    expect(screen.queryByText(/blocks clipping/)).not.toBeInTheDocument();
  });

  it("says the store page didn't answer when nothing arrives", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      mockApi(routes());
      renderApp("/capture/clip");
      await screen.findByText("Waiting for the page…");
      act(() => {
        vi.advanceTimersByTime(10_000);
      });
      expect(await screen.findByText(/The store page didn't answer\. Reload it and click Save to Kitchen ERP again/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("explains a failed save in the clip's own words, never as a photo upload", async () => {
    mockApi(routes({ "POST /product-captures": () => errorResponse(415, "unsupported_image", "Photos must be JPEG, PNG, WebP or HEIC.") }));
    const user = userEvent.setup();
    renderApp("/capture/clip");
    await screen.findByText("Waiting for the page…");
    deliver({ type: "kerp-clip", payload: clip }, opener);
    await screen.findByText("From Juniper Market");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText(/Couldn't save this page: one of its images isn't a photo Kitchen ERP can use\. Try again, or paste the address in Add product\./)).toBeInTheDocument();
    expect(screen.queryByText(/Photos must be/)).not.toBeInTheDocument();
  });

  it("says when the server failed", async () => {
    mockApi(routes({ "POST /product-captures": () => errorResponse(500, "internal_error", "Internal server error.") }));
    const user = userEvent.setup();
    renderApp("/capture/clip");
    await screen.findByText("Waiting for the page…");
    deliver({ type: "kerp-clip", payload: clip }, opener);
    await screen.findByText("From Juniper Market");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("Couldn't save this page: something went wrong in Kitchen ERP. Try again, or paste the address in Add product.")).toBeInTheDocument();
  });

  it("signs in inside the window and then repeats the handshake", async () => {
    let signedIn = false;
    mockApi({
      ...routes({}, false),
      "GET /auth/me": () => (signedIn ? jsonResponse(200, adminUser) : errorResponse(401, "unauthenticated", "Sign in.")),
      "POST /auth/login": () => {
        signedIn = true;
        return jsonResponse(200, { user: adminUser });
      },
    });
    const user = userEvent.setup();
    renderApp("/capture/clip");
    await user.type(await screen.findByLabelText("Email"), "admin@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
    expect(opener.postMessage).not.toHaveBeenCalled();
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(opener.postMessage).toHaveBeenCalledWith({ type: "kerp-clip-ready" }, "*"));
  });

  it("says when a page is too large or was already saved", async () => {
    mockApi(routes({ "POST /product-captures": () => errorResponse(413, "payload_too_large", "This page is too large to save.", { field: "dom_text" }) }));
    const user = userEvent.setup();
    renderApp("/capture/clip");
    await screen.findByText("Waiting for the page…");
    deliver({ type: "kerp-clip", payload: clip }, opener);
    await screen.findByText("From Juniper Market");
    await user.click(screen.getByRole("button", { name: "Save" }));
    expect(await screen.findByText("This page is too large to save.")).toBeInTheDocument();
  });
});

describe("an older bookmark (clip quality, CQ2)", () => {
  it("asks for a reinstall, and never sends the version to the server", async () => {
    const calls = mockApi(routes());
    const user = userEvent.setup();
    renderApp("/capture/clip");
    await screen.findByText("Waiting for the page…");
    deliver({ type: "kerp-clip", payload: clip }, opener);
    expect(await screen.findByText(/This bookmark is an older version\. Reinstall it from Settings → Capture/)).toBeInTheDocument();
    await screen.findByText("From Juniper Market");
    await user.click(screen.getByRole("button", { name: "Save" }));
    await waitFor(() => expect(calls.some((c) => c.method === "POST" && c.path === "/product-captures")).toBe(true));
    const body = calls.find((c) => c.method === "POST" && c.path === "/product-captures")?.body as Record<string, unknown>;
    expect("clip_version" in body).toBe(false);
  });

  it("says nothing for a current one", async () => {
    mockApi(routes());
    renderApp("/capture/clip");
    await screen.findByText("Waiting for the page…");
    deliver({ type: "kerp-clip", payload: { ...clip, clip_version: CLIP_VERSION } }, opener);
    await screen.findByText("From Juniper Market");
    expect(screen.queryByText(/older version/)).not.toBeInTheDocument();
  });
});

describe("what the clip window accepts", () => {
  it("keeps only what the bookmarklet sends, checked", () => {
    expect(readClip({ type: "kerp-clip", payload: clip })?.page_url).toBe(PAGE);
    expect(readClip({ type: "other", payload: clip })).toBeNull();
    expect(readClip({ type: "kerp-clip", payload: { ...clip, page_url: "file:///etc/passwd" } })).toBeNull();
    const odd = readClip({ type: "kerp-clip", payload: { ...clip, meta: { a: 1, b: "ok" }, image_urls: ["javascript:x", "https://ok.test/i.jpg"] } });
    expect(odd?.meta).toEqual({ b: "ok" });
    expect(odd?.image_urls).toEqual(["https://ok.test/i.jpg"]);
  });
});

describe("the bookmarklet and Settings → Capture", () => {
  it("is tied to the app's address and reads no cookies, storage or forms", () => {
    const code = bookmarkletCode("https://kitchen.example.test");
    expect(code).toContain('const APP = "https://kitchen.example.test"');
    expect(code).toContain('e.data.type !== "kerp-clip-ready"');
    expect(code).not.toMatch(/document\.cookie|localStorage|sessionStorage|\.value\b/);
    // The link is exactly the code, encoded: nothing added, nothing dropped.
    const origin = "https://kitchen.example.test";
    expect(bookmarkletHref(origin)).toBe(`javascript:${encodeURIComponent(bookmarkletCode(origin))}`);
  });

  it("offers the draggable link, the address it is tied to, and Copy the code", async () => {
    mockApi(routes({ "GET /inbox": () => jsonResponse(200, { items: [], reading: { count: 0, oldest_at: null, stalled: false } }) }));
    renderApp("/settings/capture");
    const link = await screen.findByTestId("bookmarklet");
    await waitFor(() => expect(link.getAttribute("href")).toBe(bookmarkletHref(window.location.origin)));
    expect(link).toHaveTextContent("Save to Kitchen ERP");
    expect(screen.getByText(window.location.origin)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Copy the code" })).toBeInTheDocument();
  });
});

describe("Add product's web address", () => {
  it("prefills the name from the address and says so", async () => {
    mockApi({
      ...routes(),
      "GET /inbox": () => jsonResponse(200, { items: [], reading: { count: 0, oldest_at: null, stalled: false } }),
      "GET /products": () => jsonResponse(200, { items: [], next_cursor: null }),
      "GET /units": () => jsonResponse(200, { items: [] }),
    });
    const user = userEvent.setup();
    renderApp("/catalog/products");
    await user.click((await screen.findAllByRole("button", { name: "Add product" }))[0]);
    const field = await screen.findByLabelText("Web address (optional)");
    await user.click(field);
    await user.paste(PAGE);
    await waitFor(() => expect(screen.getByLabelText("Name")).toHaveValue("Rolled oats 500g"));
    expect(screen.getByText("From the address")).toBeInTheDocument();
    expect(screen.getByText(/Item number 77123 · From the address\. This page can't be read from here/)).toBeInTheDocument();
  });
});
