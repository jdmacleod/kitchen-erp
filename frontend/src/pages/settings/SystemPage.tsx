import { useState, type FormEvent } from "react";
import { errorMessage, isApiError } from "../../api/client";
import { useChangePassword, useHealth, useLogoutEverywhere } from "../../api/queries";
import { Badge, type BadgeTone } from "../../components/catalog/fields";
import { HealthStatus } from "../../components/HealthStatus";
import { useNotice } from "../../components/Notice";
import { Alert, Button, Card, Field, PageHeader } from "../../components/ui";
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

const emptyPasswords = { current_password: "", new_password: "", confirm: "" };

/** Change your own password (#75). This session stays; every other one ends. */
function ChangePassword() {
  const change = useChangePassword();
  const notice = useNotice();
  const [form, setForm] = useState(emptyPasswords);
  const [invalid, setInvalid] = useState<string | null>(null);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (form.new_password.length < 8) return setInvalid("The new password needs at least 8 characters.");
    if (form.new_password !== form.confirm) return setInvalid("The two new passwords don't match.");
    setInvalid(null);
    change.mutate(
      { current_password: form.current_password, new_password: form.new_password },
      {
        onSuccess: () => {
          setForm(emptyPasswords);
          notice.show({ tone: "success", message: "Password changed. You're signed out on your other devices." });
        },
      },
    );
  };

  const error = invalid ?? (change.isError ? (isApiError(change.error) && change.error.code === "wrong_password" ? "The current password is not right." : errorMessage(change.error)) : null);

  return (
    <Card>
      <form onSubmit={onSubmit} className="flex flex-col gap-4" aria-labelledby="change-password-heading">
        <div>
          <h2 id="change-password-heading" className="mb-1 text-lg font-medium">
            Password
          </h2>
          <p className="text-sm text-neutral-600 dark:text-neutral-400">Changing it keeps you signed in here and signs you out everywhere else.</p>
        </div>
        {error ? <Alert tone="error">{error}</Alert> : null}
        <Field id="current-password" label="Current password" type="password" autoComplete="current-password" required value={form.current_password} onChange={(e) => setForm({ ...form, current_password: e.target.value })} />
        <Field id="new-password" label="New password" type="password" autoComplete="new-password" required value={form.new_password} onChange={(e) => setForm({ ...form, new_password: e.target.value })} hint="At least 8 characters." />
        <Field id="confirm-password" label="New password again" type="password" autoComplete="new-password" required value={form.confirm} onChange={(e) => setForm({ ...form, confirm: e.target.value })} />
        <div>
          <Button type="submit" variant="secondary" disabled={change.isPending}>
            {change.isPending ? "Changing…" : "Change password"}
          </Button>
        </div>
      </form>
    </Card>
  );
}

/** Settings → System: health detail, your password, and "Log out everywhere" (T10). */
export function SystemPage() {
  usePageTitle("System");
  const logoutEverywhere = useLogoutEverywhere();
  return (
    <>
      <PageHeader title="System" description="How this deployment is doing, and your sessions on it." />
      <div className="flex flex-col gap-6">
        <Card>
          <h2 className="mb-3 text-lg font-medium">Status</h2>
          <div className="mb-3">
            <HealthStatus />
          </div>
          <Checks />
        </Card>
        <ChangePassword />
        <Card>
          <h2 className="mb-1 text-lg font-medium">Sessions</h2>
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
