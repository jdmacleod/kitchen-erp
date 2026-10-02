import { useId, useRef, useState } from "react";
import { MAX_PHOTOS, ROLE_LABELS, photoErrorMessage, type PhotoRole } from "../../api/productPhotos";
import { photographProduct } from "../../api/proposals";
import { inboxKey } from "../../api/inbox";
import { useQueryClient } from "@tanstack/react-query";
import { useNotice } from "../../components/Notice";
import { Alert, Button, Card, PageHeader, focusRing } from "../../components/ui";
import { usePageTitle } from "../../lib/usePageTitle";

const ROLE_OPTIONS = Object.entries(ROLE_LABELS) as [PhotoRole, string][];

interface Staged {
  key: string;
  file: File;
  role: PhotoRole;
}

/**
 * Capture → Photograph a product (09, 10; PD11, PD12): up to four photos of one
 * product, each with a role, sent as one proposal. Errors are inline and keep
 * the photos, so nothing has to be taken again.
 */
export function PhotographProductPage() {
  usePageTitle("Photograph a product");
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const notice = useNotice();
  const client = useQueryClient();
  const [staged, setStaged] = useState<Staged[]>([]);
  const [progress, setProgress] = useState<number | null>(null);
  const [error, setError] = useState<unknown>(null);

  const choose = (files: FileList | null) => {
    if (!files) return;
    const next = [...staged];
    for (const file of Array.from(files)) {
      if (next.length >= MAX_PHOTOS) break;
      // The first is the product itself; later ones are usually its labels.
      next.push({ key: `${file.name}-${file.size}-${next.length}-${Date.now()}`, file, role: next.length === 0 ? "product" : "label_front" });
    }
    setStaged(next);
    setError(null);
  };

  const send = async () => {
    setError(null);
    setProgress(0);
    try {
      await photographProduct(
        staged.map(({ file, role }) => ({ file, role })),
        (f) => setProgress(f),
      );
      setStaged([]);
      void client.invalidateQueries({ queryKey: inboxKey });
      notice.show({ tone: "success", message: "Photo saved. It'll appear in Needs you once it's identified." });
    } catch (e) {
      setError(e);
    } finally {
      setProgress(null);
    }
  };

  return (
    <>
      <PageHeader title="Photograph a product" description="A product that isn't in your catalog yet: its front, and its labels if you like." />
      <Card className="flex flex-col gap-4">
        <input
          ref={input}
          id={`${id}-files`}
          type="file"
          accept="image/*"
          capture="environment"
          multiple
          className="sr-only"
          onChange={(e) => {
            choose(e.target.files);
            e.target.value = "";
          }}
        />
        {staged.length === 0 ? (
          <p className="text-sm text-neutral-600 dark:text-neutral-400">Take a photo of the product's front. You can add up to three more, of its labels or shelf tag.</p>
        ) : (
          <ul aria-label="Photos to send" className="flex flex-col gap-3">
            {staged.map((s, i) => (
              <li key={s.key} className="flex flex-wrap items-center gap-2 text-sm">
                <img src={URL.createObjectURL(s.file)} alt="" className="size-14 flex-none rounded-md bg-neutral-100 object-cover dark:bg-neutral-800" />
                <span className="min-w-0 flex-1 truncate">{s.file.name}</span>
                <label className="sr-only" htmlFor={`${id}-role-${i}`}>
                  What photo {i + 1} shows
                </label>
                <select
                  id={`${id}-role-${i}`}
                  value={s.role}
                  onChange={(e) => setStaged(staged.map((x) => (x.key === s.key ? { ...x, role: e.target.value as PhotoRole } : x)))}
                  className={`min-h-11 rounded-md border border-neutral-300 bg-white px-2 text-sm lg:min-h-9 dark:border-neutral-700 dark:bg-neutral-900 ${focusRing}`}
                >
                  {ROLE_OPTIONS.map(([value, label]) => (
                    <option key={value} value={value}>
                      {label}
                    </option>
                  ))}
                </select>
                <Button variant="secondary" disabled={progress !== null} onClick={() => setStaged(staged.filter((x) => x.key !== s.key))}>
                  Remove
                </Button>
              </li>
            ))}
          </ul>
        )}
        {error ? <Alert tone="error">{photoErrorMessage(error)}</Alert> : null}
        {progress !== null ? (
          <div className="flex flex-col gap-1">
            <progress aria-label="Uploading photos" max={1} value={progress} className="h-2 w-full accent-neutral-700" />
            <span className="text-xs text-neutral-600 dark:text-neutral-400">Uploading… {Math.round(progress * 100)}%</span>
          </div>
        ) : null}
        <div className="flex flex-wrap gap-2">
          {staged.length < MAX_PHOTOS ? (
            <Button variant="secondary" disabled={progress !== null} onClick={() => input.current?.click()}>
              {staged.length === 0 ? "Take a photo" : "Add another photo of this product"}
            </Button>
          ) : null}
          {staged.length > 0 ? (
            <Button disabled={progress !== null} onClick={() => void send()}>
              {progress !== null ? "Sending…" : staged.length === 1 ? "Send photo" : `Send ${staged.length} photos`}
            </Button>
          ) : null}
        </div>
      </Card>
    </>
  );
}
