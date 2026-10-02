import { useEffect, useRef, useState, type FormEvent } from "react";
import { api, errorMessage, isApiError, newIdempotencyKey } from "../../api/client";
import { useVendors } from "../../api/geo";
import { useLogin, useMe } from "../../api/queries";
import type { Proposal } from "../../api/proposals";
import { Alert, Button, Field, focusRing } from "../../components/ui";
import { usePageTitle } from "../../lib/usePageTitle";

/** What the bookmarklet sends; everything in it is untrusted and checked here. */
export interface ClipPayload {
  page_url: string;
  canonical_url: string | null;
  title: string;
  meta: Record<string, string>;
  structured_data: string[];
  dom_text: string;
  image_urls: string[];
  images: { url: string; data_base64: string }[];
}

const isHttp = (u: unknown): u is string => typeof u === "string" && u.length <= 2048 && /^https?:\/\//i.test(u);
const strings = (v: unknown, max: number): string[] => (Array.isArray(v) ? v.filter((x): x is string => typeof x === "string").slice(0, max) : []);

/** The page from a message, or null when it is not one the bookmarklet would send. */
export function readClip(data: unknown): ClipPayload | null {
  if (typeof data !== "object" || data === null || (data as { type?: unknown }).type !== "kerp-clip") return null;
  const p = (data as { payload?: unknown }).payload;
  if (typeof p !== "object" || p === null) return null;
  const raw = p as Record<string, unknown>;
  if (!isHttp(raw.page_url)) return null;
  const meta: Record<string, string> = {};
  if (typeof raw.meta === "object" && raw.meta !== null) {
    for (const [k, v] of Object.entries(raw.meta as Record<string, unknown>).slice(0, 300)) if (typeof v === "string") meta[k] = v.slice(0, 4000);
  }
  const images = Array.isArray(raw.images)
    ? raw.images
        .filter((i): i is { url: string; data_base64: string } => typeof i === "object" && i !== null && isHttp((i as { url?: unknown }).url) && typeof (i as { data_base64?: unknown }).data_base64 === "string")
        .slice(0, 4)
    : [];
  return {
    page_url: raw.page_url,
    canonical_url: isHttp(raw.canonical_url) ? raw.canonical_url : null,
    title: typeof raw.title === "string" ? raw.title.slice(0, 500) : "",
    meta,
    structured_data: strings(raw.structured_data, 20),
    dom_text: typeof raw.dom_text === "string" ? raw.dom_text : "",
    image_urls: strings(raw.image_urls, 12).filter(isHttp),
    images,
  };
}

interface AddressPreview {
  vendor: { id: string; name: string } | null;
  canonical_url: string;
  title: string | null;
  item_number: string | null;
}

type State =
  | { kind: "blocked" }
  | { kind: "waiting" }
  | { kind: "ready"; clip: ClipPayload }
  | { kind: "saved"; proposal: Proposal; already: boolean }
  | { kind: "too_large" };

/** How long the window waits for the page before saying the site blocks clipping. */
const WAIT_MS = 10_000;

/**
 * The clip window (10; PD9, PD21): opened by the bookmarklet, no app chrome. It
 * says it is ready, accepts one message, only from the window that opened it,
 * shows what will be saved, and saves only when the person clicks Save.
 */
export function ClipPage() {
  usePageTitle("Save this product");
  const me = useMe();
  const signedIn = Boolean(me.data);
  const [state, setState] = useState<State>(() => (window.opener ? { kind: "waiting" } : { kind: "blocked" }));
  const accepted = useRef(false);

  // Ready handshake (F9): say so once signed in (again after a sign-in), then take
  // the first message from the opener that is a page.
  useEffect(() => {
    if (!signedIn || !window.opener) return;
    const onMessage = (e: MessageEvent) => {
      if (accepted.current || e.source !== window.opener) return;
      const clip = readClip(e.data);
      if (!clip) return;
      accepted.current = true;
      setState({ kind: "ready", clip });
    };
    window.addEventListener("message", onMessage);
    // The opener is another site: "ready" carries nothing, so any origin may hear it.
    window.opener.postMessage({ type: "kerp-clip-ready" }, "*");
    const timer = window.setTimeout(() => {
      if (!accepted.current) setState({ kind: "blocked" });
    }, WAIT_MS);
    return () => {
      window.removeEventListener("message", onMessage);
      window.clearTimeout(timer);
    };
  }, [signedIn]);

  return (
    <main className="mx-auto flex min-h-dvh max-w-md flex-col gap-4 p-4">
      <h1 className="text-2xl">Save this product</h1>
      {state.kind === "blocked" ? (
        // Said even when signed out: signing in could not reconnect a cut-off page.
        <Alert tone="info">This site blocks clipping. Paste the address in Add product instead.</Alert>
      ) : me.isPending ? (
        <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
          Loading…
        </p>
      ) : !signedIn ? (
        <SignIn />
      ) : state.kind === "waiting" ? (
        <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
          Waiting for the page…
        </p>
      ) : state.kind === "too_large" ? (
        <Alert tone="error">This page is too large to save.</Alert>
      ) : state.kind === "saved" ? (
        <Saved proposal={state.proposal} already={state.already} />
      ) : (
        <Preview clip={state.clip} onSaved={(proposal, already) => setState({ kind: "saved", proposal, already })} onTooLarge={() => setState({ kind: "too_large" })} />
      )}
    </main>
  );
}

