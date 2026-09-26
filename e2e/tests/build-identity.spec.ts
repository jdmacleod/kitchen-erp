import { expect, test } from "@playwright/test";

import { login } from "./helpers";

/**
 * The only test that proves the build arg survived the whole chain:
 * `make up` -> compose `build.args` -> Dockerfile `ARG`/`ENV` -> `Settings` ->
 * the authenticated `/health` response -> the sidebar. Every unit test on either
 * side of that chain mocks the other side and would still pass with the wiring
 * cut.
 *
 * Run it after `make up` (or the CI step that exports the same two variables).
 * A bare `docker compose up -d --build` leaves the sentinels in place, and this
 * spec is what says so out loud rather than letting "dev" quietly become normal.
 */
test("the sidebar names the build this stack was made from", async ({ page }) => {
  await login(page);

  // On a phone the status line lives in the More sheet, one tap away, and the
  // desktop sidebar is still in the DOM behind `hidden lg:flex`. Match the
  // visible one so the spec covers every project without a strict-mode collision.
  // Decided by the viewport, not by whether the tab bar has rendered yet: an
  // instant isVisible() raced the first paint and skipped the tap (phone-375).
  if ((page.viewportSize()?.width ?? 1280) < 1024) {
    const more = page.getByRole("navigation", { name: "Tabs" }).getByRole("button", { name: "More" });
    await more.click();
  }

  const line = page.locator('[data-testid="build-identity"]:visible');
  await expect(line).toBeVisible();

  const title = await line.getAttribute("title");
  const parsed = title?.match(/^kitchen-erp (.+) \((.+)\)$/);
  expect(parsed, `build identity did not parse: ${title}`).not.toBeNull();
  const [, version, commit] = parsed!;

  // "unknown" is the Dockerfile default. Seeing it here means the arg never
  // reached the image, which is the exact failure this spec exists to catch.
  expect(commit, "the commit is the Dockerfile default").toMatch(/^[0-9a-f]{7,40}$/);
  expect(version, "the version is the Dockerfile default").not.toBe("dev");

  // Nothing narrower than this. `git describe --tags --always --dirty` emits a
  // tag name, which need not start with `v` (`is_dev_build` treats `0.2.0` as a
  // release); a tag plus `-5-gabc1234` once commits land on top; or the
  // abbreviated sha until a tag exists; any of them with `-dirty` on an
  // uncommitted tree. A shape assertion here would be a second copy of a rule
  // the backend already owns, and would fail the first time someone tags
  // without a `v`. The sha above is what proves the args traversed the stack:
  // "unknown" cannot match hex.
  expect(version, "the version is empty or not a single token").toMatch(/^\S+$/);

  // Whichever half leads, the sha is on screen: it is what a bug report needs.
  await expect(line).toContainText(version.startsWith(commit) ? version : commit);
});
