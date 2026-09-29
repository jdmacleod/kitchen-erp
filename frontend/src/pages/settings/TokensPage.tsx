import { useState, type FormEvent } from "react";
import { errorMessage } from "../../api/client";
import { useCreateToken, useRevokeToken, useTokens } from "../../api/queries";
import type { ApiToken } from "../../api/types";
import { Alert, Button, Card, EmptyState, Field, PageHeader, focusRing } from "../../components/ui";
import { formatDateTime } from "../../lib/format";
import { usePageTitle } from "../../lib/usePageTitle";

/**
 * What a token may do, as three named choices (spec 03 §1F, design D16). A
 * scoped token is refused everywhere except the routes its scope names.
 */
export const TOKEN_ACCESS = [
  { value: "full", scopes: ["*"], label: "Full access", hint: "Everything you can do. For capture apps and your own scripts." },
  { value: "read", scopes: ["vendors:read"], label: "Read the public vendor list", hint: "Only the public vendor export." },
  {
    value: "suggest",
    scopes: ["vendors:read", "vendors:suggest"],
    label: "Read and suggest vendor facts",
    hint: "For an enrichment tool. Suggestions wait for your review. Cannot see notes, home bases, purchases or receipts.",
  },
] as const;
type Access = (typeof TOKEN_ACCESS)[number]["value"];

/** A token's access in the same words as the choice that made it. */
export function accessLabel(scopes: string[]): string {
  const key = [...scopes].sort().join(" ");
  return TOKEN_ACCESS.find((a) => [...a.scopes].sort().join(" ") === key)?.label ?? scopes.join(", ");
}

interface Reveal {
  id: string;
  name: string;
  plaintext: string;
}

export function TokensPage() {
  usePageTitle("API tokens");
  const tokens = useTokens();
  const create = useCreateToken();
  const [name, setName] = useState("");
  const [access, setAccess] = useState<Access>("full");
  // The plaintext lives only here, in component state, and only until dismissed.
  const [reveal, setReveal] = useState<Reveal | null>(null);

  const onSubmit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setReveal(null);
    const scopes = [...(TOKEN_ACCESS.find((a) => a.value === access)?.scopes ?? ["*"])];
    create.mutate(
      { name: name.trim(), scopes },
      {
        onSuccess: (result) => {
          setName("");
          setAccess("full");
          setReveal({ id: result.token.id, name: result.token.name, plaintext: result.plaintext });
        },
      },
    );
  };

  return (
    <>
      <PageHeader title="API tokens" description="Keys for apps that capture prices and receipts on your behalf." />
      <div className="flex flex-col gap-6">
        {reveal ? <NewTokenPanel reveal={reveal} onDismiss={() => setReveal(null)} /> : null}

        <Card>
          <form onSubmit={onSubmit} className="flex flex-col gap-4" aria-labelledby="create-token-heading">
            <h2 id="create-token-heading" className="text-lg font-medium">
              Create a token
            </h2>
            {create.isError ? <Alert tone="error">{errorMessage(create.error)}</Alert> : null}
            <Field
              id="new-token-name"
              label="Name"
              type="text"
              autoComplete="off"
              required
              value={name}
              onChange={(e) => setName(e.target.value)}
              hint="Something that tells you which device or script uses it."
            />
            <fieldset className="flex flex-col gap-1">
              <legend className="mb-1 text-sm font-medium">What it can do</legend>
              {TOKEN_ACCESS.map((a) => (
                <label key={a.value} className="flex min-h-11 items-start gap-2 py-1 text-sm lg:min-h-10">
                  <input
                    type="radio"
                    name="new-token-access"
                    value={a.value}
                    checked={access === a.value}
                    onChange={() => setAccess(a.value)}
                    aria-describedby={`token-access-${a.value}`}
                    className={`mt-0.5 size-4 ${focusRing}`}
                  />
                  <span>
                    <span className="font-medium">{a.label}</span>
                    <span id={`token-access-${a.value}`} className="block text-xs text-neutral-600 dark:text-neutral-400">
                      {a.hint}
                    </span>
                  </span>
                </label>
              ))}
            </fieldset>
            <div>
              <Button type="submit" disabled={create.isPending}>
                {create.isPending ? "Creating…" : "Create token"}
              </Button>
            </div>
          </form>
        </Card>

        <Card>
          <h2 className="mb-3 text-lg font-medium">Your tokens</h2>
          {tokens.isPending ? (
            <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
              Loading…
            </p>
          ) : tokens.isError ? (
            <Alert tone="error">{errorMessage(tokens.error)}</Alert>
          ) : tokens.data.length === 0 ? (
            <EmptyState title="No tokens yet" />
          ) : (
            <ul aria-label="Your tokens" className="divide-y divide-neutral-200 dark:divide-neutral-800">
              {tokens.data.map((t) => (
                <TokenRow
                  key={t.id}
                  token={t}
                  // A revoked key is useless; leaving it on screen invited copying it.
                  onRevoked={() => setReveal((r) => (r?.id === t.id ? null : r))}
                />
              ))}
            </ul>
          )}
        </Card>
      </div>
    </>
  );
}

