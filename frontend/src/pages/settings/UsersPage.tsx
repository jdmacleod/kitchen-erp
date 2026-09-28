import { useState, type FormEvent } from "react";
import { Link } from "react-router";
import { useCurrentUser } from "../../auth/context";
import { errorMessage, isApiError } from "../../api/client";
import { useCreateUser, useEditUser, useSetUserPassword, useUsers } from "../../api/queries";
import type { User } from "../../api/types";
import { Drawer } from "../../components/Drawer";
import { Alert, Button, Card, EmptyState, Field, PageHeader, focusRing } from "../../components/ui";
import { formatDateTime } from "../../lib/format";
import { usePageTitle } from "../../lib/usePageTitle";
import { useNotice } from "../../components/Notice";

const emptyForm = { email: "", display_name: "", password: "" };

export function UsersPage() {
  usePageTitle("Users");
  const users = useUsers();
  const create = useCreateUser();
  const [form, setForm] = useState(emptyForm);
  const notice = useNotice();
  const [editing, setEditing] = useState<User | null>(null);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    create.mutate(
      { email: form.email.trim(), display_name: form.display_name.trim(), password: form.password },
      {
        onSuccess: (user) => {
          setForm(emptyForm);
          notice.show({ tone: "success", message: `Created ${user.email}.` });
        },
      },
    );
  };

  const createError = create.isError
    ? isApiError(create.error) && create.error.code === "email_taken"
      ? "A user with that email already exists."
      : errorMessage(create.error)
    : null;

  return (
    <>
      <PageHeader title="Users" description="The people in this household who can sign in." />
      <div className="flex flex-col gap-6">
        <Card>
          <h2 className="mb-3 text-lg font-medium">Household members</h2>
          {users.isPending ? (
            <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
              Loading…
            </p>
          ) : users.isError ? (
            <Alert tone="error">{errorMessage(users.error)}</Alert>
          ) : users.data.length === 0 ? (
            <EmptyState title="No users yet" />
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead className="text-left text-xs text-neutral-600 dark:text-neutral-400">
                  <tr>
                    <th scope="col" className="py-2 pr-3 font-semibold">Name</th>
                    <th scope="col" className="py-2 pr-3 font-semibold">Email</th>
                    <th scope="col" className="py-2 pr-3 font-semibold">Role</th>
                    <th scope="col" className="py-2 pr-3 font-semibold">Status</th>
                    <th scope="col" className="py-2 pr-3 font-semibold">Created</th>
                    <th scope="col" className="py-2 font-semibold">
                      <span className="sr-only">Actions</span>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {users.data.map((u) => (
                    <tr key={u.id} className="border-t border-neutral-200 dark:border-neutral-800">
                      <td className="py-2 pr-3 font-medium">{u.display_name}</td>
                      <td className="py-2 pr-3">{u.email}</td>
                      <td className="py-2 pr-3">{u.role}</td>
                      <td className="py-2 pr-3">{u.active ? "active" : "inactive"}</td>
                      <td className="py-2 pr-3 whitespace-nowrap">
                        <time dateTime={u.created_at}>{formatDateTime(u.created_at)}</time>
                      </td>
                      <td className="py-2 text-right">
                        <Button variant="secondary" onClick={() => setEditing(u)} aria-label={`Edit ${u.display_name}`}>
                          Edit
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        {editing ? (
          <EditUserDrawer
            user={editing}
            onClose={() => setEditing(null)}
            onDone={(message) => {
              setEditing(null);
              notice.show({ tone: "success", message });
            }}
          />
        ) : null}

        <Card>
          <form onSubmit={onSubmit} className="flex flex-col gap-4" aria-labelledby="create-user-heading">
            <h2 id="create-user-heading" className="text-lg font-medium">
              Add a member
            </h2>
            {createError ? <Alert tone="error">{createError}</Alert> : null}
            <Field
              id="new-user-email"
              label="Email"
              type="email"
              autoComplete="off"
              required
              value={form.email}
              onChange={(e) => setForm({ ...form, email: e.target.value })}
            />
            <Field
              id="new-user-name"
              label="Display name"
              type="text"
              autoComplete="off"
              required
              value={form.display_name}
              onChange={(e) => setForm({ ...form, display_name: e.target.value })}
            />
            <Field
              id="new-user-password"
              label="Password"
              type="password"
              autoComplete="new-password"
              required
              value={form.password}
              onChange={(e) => setForm({ ...form, password: e.target.value })}
              hint="Share it with them privately; they can sign in right away."
            />
            <div>
              <Button type="submit" disabled={create.isPending}>
                {create.isPending ? "Creating…" : "Create member"}
              </Button>
            </div>
          </form>
        </Card>
      </div>
    </>
  );
}

const muted = "text-neutral-600 dark:text-neutral-400";

/** What a failed user change means, in words. */
function userErrorMessage(error: unknown): string {
  if (isApiError(error)) {
    if (error.code === "email_taken") return "A user with that email already exists.";
    if (error.code === "self_lockout") return "You can't remove your own admin access. Another admin can.";
    if (error.code === "last_admin") return "The household needs at least one active admin.";
  }
  return errorMessage(error);
}

/**
 * Edit a member (#75): details, whether they can sign in, and a new password.
 * Your own row can't lose its admin access here, and your own password is
 * changed in Settings, System, with the current one.
 */
function EditUserDrawer({ user, onClose, onDone }: { user: User; onClose: () => void; onDone: (message: string) => void }) {
  const me = useCurrentUser();
  const self = me.id === user.id;
  const edit = useEditUser();
  const access = useEditUser();
  const setPassword = useSetUserPassword();
  const [form, setForm] = useState({ display_name: user.display_name, email: user.email, role: user.role });
  const [password, setPasswordText] = useState("");
  const [confirming, setConfirming] = useState(false);
  const dirty = form.display_name !== user.display_name || form.email !== user.email || form.role !== user.role || password !== "";

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const input: { display_name?: string; email?: string; role?: "admin" | "member" } = {};
    if (form.display_name.trim() !== user.display_name) input.display_name = form.display_name.trim();
    if (form.email.trim().toLowerCase() !== user.email) input.email = form.email.trim();
    if (form.role !== user.role) input.role = form.role;
    if (Object.keys(input).length === 0) return onClose();
    edit.mutate({ id: user.id, input }, { onSuccess: (u) => onDone(`Saved ${u.display_name}.`) });
  };

  const toggleAccess = () =>
    access.mutate(
      { id: user.id, input: { active: !user.active } },
      { onSuccess: (u) => onDone(u.active ? `${u.display_name} can sign in again.` : `${u.display_name} is deactivated and signed out everywhere.`) },
    );

  return (
    <Drawer title={`Edit ${user.display_name}`} thing="change" dirty={dirty} onClose={onClose} formId="edit-user" primaryLabel="Save changes" busy={edit.isPending} busyLabel="Saving…">
      <form id="edit-user" onSubmit={onSubmit} className="flex flex-col gap-4" aria-label={`Edit ${user.display_name}`}>
        {edit.isError ? <Alert tone="error">{userErrorMessage(edit.error)}</Alert> : null}
        <Field id="edit-user-name" label="Display name" autoComplete="off" required value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} />
        <Field id="edit-user-email" label="Email" type="email" autoComplete="off" required value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
        <fieldset className="flex flex-col gap-2">
          <legend className="text-sm font-medium">Role</legend>
          {(["member", "admin"] as const).map((role) => (
            <label key={role} className="inline-flex min-h-11 items-center gap-2 text-sm lg:min-h-8">
              <input type="radio" name="edit-user-role" value={role} checked={form.role === role} disabled={self} onChange={() => setForm({ ...form, role })} className={`size-4 ${focusRing}`} />
              {role === "admin" ? "Admin: also manages members and tokens" : "Member"}
            </label>
          ))}
          {self ? <p className={`text-xs ${muted}`}>You can't remove your own admin access. Another admin can.</p> : null}
        </fieldset>
      </form>

      <section className="mt-6 flex flex-col gap-3 border-t border-neutral-200 pt-4 dark:border-neutral-800" aria-labelledby="edit-user-access">
        <h3 id="edit-user-access" className="text-sm font-medium">
          Signing in
        </h3>
        {self ? (
          <p className={`text-sm ${muted}`}>
            Change your own password in{" "}
            <Link to="/settings/system" className={`underline underline-offset-2 ${focusRing}`}>
              Settings, System
            </Link>
            .
          </p>
        ) : (
          <>
            {access.isError ? <Alert tone="error">{userErrorMessage(access.error)}</Alert> : null}
            {user.active ? (
              confirming ? (
                <div className="flex flex-col gap-2 rounded-md border border-red-300 p-3 dark:border-red-900">
                  <p className="text-sm">
                    {user.display_name} is signed out everywhere, and their API tokens stop working. You can reactivate them later; the tokens stay revoked.
                  </p>
                  <div className="flex flex-wrap gap-2">
                    <Button variant="danger" disabled={access.isPending} onClick={toggleAccess}>
                      {access.isPending ? "Deactivating…" : "Deactivate"}
                    </Button>
                    <Button variant="secondary" onClick={() => setConfirming(false)}>
                      Keep them
                    </Button>
                  </div>
                </div>
              ) : (
                <div>
                  <Button variant="danger" onClick={() => setConfirming(true)}>
                    Deactivate {user.display_name}
                  </Button>
                </div>
              )
            ) : (
              <div>
                <Button variant="secondary" disabled={access.isPending} onClick={toggleAccess}>
                  {access.isPending ? "Reactivating…" : `Reactivate ${user.display_name}`}
                </Button>
              </div>
            )}

            <div className="flex flex-col gap-2">
              {setPassword.isError ? <Alert tone="error">{userErrorMessage(setPassword.error)}</Alert> : null}
              <Field
                id="edit-user-password"
                label="Set a new password"
                type="password"
                autoComplete="new-password"
                minLength={8}
                value={password}
                onChange={(e) => setPasswordText(e.target.value)}
                hint="At least 8 characters. It signs them out everywhere; share it with them privately."
              />
              <div>
                <Button
                  variant="secondary"
                  disabled={setPassword.isPending || password.length < 8}
                  onClick={() => setPassword.mutate({ id: user.id, password }, { onSuccess: (u) => onDone(`New password set for ${u.display_name}. They are signed out everywhere.`) })}
                >
                  {setPassword.isPending ? "Setting…" : "Set password"}
                </Button>
              </div>
            </div>
          </>
        )}
      </section>
    </Drawer>
  );
}
