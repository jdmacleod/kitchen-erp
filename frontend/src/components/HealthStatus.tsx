import { useHealth } from "../api/queries";
import type { Health } from "../api/types";

const dot: Record<string, string> = {
  ok: "bg-green-500",
  degraded: "bg-amber-500",
  failed: "bg-red-500",
  unreachable: "bg-neutral-400",
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
