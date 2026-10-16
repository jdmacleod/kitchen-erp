import { type Page, expect } from "@playwright/test";

// Local development credentials only; see README. Never real ones.
export const ADMIN = {
  email: process.env.E2E_ADMIN_EMAIL ?? "admin@example.com",
  password: process.env.E2E_ADMIN_PASSWORD ?? "local-dev-admin-pw",
};

/**
 * A per-run, per-worker tag for any name the backend holds a unique index on.
 *
 * `Date.now()` alone is not enough: the suite runs three projects (desktop,
 * phone, phone-375) in parallel against ONE backend, their workers start within
 * the same millisecond, and each file computes its tag once at module load. Two
 * workers then pick the same name and the second create comes back 409 instead
 * of 201 -- an intermittent failure with nothing wrong in the code under test.
 * The random suffix is what makes it per-worker.
 */
export const nextTag = (): string =>
  `${Date.now().toString(36)}${Math.random().toString(36).slice(2, 7)}`;

export async function login(page: Page): Promise<void> {
  await loginAs(page, ADMIN.email, ADMIN.password);
}

/** Signs in as any user; the Plan specs make a member of their own per run. */
export async function loginAs(page: Page, email: string, password: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Email").fill(email);
  await page.getByLabel("Password").fill(password);
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page).not.toHaveURL(/\/login$/);
}

/** Collects every request to an origin other than the app's own. */
export function watchExternalRequests(page: Page, baseURL: string): string[] {
  const own = new URL(baseURL).origin;
  const external: string[] = [];
  page.on("request", (req) => {
    const url = req.url();
    if (!url.startsWith(own) && !url.startsWith("data:") && !url.startsWith("blob:")) {
      external.push(url);
    }
  });
  return external;
}

/**
 * Click one of the Vendors page's secondary actions (Find nearby, Export,
 * Import). Below 1024px they sit behind "More actions" (design D15).
 */
export async function vendorsAction(page: Page, name: string): Promise<void> {
  // The header renders its actions together; wait for it before asking which layout.
  await page.getByRole("button", { name: "Add vendor" }).first().waitFor();
  const more = page.getByRole("button", { name: "More actions", exact: true });
  if (await more.isVisible()) {
    await more.click();
    await page.getByRole("dialog", { name: "More actions" }).getByRole("button", { name }).click();
  } else {
    await page.getByRole("button", { name, exact: true }).click();
  }
}

/**
 * A page that scrolls sideways on a phone is a bug, and it is the one class of
 * bug the rest of this suite cannot see: every assertion here passes at desktop
 * width. The purchase form shipped overflowing because its Location `select`
 * sized itself to the longest option and grid children default to
 * `min-width: auto`, so the column could not shrink. `phone` runs at 390px and
 * stayed green; the overflow started below ~427px. Hence `phone-375`.
 */
export async function expectNoSidewaysScroll(page: Page, where: string): Promise<void> {
  // Measure against the configured device width, NOT window.innerWidth. Under
  // Playwright's mobile emulation the page carries a meta viewport, so an
  // overflowing layout makes the browser zoom out and grow innerWidth to match
  // the content: innerWidth reported 460 on a 375px device while the form ran
  // off the screen, and `scrollWidth <= innerWidth` was satisfied the whole
  // time. Comparing to the real device width is what makes this fail.
  const deviceWidth = page.viewportSize()?.width;
  expect(deviceWidth, "the project must declare a viewport").toBeTruthy();

  const { scrollWidth, innerWidth, widest } = await page.evaluate(() => {
    const doc = document.documentElement;
    let widest: { tag: string; cls: string; width: number } | null = null;
    for (const el of document.querySelectorAll("main *")) {
      const width = el.getBoundingClientRect().width;
      if (!widest || width > widest.width) {
        widest = {
          tag: el.tagName,
          cls: String(el.className).slice(0, 80),
          width: Math.round(width),
        };
      }
    }
    return { scrollWidth: doc.scrollWidth, innerWidth: window.innerWidth, widest };
  });

  expect(
    scrollWidth,
    `${where} is ${scrollWidth}px wide on a ${deviceWidth}px device` +
      ` (innerWidth ${innerWidth}px — inflated by zoom-out when content overflows)` +
      (widest
        ? `; widest element in main: <${widest.tag} class="${widest.cls}"> at ${widest.width}px`
        : ""),
  ).toBeLessThanOrEqual(deviceWidth as number);
}

