// The "Save to Kitchen ERP" bookmarklet (04, 2M; PD19). It runs on a vendor's
// page, in that page's origin, so it is kept as a plain string: nothing the
// bundler does can change what it runs, and it can be read here in full.
//
// What it does:
// 1. Opens /capture/clip on this app.
// 2. Waits for that window to say it is ready (a message from this app's
//    origin, from that window), and only then sends the page (F9).
// 3. Sends the page and canonical addresses, the title and meta tags (with the
//    microdata ones, as "itemprop:<name>"), each structured-data block as raw
//    text, the visible text of the product region (main, never navigation or
//    headers) up to 200 KB, up to 12 image addresses, and up to four of those
//    images it could read itself, 5 MB each (best effort). Images are ranked:
//    the structured data's, then og:image, then page images whose alt text or
//    file name share words with the title; the same image at another size
//    (same host and path) counts once. Only photos count: an address ending in
//    .svg, .ico or .gif is left out, and an image read is kept only when it is
//    JPEG, PNG, WebP or HEIC, so a page's own logo never travels as a photo.
// 4. Says which version of this code it is, so the window can ask for a
//    reinstall when the bookmark is older than CLIP_VERSION.
//
// It never reads cookies, storage or form values, and it answers each "ready"
// (the window repeats it after a sign-in).

/** Raise when the bookmarklet changes in a way an old bookmark misses.
 *  3: photos only (no SVG, icon or GIF images). */
export const CLIP_VERSION = 3;

const SOURCE = `(() => {
  const APP = __APP__;
  const win = window.open(APP + "/capture/clip", "kerp-clip", "width=440,height=620");
  if (!win) { alert("Allow pop-ups on this page to save it to Kitchen ERP."); return; }
  const abs = (u) => { if (!u) return null; try { const x = new URL(u, location.href); return /^https?:$/.test(x.protocol) ? x.href : null; } catch (e) { return null; } };
  const meta = {};
  document.querySelectorAll("meta[property],meta[name]").forEach((m) => {
    const k = (m.getAttribute("property") || m.getAttribute("name") || "").toLowerCase();
    if (/^(og:|product:|twitter:)|^description$/.test(k) && m.content && Object.keys(meta).length < 300) meta[k] = m.content.slice(0, 4000);
  });
  document.querySelectorAll("meta[itemprop]").forEach((m) => {
    const k = "itemprop:" + (m.getAttribute("itemprop") || "").toLowerCase();
    if (/^itemprop:(price|pricecurrency|sku|gtin(8|12|13|14)?|brand|name)$/.test(k) && m.content && !(k in meta) && Object.keys(meta).length < 300) meta[k] = m.content.slice(0, 4000);
  });
  const blocks = [...document.querySelectorAll('script[type="application/ld+json"]')].map((s) => s.textContent || "").slice(0, 20);
  const region = document.querySelector("main,[role=main],article");
  let text = "";
  if (region) { text = region.innerText || ""; } else {
    const copy = document.body.cloneNode(true);
    copy.querySelectorAll("nav,header,footer,script,style,noscript,form").forEach((n) => n.remove());
    text = copy.textContent || "";
  }
  text = text.replace(/\\s+\\n/g, "\\n").slice(0, 200000);
  while (new TextEncoder().encode(text).length > 204800) text = text.slice(0, Math.floor(text.length * 0.9));
  const images = [];
  const seen = new Set();
  const add = (u) => {
    const a = abs(u); if (!a || images.length >= 12) return;
    const x = new URL(a); const key = x.host + x.pathname;
    if (/\\.(svg|ico|gif)$/i.test(x.pathname)) return;
    if (seen.has(key)) return; seen.add(key); images.push(a);
  };
  const walk = (o, d) => {
    if (!o || d > 6 || typeof o !== "object") return;
    if (Array.isArray(o)) { o.forEach((v) => walk(v, d + 1)); return; }
    const im = o.image; [].concat(im || []).forEach((v) => add(typeof v === "string" ? v : v && (v.url || v.contentUrl)));
    Object.keys(o).forEach((k) => { if (k !== "image") walk(o[k], d + 1); });
  };
  blocks.forEach((b) => { try { walk(JSON.parse(b), 0); } catch (e) {} });
  add(meta["og:image"]);
  const words = (meta["og:title"] || document.title).toLowerCase().split(/[^a-z0-9]+/).filter((w) => w.length > 2);
  const score = (i) => { const t = ((i.alt || "") + " " + (i.currentSrc || i.src || "")).toLowerCase(); return words.filter((w) => t.includes(w)).length; };
  [...(region || document.body).querySelectorAll("img")]
    .filter((i) => (i.naturalWidth || 0) >= 200)
    .map((i, n) => [i, score(i), n])
    .sort((a, b) => b[1] - a[1] || a[2] - b[2])
    .forEach(([i]) => add(i.currentSrc || i.src));
  const canonical = document.querySelector('link[rel="canonical"]');
  const read = async (u) => {
    try {
      const r = await fetch(u, { credentials: "omit" });
      const b = await r.blob();
      if (!r.ok || b.size > 5 * 1024 * 1024 || !/^image\\/(jpeg|png|webp|heic|heif)$/.test(b.type)) return null;
      const d = await new Promise((ok, no) => { const f = new FileReader(); f.onload = () => ok(f.result); f.onerror = no; f.readAsDataURL(b); });
      return { url: u, data_base64: String(d).split(",")[1] };
    } catch (e) { return null; }
  };
  let payload = null;
  const build = async () => {
    if (payload) return payload;
    const got = (await Promise.all(images.slice(0, 4).map(read))).filter(Boolean);
    payload = {
      page_url: location.href.slice(0, 2048),
      canonical_url: canonical ? abs(canonical.getAttribute("href")) : null,
      title: document.title.slice(0, 500),
      meta, structured_data: blocks, dom_text: text, image_urls: images, images: got,
      clip_version: __VERSION__,
    };
    return payload;
  };
  const listen = async (e) => {
    if (e.origin !== APP || e.source !== win || !e.data || e.data.type !== "kerp-clip-ready") return;
    win.postMessage({ type: "kerp-clip", payload: await build() }, APP);
  };
  window.addEventListener("message", listen);
  setTimeout(() => window.removeEventListener("message", listen), 15 * 60 * 1000);
})();`;

/** The bookmarklet's code for an app at ``origin``. */
export function bookmarkletCode(origin: string): string {
  return SOURCE.replace("__APP__", JSON.stringify(origin)).replace("__VERSION__", String(CLIP_VERSION));
}

/** The link to drag to the bookmarks bar. */
export function bookmarkletHref(origin: string): string {
  return `javascript:${encodeURIComponent(bookmarkletCode(origin))}`;
}
