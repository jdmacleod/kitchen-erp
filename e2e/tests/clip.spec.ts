import { expect, test, type Page } from "@playwright/test";
import { bookmarkletCode } from "../../frontend/src/lib/bookmarklet";
import { ADMIN, nextTag } from "./helpers";

// Criterion 85 (04, 2M): the clip window and a page on another origin. The
// storefront is invented and served by Playwright itself, so nothing here
// reaches a real site.

const SHOP = "https://shop.example.test";

function storefront(tag: string): string {
  const product = { "@context": "https://schema.org", "@type": "Product", name: `E2E Rolled Oats ${tag}`, sku: "77123", offers: { "@type": "Offer", price: 3.49 } };
  return `<!doctype html><html><head><title>E2E Rolled Oats ${tag} – Shop</title>
<meta property="og:title" content="E2E Rolled Oats ${tag}">
<script type="application/ld+json">${JSON.stringify(product)}</script></head>
<body><nav>Menu</nav><main><h1>E2E Rolled Oats ${tag}</h1><p>Whole grain.</p></main></body></html>`;
}

/** A page that only opens the clip window and never answers it. */
const SILENT = (app: string) => `<!doctype html><html><body><button onclick="window.open('${app}/capture/clip','kerp-clip','width=440,height=620')">open</button></body></html>`;

async function serve(page: Page, html: string, headers: Record<string, string> = {}) {
  await page.context().route(`${SHOP}/**`, (route) => route.fulfill({ contentType: "text/html; charset=utf-8", body: html, headers }));
}

test.describe("the clip window", () => {
  test.skip(({ isMobile }) => isMobile, "pop-up windows are a desktop flow");

  test("completes the ready handshake with another origin, after signing in inside the window", async ({ page, baseURL }) => {
    const tag = nextTag();
    await serve(page, storefront(tag));
    await page.goto(`${SHOP}/p/rolled-oats-${tag}`);
    const opened = page.waitForEvent("popup");
    await page.evaluate(bookmarkletCode(new URL(baseURL!).origin));
    const popup = await opened;
    const saves: string[] = [];
    popup.on("request", (r) => {
      if (r.method() === "POST" && r.url().endsWith("/api/v1/product-captures")) saves.push(r.url());
    });

    await expect(popup.getByRole("heading", { name: "Save this product" })).toBeVisible();
    // Signed out: sign in inside the window, then the exchange repeats.
    await popup.getByLabel("Email").fill(ADMIN.email);
    await popup.getByLabel("Password").fill(ADMIN.password);
    await popup.getByRole("button", { name: "Sign in" }).click();

    await expect(popup.getByText(`E2E Rolled Oats ${tag} – Shop`)).toBeVisible();
    await expect(popup.getByText("Not one of your vendors.")).toBeVisible();
    // The page had no images: none is claimed.
    await expect(popup.getByText("0 images came along.")).toBeVisible();
    // Nothing is saved without a click.
    expect(saves).toEqual([]);
    await popup.getByLabel("Save without a store (no listing or price)").check();
    await popup.getByRole("button", { name: "Save" }).click();
    await expect(popup.getByText(/Saved ·/)).toBeVisible();
    expect(saves).toHaveLength(1);
    // The window stays open after saving.
    expect(popup.isClosed()).toBe(false);
  });

  test("ignores a message from any window but its opener, and saves nothing", async ({ page, baseURL }) => {
    const app = new URL(baseURL!).origin;
    await page.goto("/login");
    await page.getByLabel("Email").fill(ADMIN.email);
    await page.getByLabel("Password").fill(ADMIN.password);
    await page.getByRole("button", { name: "Sign in" }).click();
    await expect(page).not.toHaveURL(/\/login$/);

    await serve(page, SILENT(app));
    await page.goto(`${SHOP}/p/silent`);
    const opened = page.waitForEvent("popup");
    await page.getByRole("button", { name: "open" }).click();
    const popup = await opened;
    await expect(popup.getByText("Waiting for the page…")).toBeVisible();
    // A forged page, posted by the window itself rather than its opener.
    await popup.evaluate(() =>
      window.postMessage({ type: "kerp-clip", payload: { page_url: "https://forged.example.test/p/1", title: "Forged" } }, "*"),
    );
    await expect(popup.getByText("Forged")).toHaveCount(0);
    // The opener never answers: after the wait, the window says so.
    await expect(popup.getByText("This site blocks clipping. Paste the address in Add product instead.")).toBeVisible({ timeout: 15_000 });
  });

  test("says the site blocks clipping when the page cuts its opener", async ({ page, baseURL }) => {
    const tag = nextTag();
    await serve(page, storefront(tag), { "Cross-Origin-Opener-Policy": "same-origin" });
    await page.goto(`${SHOP}/p/coop-${tag}`);
    const opened = page.waitForEvent("popup");
    await page.evaluate(bookmarkletCode(new URL(baseURL!).origin));
    const popup = await opened;
    await expect(popup.getByText("This site blocks clipping. Paste the address in Add product instead.")).toBeVisible();
  });
});
