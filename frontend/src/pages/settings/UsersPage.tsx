import { useState, type FormEvent } from "react";
import { errorMessage, isApiError } from "../../api/client";
import { useCreateUser, useUsers } from "../../api/queries";
import { Alert, Button, Card, EmptyState, Field, PageHeader } from "../../components/ui";
import { formatDateTime } from "../../lib/format";
import { usePageTitle } from "../../lib/usePageTitle";

const emptyForm = { email: "", display_name: "", password: "" };

export function UsersPage() {
  usePageTitle("Users");
  const users = useUsers();
  const create = useCreateUser();
  const [form, setForm] = useState(emptyForm);
  const [created, setCreated] = useState<string | null>(null);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setCreated(null);
    create.mutate(
      { email: form.email.trim(), display_name: form.display_name.trim(), password: form.password },
      {
        onSuccess: (user) => {
          setForm(emptyForm);
          setCreated(user.email);
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
      <PageHeader title="Users" />
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
                    <th scope="col" className="py-2 font-semibold">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {users.data.map((u) => (
                    <tr key={u.id} className="border-t border-neutral-200 dark:border-neutral-800">
                      <td className="py-2 pr-3 font-medium">{u.display_name}</td>
                      <td className="py-2 pr-3">{u.email}</td>
                      <td className="py-2 pr-3">{u.role}</td>
                      <td className="py-2 pr-3">{u.active ? "active" : "inactive"}</td>
                      <td className="py-2 whitespace-nowrap">
                        <time dateTime={u.created_at}>{formatDateTime(u.created_at)}</time>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </Card>

        <Card>
          <form onSubmit={onSubmit} className="flex flex-col gap-4" aria-labelledby="create-user-heading">
            <h2 id="create-user-heading" className="text-lg font-medium">
              Add a member
            </h2>
            {createError ? <Alert tone="error">{createError}</Alert> : null}
            {created ? <Alert tone="success">Created {created}.</Alert> : null}
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
