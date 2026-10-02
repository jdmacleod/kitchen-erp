// The "Save to Kitchen ERP" bookmarklet (04, 2M; PD19). It runs on a vendor's
// page, in that page's origin, so it is kept as a plain string: nothing the
// bundler does can change what it runs, and it can be read here in full.
//
// What it does:
// 1. Opens /capture/clip on this app.
// 2. Waits for that window to say it is ready (a message from this app's
//    origin, from that window), and only then sends the page (F9).
// 3. Sends the page and canonical addresses, the title and meta tags, each
//    structured-data block as raw text, the visible text of the product region
//    (main, never navigation or headers) up to 200 KB, up to 12 image
//    addresses, and up to four of those images it could read itself, 5 MB
//    each (best effort).
//
// It never reads cookies, storage or form values, and it answers each "ready"
// (the window repeats it after a sign-in).

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
  const add = (u) => { const a = abs(u); if (a && !images.includes(a) && images.length < 12) images.push(a); };
  add(meta["og:image"]);
  (region || document.body).querySelectorAll("img").forEach((i) => { if ((i.naturalWidth || 0) >= 200) add(i.currentSrc || i.src); });
  const canonical = document.querySelector('link[rel="canonical"]');
  const read = async (u) => {
    try {
      const r = await fetch(u, { credentials: "omit" });
      const b = await r.blob();
      if (!r.ok || b.size > 5 * 1024 * 1024 || !/^image\\//.test(b.type)) return null;
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
  return SOURCE.replace("__APP__", JSON.stringify(origin));
}

/** The link to drag to the bookmarks bar. */
export function bookmarkletHref(origin: string): string {
  return `javascript:${encodeURIComponent(bookmarkletCode(origin))}`;
}