/** Every rendered control under `scope` smaller than 44×44, described for the failure message. */
export async function controlsUnder44(page: Page, scope: string): Promise<string[]> {
  return page.evaluate((scope) => {
    const found: string[] = [];
    const selectors = ["a", "button", "input", "select", "textarea", "[role=option]"];
    const controls = document.querySelectorAll<HTMLElement>(
      scope
        .split(",")
        .flatMap((root) => selectors.map((sel) => `${root.trim()} ${sel}`))
        .join(", "),
    );
    for (const el of controls) {
      const box = el.getBoundingClientRect();
      if (box.width === 0 && box.height === 0) continue; // not rendered
      // A checkbox or radio is sized by its label, which is the target.
      const target = el.matches("input[type=checkbox], input[type=radio]")
        ? (el.closest("label") ?? el)
        : el;
      const t = target.getBoundingClientRect();
      // Half a pixel of slack: map markers sit on subpixel transforms.
      if (t.width < 43.5 || t.height < 43.5) {
        found.push(
          `<${el.tagName.toLowerCase()}> "${(el.textContent || el.getAttribute("aria-label") || "").trim().slice(0, 40)}" ${Math.round(t.width)}×${Math.round(t.height)}`,
        );
      }
    }
    return found;
  }, scope);
}

export interface FocusOutline {
  label: string;
  style: string;
  width: string;
  /** Whether the element matches :focus-visible. */
  visible: boolean;
  inScope: boolean;
}

/** The focused element's computed outline, and whether it is under `scope`. */
export async function focusedOutline(page: Page, scope = "body"): Promise<FocusOutline> {
  return page.evaluate((scope) => {
    const el = document.activeElement as HTMLElement | null;
    if (!el || el === document.body)
      return { label: "<body>", style: "", width: "", visible: false, inScope: false };
    const style = getComputedStyle(el);
    return {
      label: `<${el.tagName.toLowerCase()}> "${(el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 40)}"`,
      style: style.outlineStyle,
      width: style.outlineWidth,
      visible: el.matches(":focus-visible"),
      inScope: el.closest(scope) !== null,
    };
  }, scope);
}

/** Tabs from the top of the page and returns the outlines of the first `count` controls under `scope`. */
export async function firstFocusOutlines(
  page: Page,
  scope: string,
  count: number,
  maxTabs = 80,
): Promise<FocusOutline[]> {
  await page.evaluate(() => (document.activeElement as HTMLElement | null)?.blur());
  const found: FocusOutline[] = [];
  for (let i = 0; i < maxTabs && found.length < count; i++) {
    await page.keyboard.press("Tab");
    const ring = await focusedOutline(page, scope);
    if (ring.inScope) found.push(ring);
  }
  return found;
}

/**
 * Tabs through the page from the top and returns every control under `scope`
 * that took focus without a visible focus indicator, described for the failure
 * message. Focus arrives by the Tab key, so `:focus-visible` applies exactly as
 * it does for a keyboard user. An indicator is a non-zero outline that is not
 * `none`, or a box shadow (the ring utilities draw one).
 */
export async function focusRingsMissing(
  page: Page,
  scope: string,
  maxTabs = 150,
): Promise<string[]> {
  await page.evaluate(() => {
    (document.activeElement as HTMLElement | null)?.blur();
    window.scrollTo(0, 0);
  });
  const missing: string[] = [];
  const seen = new Set<string>();
  let inside = 0;
  for (let i = 0; i < maxTabs; i++) {
    await page.keyboard.press("Tab");
    const found = await page.evaluate((scope) => {
      const el = document.activeElement as HTMLElement | null;
      if (!el || el === document.body) return { key: "", inScope: false, ringed: true, label: "" };
      if (!el.dataset.e2eFocusKey) el.dataset.e2eFocusKey = String(Math.random());
      const style = getComputedStyle(el);
      const outline = style.outlineStyle !== "none" && parseFloat(style.outlineWidth) > 0;
      const shadow = style.boxShadow !== "none";
      return {
        key: el.dataset.e2eFocusKey,
        inScope: el.closest(scope) !== null,
        ringed: outline || shadow,
        label: `<${el.tagName.toLowerCase()}> "${(el.getAttribute("aria-label") || el.textContent || "").trim().slice(0, 40)}"`,
      };
    }, scope);
    if (!found.key) continue;
    if (seen.has(found.key)) break; // back to the start
    seen.add(found.key);
    if (!found.inScope) {
      if (inside > 0) break; // past the end of the scope
      continue;
    }
    inside += 1;
    if (!found.ringed) missing.push(found.label);
  }
  if (inside === 0) missing.push(`nothing under ${scope} took focus`);
  return missing;
}
