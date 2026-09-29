import { expect, test } from "@playwright/test";

import { login, nextTag } from "./helpers";

/**
 * An enrichment tool's suggestion, from token to accepted fact (spec 03 §1F):
 * a scoped token posts it, the tool cannot read the vendor list, the inbox
 * offers the review, and accepting writes the value with its source.
 */
test("a scoped tool suggests, a person accepts", async ({ page, playwright, baseURL }) => {
  const stamp = nextTag();
  await login(page);
  const storeName = `E2E Beacon Grocer ${stamp}`;
  const created = await page.request.post("/api/v1/vendor-locations", {
    // The synthetic box from SECURITY.md.
    data: { name: "Elm St", lat: "33.660000", lon: "-120.550000", vendor: { name: storeName, kind: "chain" } },
    headers: { "Idempotency-Key": `e2e-suggest-loc-${stamp}` },
  });
  expect(created.status()).toBe(201);
  const location = (await created.json()) as { key: string; vendor: { id: string } };
  const token = await page.request.post("/api/v1/api-tokens", {
    data: { name: `E2E enricher ${stamp}`, scopes: ["vendors:read", "vendors:suggest"] },
  });
  const { plaintext } = (await token.json()) as { plaintext: string };

  const tool = await playwright.request.newContext({ baseURL, extraHTTPHeaders: { Authorization: `Bearer ${plaintext}` } });
  expect((await tool.get("/api/v1/vendors")).status()).toBe(403);
  const posted = await tool.post("/api/v1/vendor-suggestions", {
    data: {
      tool: "e2e-tool",
      tool_version: "1",
      items: [{ target: "location", key: location.key, field: "phone", proposed: "+1 555 0145", source_url: "https://beacon.example/elm" }],
    },
  });
  expect(posted.status()).toBe(201);
  await tool.dispose();

  await page.goto(`/catalog/vendors/${location.vendor.id}`);
  await page.getByRole("button", { name: "Review" }).click();
  const drawer = page.getByRole("dialog", { name: "Vendor suggestions" });
  await expect(drawer.getByRole("link", { name: "beacon.example ↗" })).toHaveAttribute("rel", "noopener noreferrer");
  await drawer.getByRole("button", { name: "Accept phone for Elm St" }).click();
  await expect(drawer.getByRole("button", { name: "Accept phone for Elm St" })).toHaveCount(0);
  await drawer.getByRole("button", { name: "Close" }).click();
  await expect(page.getByText("1 accepted.")).toBeVisible();

  await expect(page.getByRole("link", { name: "+1 555 0145" })).toBeVisible();
  await page.getByText("Sources").click();
  await expect(page.getByText("Suggested by e2e-tool", { exact: false })).toBeVisible();
});