function NewTokenPanel({ reveal, onDismiss }: { reveal: Reveal; onDismiss: () => void }) {
  const [copied, setCopied] = useState<"idle" | "copied" | "failed">("idle");

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(reveal.plaintext);
      setCopied("copied");
    } catch {
      setCopied("failed");
    }
  };

  return (
    <section
      aria-labelledby="new-token-heading"
      className="rounded-lg border border-green-300 bg-green-50 p-4 dark:border-green-900 dark:bg-green-950"
    >
      <h2 id="new-token-heading" className="text-lg font-medium text-green-900 dark:text-green-100">
        Token “{reveal.name}” created
      </h2>
      <p className="mt-1 text-sm text-green-900 dark:text-green-100">
        Copy it now. It is shown once and cannot be retrieved later. Send it on each request as the header{" "}
        <code className="font-mono">Authorization: Bearer &lt;token&gt;</code>.
      </p>
      <output
        aria-label="Token"
        data-testid="token-plaintext"
        className="mt-3 block break-all rounded-md border border-green-300 bg-white px-3 py-2 font-mono text-sm text-neutral-900 select-all dark:border-green-800 dark:bg-neutral-900 dark:text-neutral-100"
      >
        {reveal.plaintext}
      </output>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        <Button variant="secondary" onClick={copy}>
          Copy
        </Button>
        <Button variant="secondary" onClick={onDismiss}>
          Dismiss
        </Button>
        <span role="status" className="text-sm text-green-900 dark:text-green-100">
          {copied === "copied" ? "Copied." : copied === "failed" ? "Copy failed; select the text instead." : ""}
        </span>
      </div>
    </section>
  );
}

function TokenRow({ token, onRevoked }: { token: ApiToken; onRevoked: () => void }) {
  const revoke = useRevokeToken();
  const [confirming, setConfirming] = useState(false);
  const revoked = token.revoked_at !== null;

  return (
    <li className="flex flex-wrap items-center justify-between gap-3 py-3">
      <div className="min-w-0">
        <p className={`font-medium ${revoked ? "text-neutral-600 dark:text-neutral-400 line-through" : ""}`}>{token.name}</p>
        <p className="text-xs text-neutral-600 dark:text-neutral-400">
          {accessLabel(token.scopes)}
          {" · "}
          Created <time dateTime={token.created_at}>{formatDateTime(token.created_at)}</time>
          {" · "}
          {token.last_used_at ? (
            <>
              Last used <time dateTime={token.last_used_at}>{formatDateTime(token.last_used_at)}</time>
            </>
          ) : (
            "Never used"
          )}
          {revoked ? (
            <>
              {" · "}Revoked <time dateTime={token.revoked_at ?? ""}>{formatDateTime(token.revoked_at)}</time>
            </>
          ) : null}
        </p>
        {revoke.isError ? (
          <p role="alert" className="mt-1 text-xs text-red-700 dark:text-red-300">
            {errorMessage(revoke.error)}
          </p>
        ) : null}
      </div>
      {revoked ? null : confirming ? (
        <div className="flex gap-2" role="group" aria-label={`Confirm revoking ${token.name}`}>
          <Button
            variant="danger"
            disabled={revoke.isPending}
            onClick={() => revoke.mutate(token.id, { onSuccess: onRevoked, onSettled: () => setConfirming(false) })}
          >
            {revoke.isPending ? "Revoking…" : "Confirm revoke"}
          </Button>
          <Button variant="secondary" className={focusRing} onClick={() => setConfirming(false)}>
            Cancel
          </Button>
        </div>
      ) : (
        <Button variant="danger" onClick={() => setConfirming(true)} aria-label={`Revoke ${token.name}`}>
          Revoke
        </Button>
      )}
    </li>
  );
}
