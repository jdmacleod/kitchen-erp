import { afterEach, describe, expect, it, vi } from "vitest";
import { CLIP_VERSION, bookmarkletCode } from "../lib/bookmarklet";

// Clip quality, CQ2: run the bookmarklet's own code on an invented store page and
// check what it would send.

const APP = "https://kitchen.example.test";

function page() {
  document.head.innerHTML = `
    <title>Canyon Creek Rolled Oats | Juniper Market</title>
    <meta property="og:title" content="Canyon Creek Rolled Oats">
    <meta property="og:image" content="https://cdn.example.test/og/oats.jpg">
    <meta itemprop="price" content="$3.49">
    <meta itemprop="sku" content="77123">
    <meta itemprop="color" content="beige">
    <script type="application/ld+json">{"@type":"Product","name":"Canyon Creek Rolled Oats","image":["https://cdn.example.test/ld/oats-front.jpg"]}</script>`;
  document.body.innerHTML = `
    <main>
      <img alt="Breakfast table at sunrise" src="https://cdn.example.test/promo/sunrise.jpg">
      <img alt="Canyon Creek rolled oats, front" src="https://cdn.example.test/p/oats-front-2.jpg?w=800">
      <img alt="Canyon Creek rolled oats, front" src="https://cdn.example.test/p/oats-front-2.jpg?w=1600">
      <img alt="tiny icon" src="https://cdn.example.test/icon.png">
    </main>`;
  const widths = [1200, 800, 1600, 32];
  document.querySelectorAll("img").forEach((img, n) => Object.defineProperty(img, "naturalWidth", { value: widths[n] }));
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  document.head.innerHTML = "";
  document.body.innerHTML = "";
});

describe("the bookmarklet's payload", () => {
  it("sends microdata, ranks product images first, counts one image once, and says its version", async () => {
    page();
    const win = { postMessage: vi.fn() };
    vi.stubGlobal("open", vi.fn(() => win));
    vi.stubGlobal("fetch", vi.fn(async () => { throw new Error("blocked"); }));
    let listener: ((e: MessageEvent) => unknown) | undefined;
    vi.spyOn(window, "addEventListener").mockImplementation((type: string, fn: unknown) => {
      if (type === "message") listener = fn as (e: MessageEvent) => unknown;
    });

    new Function(bookmarkletCode(APP))();
    await listener?.({ origin: APP, source: win, data: { type: "kerp-clip-ready" } } as unknown as MessageEvent);

    const sent = win.postMessage.mock.calls[0][0].payload;
    expect(sent.meta).toMatchObject({ "og:title": "Canyon Creek Rolled Oats", "itemprop:price": "$3.49", "itemprop:sku": "77123" });
    expect(sent.meta["itemprop:color"]).toBeUndefined();
    expect(sent.image_urls).toEqual([
      "https://cdn.example.test/ld/oats-front.jpg",
      "https://cdn.example.test/og/oats.jpg",
      "https://cdn.example.test/p/oats-front-2.jpg?w=800",
      "https://cdn.example.test/promo/sunrise.jpg",
    ]);
    expect(sent.clip_version).toBe(CLIP_VERSION);
  });

  it("sends photos only: a logo in SVG, an icon or a GIF never goes along", async () => {
    document.head.innerHTML = "<title>Product Detail</title>";
    document.body.innerHTML = `
      <img alt="Juniper Market" src="https://www.juniper-market.example.test/images/logo.svg">
      <img alt="" src="https://www.juniper-market.example.test/favicon.ico">
      <img alt="" src="https://www.juniper-market.example.test/spinner.gif">
      <img alt="" src="https://www.juniper-market.example.test/img/oats.webp">
      <img alt="" src="https://www.juniper-market.example.test/img/oats-back.jpg">`;
    document.querySelectorAll("img").forEach((img) => Object.defineProperty(img, "naturalWidth", { value: 400 }));
    const win = { postMessage: vi.fn() };
    vi.stubGlobal("open", vi.fn(() => win));
    // The store serves its "photo" as an SVG under a .jpg name: kept out by its type.
    const types: Record<string, string> = {
      "https://www.juniper-market.example.test/img/oats.webp": "image/webp",
      "https://www.juniper-market.example.test/img/oats-back.jpg": "image/svg+xml",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => ({ ok: true, blob: async () => new Blob(["x"], { type: types[url] ?? "image/png" }) })),
    );
    let listener: ((e: MessageEvent) => unknown) | undefined;
    vi.spyOn(window, "addEventListener").mockImplementation((type: string, fn: unknown) => {
      if (type === "message") listener = fn as (e: MessageEvent) => unknown;
    });

    new Function(bookmarkletCode(APP))();
    await listener?.({ origin: APP, source: win, data: { type: "kerp-clip-ready" } } as unknown as MessageEvent);

    const sent = win.postMessage.mock.calls[0][0].payload;
    expect(sent.image_urls).toEqual([
      "https://www.juniper-market.example.test/img/oats.webp",
      "https://www.juniper-market.example.test/img/oats-back.jpg",
    ]);
    expect(sent.images.map((i: { url: string }) => i.url)).toEqual(["https://www.juniper-market.example.test/img/oats.webp"]);
  });
});
