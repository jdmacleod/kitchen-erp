import { errorMessage } from "../../api/client";
import { useHealth, useLogoutEverywhere } from "../../api/queries";
import { Badge, type BadgeTone } from "../../components/catalog/fields";
import { HealthStatus } from "../../components/HealthStatus";
import { Alert, Button, Card, PageHeader } from "../../components/ui";
import { usePageTitle } from "../../lib/usePageTitle";

const checkTone: Record<string, BadgeTone> = { ok: "good", degraded: "warn", failed: "danger" };

/** The per-check detail behind the sidebar's status dot. */
function Checks() {
  const health = useHealth();
  if (health.isPending) {
    return (
      <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
        Checking…
      </p>
    );
  }
  if (health.isError) return <Alert tone="error">{errorMessage(health.error)}</Alert>;
  const checks = Object.entries((health.data.checks ?? {}) as Record<string, { status?: string }>);
  if (checks.length === 0) return null;
  return (
    <ul aria-label="Checks" className="divide-y divide-neutral-200 dark:divide-neutral-800">
      {checks.map(([name, check]) => (
        <li key={name} className="flex items-center justify-between gap-3 py-2 text-sm">
          <span className="first-letter:uppercase">{name.replace(/_/g, " ")}</span>
          <Badge tone={checkTone[check.status ?? ""] ?? "neutral"}>{check.status ?? "unknown"}</Badge>
        </li>
      ))}
    </ul>
  );
}

/** Settings → System: health detail and "Log out everywhere" (T10). */
export function SystemPage() {
  usePageTitle("System");
  const logoutEverywhere = useLogoutEverywhere();
  return (
    <>
      <PageHeader title="System" description="How this deployment is doing, and your sessions on it." />
      <div className="flex flex-col gap-6">
        <Card>
          <h2 className="font-display mb-3 text-lg">Status</h2>
          <div className="mb-3">
            <HealthStatus />
          </div>
          <Checks />
        </Card>
        <Card>
          <h2 className="font-display mb-1 text-lg">Sessions</h2>
          <p className="mb-3 text-sm text-neutral-600 dark:text-neutral-400">
            Signs you out on every device, including this one. API tokens keep working; revoke them under API tokens.
          </p>
          {logoutEverywhere.isError ? <Alert tone="error">{errorMessage(logoutEverywhere.error)}</Alert> : null}
          <Button variant="secondary" disabled={logoutEverywhere.isPending} onClick={() => logoutEverywhere.mutate()}>
            Log out everywhere
          </Button>
        </Card>
      </div>
    </>
  );
}
