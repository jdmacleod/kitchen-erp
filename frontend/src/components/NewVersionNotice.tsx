import { useState } from "react";
import { useHealth } from "../api/queries";
import { alertTones, Button, focusRing } from "./ui";

/** A commit that names a real build; dev and older APIs send "unknown" or nothing. */
function knownCommit(commit: unknown): string | null {
  return typeof commit === "string" && commit !== "" && commit !== "unknown" ? commit : null;
}

/**
 * Tells an open tab that the stack was redeployed under it (issue 209).
 *
 * The tab remembers the commit /health reported when it first answered, and the
 * health poll (60 s, and on refocus) keeps asking. A different commit later means
 * the api, and with it the web bundle (`make up` rebuilds both), is newer than the
 * code this tab is running. It offers a reload and never reloads on its own,
 * because a form or drawer may hold typed input.
 *
 * This compares what the server reported at load with what it reports now, rather
 * than a build id baked into the bundle: the web image takes no build arguments,
 * and a tab only goes stale through a redeploy it has watched happen.
 *
 * It sits above the page's own Notice and outlives navigation, since a page
 * Notice is replaced by the next confirmation and cleared when the page changes.
 */
export function NewVersionNotice() {
  const health = useHealth();
  const current = health.isSuccess ? knownCommit(health.data.commit) : null;
  const [loadedWith, setLoadedWith] = useState<string | null>(null);
  const [dismissedFor, setDismissedFor] = useState<string | null>(null);
  if (loadedWith === null && current !== null) setLoadedWith(current);

  const stale = loadedWith !== null && current !== null && current !== loadedWith && dismissedFor !== current;
  return (
    <div aria-live="polite" aria-atomic="true">
      {stale ? (
        <div
          role="status"
          data-testid="new-version"
          className={`mb-6 flex flex-wrap items-center justify-between gap-2 rounded-2xl border px-4 py-3 text-sm ${alertTones.info}`}
        >
          <span>A new version is ready.</span>
          <span className="flex items-center gap-1">
            <Button onClick={() => window.location.reload()}>Reload</Button>
            <button
              type="button"
              aria-label="Not now"
              onClick={() => setDismissedFor(current)}
              className={`inline-flex size-11 items-center justify-center rounded-md hover:bg-black/5 lg:size-8 dark:hover:bg-white/10 ${focusRing}`}
            >
              <svg viewBox="0 0 16 16" className="size-3.5" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
                <path d="M4 4l8 8M12 4l-8 8" />
              </svg>
            </button>
          </span>
        </div>
      ) : null}
    </div>
  );
}