function SignIn() {
  const login = useLogin();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const onSubmit = (e: FormEvent) => {
    e.preventDefault();
    login.mutate({ email: email.trim(), password });
  };
  return (
    <form onSubmit={onSubmit} className="flex flex-col gap-3" aria-label="Sign in">
      <p className="text-sm">Sign in to save this page.</p>
      {login.isError ? <Alert tone="error">{errorMessage(login.error)}</Alert> : null}
      <Field id="clip-email" label="Email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} />
      <Field id="clip-password" label="Password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} />
      <Button type="submit" disabled={login.isPending}>
        {login.isPending ? "Signing in…" : "Sign in"}
      </Button>
    </form>
  );
}

function Preview({ clip, onSaved, onTooLarge }: { clip: ClipPayload; onSaved: (p: Proposal, already: boolean) => void; onTooLarge: () => void }) {
  const [preview, setPreview] = useState<AddressPreview | null>(null);
  const [vendorId, setVendorId] = useState<string>("");
  const [withoutStore, setWithoutStore] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [busy, setBusy] = useState(false);
  const key = useRef(newIdempotencyKey());
  const vendors = useVendors("", false, preview !== null && preview.vendor === null);

  useEffect(() => {
    let live = true;
    api<AddressPreview>("/product-captures/address", { method: "POST", body: { page_url: clip.page_url } })
      .then((p) => live && setPreview(p))
      .catch((e) => live && setError(e));
    return () => {
      live = false;
    };
  }, [clip.page_url]);

  const save = async () => {
    setBusy(true);
    setError(null);
    try {
      const body = {
        ...clip,
        channel: "clip",
        vendor_id: preview?.vendor ? preview.vendor.id : vendorId || undefined,
        without_store: !preview?.vendor && !vendorId && withoutStore,
      };
      const response = await fetch("/api/v1/product-captures", {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", Accept: "application/json", "Idempotency-Key": key.current },
        body: JSON.stringify(body),
      });
      if (response.status === 413) {
        onTooLarge();
        return;
      }
      const payload = await response.json();
      if (!response.ok) throw new Error(payload?.error?.message ?? `Saving failed with status ${response.status}.`);
      onSaved(payload as Proposal, response.status === 200);
    } catch (e) {
      setError(e);
    } finally {
      setBusy(false);
    }
  };

  const needsChoice = preview !== null && preview.vendor === null && !vendorId && !withoutStore;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-col gap-1 text-sm">
        <p className="font-medium">{clip.title || preview?.title || "Untitled page"}</p>
        <p className="break-all text-neutral-600 dark:text-neutral-400">{clip.page_url}</p>
        <p className="text-neutral-600 dark:text-neutral-400">
          {clip.image_urls.length === 1 ? "1 image" : `${clip.image_urls.length} images`} came along
          {clip.images.length > 0 ? `, ${clip.images.length} saved with it` : ""}.
        </p>
      </div>
      {preview === null && !error ? (
        <p role="status" className="text-sm text-neutral-600 dark:text-neutral-400">
          Matching the store…
        </p>
      ) : preview?.vendor ? (
        <p className="text-sm">From {preview.vendor.name}</p>
      ) : preview ? (
        <div className="flex flex-col gap-2 rounded-md border border-amber-400 p-3 text-sm dark:border-amber-600">
          <p className="text-amber-900 dark:text-amber-200">Not one of your vendors.</p>
          <label className="flex flex-col gap-1">
            Which vendor is this?
            <select
              value={vendorId}
              disabled={withoutStore}
              onChange={(e) => setVendorId(e.target.value)}
              className={`min-h-11 rounded-md border border-neutral-300 bg-white px-2 dark:border-neutral-700 dark:bg-neutral-900 ${focusRing}`}
            >
              <option value="">Choose a vendor</option>
              {(vendors.data ?? []).map((v) => (
                <option key={v.id} value={v.id}>
                  {v.name}
                </option>
              ))}
            </select>
          </label>
          <label className="inline-flex min-h-11 items-center gap-2">
            <input type="checkbox" checked={withoutStore} onChange={(e) => setWithoutStore(e.target.checked)} className={`size-4 ${focusRing}`} />
            Save without a store (no listing or price)
          </label>
        </div>
      ) : null}
      {error ? <Alert tone="error">{isApiError(error) || error instanceof Error ? errorMessage(error) : "Saving failed."}</Alert> : null}
      <div className="flex gap-2">
        <Button disabled={busy || preview === null || needsChoice} onClick={() => void save()}>
          {busy ? "Saving…" : "Save"}
        </Button>
        <Button variant="secondary" onClick={() => window.close()}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

function Saved({ proposal, already }: { proposal: Proposal; already: boolean }) {
  const review = `/catalog/products/review/${proposal.id}`;
  return (
    <Alert tone="success">
      {already ? "Already saved · " : "Saved · "}
      <a href={review} target="_blank" rel="noreferrer" className={`rounded font-medium underline ${focusRing}`}>
        {already ? "Open it" : "Review it in Needs you"}
      </a>
    </Alert>
  );
}
