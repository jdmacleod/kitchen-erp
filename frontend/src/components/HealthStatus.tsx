import { useHealth } from "../api/queries";
import type { Health } from "../api/types";

/**
 * The status dot's colour per state, measured rather than chosen (issue #21).
 *
 * WCAG 1.4.11 asks 3:1 of non-text UI that carries meaning. The word sits
 * beside the dot, so no state is conveyed by colour alone and the criterion
 * arguably does not bite -- but the dot is what people actually scan, and every
 * -500 was under the floor against the page it sits on.
 *
 * Contrast against the page background, Tailwind 4's palette, light
 * (neutral-50) / dark (neutral-950):
 *
 *   ok           green-500  2.13 x / 8.90     green-600  3.08 / 8.90
 *   degraded     amber-500  2.05 x / 9.23     amber-700  4.84 / 9.23
 *   failed       red-500    3.66   / 5.18     red-600    4.56 / 5.18
 *   unreachable  neutral-400 2.48 x / 7.63    neutral-500 4.53 / 7.63
 *
 * Light gets the darker step and dark keeps -500, which is the pairing
 * FirstRunChecklist already settled on for its tick. Degraded goes one step
 * further than the others because amber-600 clears the floor by 0.06 against
 * neutral-50, which is close enough to round the wrong way on a palette tweak.
 *
 * Move this table to DESIGN.md when that exists; it has to document the palette
 * anyway, and these numbers should not have to be derived twice.
 */
const dot: Record<string, string> = {
  ok: "bg-green-600 dark:bg-green-500",
  degraded: "bg-amber-700 dark:bg-amber-500",
  failed: "bg-red-600 dark:bg-red-500",
  unreachable: "bg-neutral-500 dark:bg-neutral-400",
};

export interface BuildIdentity {
  /** What identifies the build. Shown first, in mono: it is what a bug report needs. */
  primary: string;
  /** The friendly label, when it says something the sha does not. */
  secondary: string | null;
  isDev: boolean;
  /** The whole identity, matching what `kerp --version` prints. */
  title: string;
}

/**
 * Decide what the second line says.
 *
 * `BUILD_VERSION` comes from `git describe --tags --always --dirty`, so until the
 * first tag it *is* the sha, sometimes with a `-dirty` suffix. Printing
 * `4b4d8fc · 4b4d8fc-dirty` would be the same fact twice, so when the version
 * already starts with the commit it replaces the sha rather than following it.
 * Once a tag exists the two diverge and both are worth showing, sha first.
 *
 * Returns null when the fields are absent, which is what an older API sends: the
 * line disappears rather than printing `undefined`.
 */
export function buildIdentity(health: Health): BuildIdentity | null {
  const version = typeof health.version === "string" ? health.version : null;
  const commit = typeof health.commit === "string" ? health.commit : null;
  if (!version || !commit) return null;
  const versionRestatesTheSha = version.startsWith(commit);
  return {
    primary: versionRestatesTheSha ? version : commit,
    secondary: versionRestatesTheSha ? null : version,
    isDev: health.is_dev === true,
    title: `kitchen-erp ${version} (${commit})`,
  };
}

export function HealthStatus() {
  const health = useHealth();
  const word = health.isPending ? "checking" : health.isError ? "unreachable" : health.data.status;
  // No build line while pending or on a request error. It resolves in well under a
  // second, and the line above already carries the news that the API is unreachable.
  const build = health.isPending || health.isError ? null : buildIdentity(health.data);
  return (
    <div className="text-xs text-neutral-600 dark:text-neutral-400">
      {/* The live region covers the status only. The build identity never changes,
          and re-announcing a sha on every 60s poll would train people to ignore it. */}
      <p role="status" className="flex items-center gap-2">
        <span aria-hidden="true" className={`inline-block size-2 rounded-full ${dot[word] ?? dot.unreachable}`} />
        <span>
          System: <span data-testid="health-status">{word}</span>
        </span>
      </p>
      {build && (
        // pl-4 puts this under "System:", so the dot keeps the left edge and the
        // alert still reads first. It wraps rather than truncates: truncation eats
        // the sha, which is the part people copy.
        <p className="mt-0.5 break-words pl-4" data-testid="build-identity" title={build.title}>
          {build.isDev && (
            // Neutral, not amber: amber already means degraded in this block, and a
            // dev build is a choice rather than a fault.
            <span
              data-testid="build-dev-badge"
              className="mr-1 rounded bg-neutral-200 px-1 py-px text-[10px] font-semibold text-neutral-700 dark:bg-neutral-700 dark:text-neutral-200">
              dev
            </span>
          )}
          <span className="font-mono">{build.primary}</span>
          {build.secondary && <span> · {build.secondary}</span>}
        </p>
      )}
    </div>
  );
}
