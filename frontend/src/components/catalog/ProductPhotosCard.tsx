import { useId, useRef, useState, type ReactNode } from "react";
import { errorMessage } from "../../api/client";
import { productTitle, type Product } from "../../api/catalog";
import {
  MAX_PHOTOS,
  ROLE_LABELS,
  SOURCE_LABELS,
  photoErrorMessage,
  useAddProductPhotos,
  usePhotoAction,
  useProductPhotos,
  type PhotoAction,
  type PhotoRole,
  type ProductPhoto,
} from "../../api/productPhotos";
import { useNotice } from "../Notice";
import { SegmentedControl } from "../SegmentedControl";
import { Alert, Button, Card, focusRing } from "../ui";
import { Badge } from "./fields";
import { ProductThumb } from "./ProductThumb";

const muted = "text-neutral-600 dark:text-neutral-400";
const LABEL_ROLES: PhotoRole[] = ["label_front", "label_nutrition", "label_ingredients", "shelf_tag"];
const ROLE_OPTIONS = Object.entries(ROLE_LABELS) as [PhotoRole, string][];

/**
 * The product page's Photos card (10, PD4): the main photo at 480, the other
 * photos as tiles with "Use as main photo" and "Hide", "Show hidden (n)", and
 * "Add photo" for up to four at a time, each with a role. Label photos are
 * listed in the Labels card instead and are never the main photo.
 */
export function ProductPhotosCard({ product }: { product: Product }) {
  const photos = useProductPhotos(product.id);
  const action = usePhotoAction(product.id);
  const [showHidden, setShowHidden] = useState(false);
  const [adding, setAdding] = useState(false);
  const title = productTitle(product);

  const items = (photos.data?.items ?? []).filter((p) => p.role === "product");
  const main = items.find((p) => p.is_main) ?? null;
  const hidden = items.filter((p) => p.status === "hidden");
  const others = items.filter((p) => p !== main && p.status !== "hidden");
  const act = (id: string, a: PhotoAction) => action.mutate({ id, action: a });

  return (
    <Card>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
        <h2 className="text-lg font-medium">Photos</h2>
        <div className="flex flex-wrap items-center gap-3">
          {hidden.length > 0 ? (
            <label className="inline-flex min-h-11 items-center gap-2 text-sm lg:min-h-9">
              <input type="checkbox" checked={showHidden} onChange={(e) => setShowHidden(e.target.checked)} className={`size-4 ${focusRing}`} />
              Show hidden ({hidden.length})
            </label>
          ) : null}
          {adding ? null : (
            <Button variant="secondary" onClick={() => setAdding(true)}>
              Add photo
            </Button>
          )}
        </div>
      </div>

      {adding ? <AddPhotos productId={product.id} onDone={() => setAdding(false)} /> : null}

      {photos.isPending ? (
        <p role="status" className={`text-sm ${muted}`}>
          Loading…
        </p>
      ) : photos.isError ? (
        <Alert tone="error">{errorMessage(photos.error)}</Alert>
      ) : (
        <div className="flex flex-col gap-4 md:flex-row md:items-start">
          {main ? (
            <MainPhoto photo={main} title={title} onAction={act} busy={action.isPending} />
          ) : (
            <div className="flex items-center gap-3">
              <ProductThumb name={product.name} photo={null} categoryKey={product.ingredient.category_key} size={96} />
              <p className={`text-sm ${muted}`}>{others.length > 0 ? "No photo can be the main photo yet." : "No photo yet."}</p>
            </div>
          )}
          {others.length > 0 || (showHidden && hidden.length > 0) ? (
            <ul aria-label="Other photos" className="grid flex-1 grid-cols-2 gap-3 sm:grid-cols-3">
              {others.map((p) => (
                <PhotoTile key={p.id} photo={p} title={title} onAction={act} busy={action.isPending} />
              ))}
              {showHidden
                ? hidden.map((p) => <PhotoTile key={p.id} photo={p} title={title} onAction={act} busy={action.isPending} />)
                : null}
            </ul>
          ) : null}
        </div>
      )}
      {action.isError ? (
        <Alert tone="error" className="mt-3">
          {errorMessage(action.error)}
        </Alert>
      ) : null}
    </Card>
  );
}

function MainPhoto({
  photo,
  title,
  onAction,
  busy,
}: {
  photo: ProductPhoto;
  title: string;
  onAction: (id: string, a: PhotoAction) => void;
  busy: boolean;
}) {
  const [view, setView] = useState<"cutout" | "original">("cutout");
  const urls = photo.urls;
  const cutout = view === "cutout" && urls?.cutout_medium ? urls.cutout_medium : null;
  return (
    <figure data-testid="main-photo" className="flex w-full max-w-[20rem] flex-none flex-col gap-2">
      {cutout ? (
        <img src={cutout} alt={`Photo of ${title}`} className="aspect-square w-full object-contain cutout-shadow" />
      ) : urls ? (
        <img src={urls.medium} alt={`Photo of ${title}`} className="aspect-square w-full rounded-md bg-neutral-100 object-contain dark:bg-neutral-800" />
      ) : null}
      <figcaption className="flex flex-col gap-2 text-sm">
        <span className="flex flex-wrap items-center gap-2">
          <span className="font-medium">Main photo</span>
          <Badge>{SOURCE_LABELS[photo.source_kind]}</Badge>
          {photo.is_stock_suspect ? <Badge tone="warn">May be a stock photo</Badge> : null}
        </span>
        {photo.attribution ? <span className={muted}>{photo.attribution}</span> : null}
        {photo.has_cutout ? (
          <SegmentedControl
            label="Show the photo as"
            value={view}
            onChange={setView}
            options={[
              { value: "cutout", label: "Cutout" },
              { value: "original", label: "Original" },
            ]}
          />
        ) : null}
        <span className="flex flex-wrap gap-2">
          {photo.pinned ? (
            <Button variant="secondary" disabled={busy} onClick={() => onAction(photo.id, "unset-main")}>
              Let the app choose
            </Button>
          ) : null}
          <Button variant="secondary" disabled={busy} onClick={() => onAction(photo.id, "hide")}>
            Hide
          </Button>
        </span>
      </figcaption>
    </figure>
  );
}

function PhotoTile({
  photo,
  title,
  onAction,
  busy,
}: {
  photo: ProductPhoto;
  title: string;
  onAction: (id: string, a: PhotoAction) => void;
  busy: boolean;
}) {
  let picture: ReactNode;
  let actions: ReactNode = null;
  if (photo.status === "processing") {
    picture = <StateTile>Preparing photo…</StateTile>;
  } else if (photo.status === "failed") {
    picture = <StateTile>Couldn't process this photo</StateTile>;
    actions = (
      <Button variant="secondary" disabled={busy} onClick={() => onAction(photo.id, "retry")}>
        Retry
      </Button>
    );
  } else {
    const src = photo.has_cutout && photo.urls?.cutout_medium ? photo.urls.cutout_medium : photo.urls?.small;
    picture = (
      <img
        src={src}
        alt={`Photo of ${title}`}
        loading="lazy"
        className={`aspect-square w-full ${photo.has_cutout ? "object-contain cutout-shadow" : "rounded-md bg-neutral-100 object-cover dark:bg-neutral-800"} ${
          photo.status === "hidden" ? "opacity-60" : ""
        }`}
      />
    );
    actions =
      photo.status === "hidden" ? (
        <Button variant="secondary" disabled={busy} onClick={() => onAction(photo.id, "show")}>
          Show
        </Button>
      ) : (
        <>
          <Button variant="secondary" disabled={busy} onClick={() => onAction(photo.id, "use-as-main")}>
            Use as main photo
          </Button>
          <Button variant="secondary" disabled={busy} onClick={() => onAction(photo.id, "hide")}>
            Hide
          </Button>
        </>
      );
  }
  return (
    <li className="flex flex-col gap-2" data-testid="photo-tile">
      {picture}
      <span className="flex flex-wrap items-center gap-1">
        <Badge>{SOURCE_LABELS[photo.source_kind]}</Badge>
        {photo.status === "hidden" ? <Badge>Hidden</Badge> : null}
        {photo.is_stock_suspect ? <Badge tone="warn">May be a stock photo</Badge> : null}
      </span>
      {photo.attribution ? <span className={`text-xs ${muted}`}>{photo.attribution}</span> : null}
      {actions ? <span className="flex flex-wrap gap-2">{actions}</span> : null}
    </li>
  );
}

function StateTile({ children }: { children: ReactNode }) {
  return (
    <span role="status" className={`flex aspect-square w-full items-center justify-center rounded-md bg-neutral-100 p-2 text-center text-sm dark:bg-neutral-800 ${muted}`}>
      {children}
    </span>
  );
}

interface Staged {
  key: string;
  file: File;
  role: PhotoRole;
}

/** Choose up to four photos, give each a role, and upload them together. */
function AddPhotos({ productId, onDone }: { productId: string; onDone: () => void }) {
  const add = useAddProductPhotos(productId);
  const notice = useNotice();
  const input = useRef<HTMLInputElement>(null);
  const [staged, setStaged] = useState<Staged[]>([]);
  const [tooMany, setTooMany] = useState(false);
  const id = useId();

  const choose = (files: FileList | null) => {
    if (!files) return;
    const chosen = Array.from(files);
    setTooMany(staged.length + chosen.length > MAX_PHOTOS);
    const next = [
      ...staged,
      ...chosen.map((file, i) => ({ key: `${file.name}-${file.size}-${Date.now()}-${i}`, file, role: "product" as PhotoRole })),
    ].slice(0, MAX_PHOTOS);
    setStaged(next);
    add.reset();
  };

  const upload = () =>
    add.mutate(
      { productId, photos: staged.map(({ file, role }) => ({ file, role })) },
      {
        onSuccess: () => {
          notice.show({
            tone: "success",
            message: staged.length === 1 ? "Photo saved. It'll be ready in a moment." : "Photos saved. They'll be ready in a moment.",
          });
          onDone();
        },
      },
    );

  return (
    <div className="mb-4 flex flex-col gap-3 rounded-md border border-neutral-200 p-3 dark:border-neutral-800">
      <input
        ref={input}
        id={`${id}-files`}
        type="file"
        accept="image/jpeg,image/png,image/webp,image/heic,.heic"
        multiple
        className="sr-only"
        onChange={(e) => {
          choose(e.target.files);
          e.target.value = "";
        }}
      />
      {staged.length === 0 ? (
        <p className={`text-sm ${muted}`}>Choose up to four photos. Give each one a role: the product itself, or one of its labels.</p>
      ) : (
        <ul aria-label="Photos to add" className="flex flex-col gap-2">
          {staged.map((s, i) => (
            <li key={s.key} className="flex flex-wrap items-center gap-2 text-sm">
              <span className="min-w-0 flex-1 truncate">{s.file.name}</span>
              <label className="sr-only" htmlFor={`${id}-role-${i}`}>
                Role for {s.file.name}
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
              <Button variant="secondary" onClick={() => setStaged(staged.filter((x) => x.key !== s.key))}>
                Remove
              </Button>
            </li>
          ))}
        </ul>
      )}
      {tooMany ? <Alert tone="info">Only four photos can be added at a time; the first four are kept.</Alert> : null}
      {add.isError ? <Alert tone="error">{photoErrorMessage(add.error)}</Alert> : null}
      <div className="flex flex-wrap gap-2">
        {staged.length < MAX_PHOTOS ? (
          <Button variant="secondary" onClick={() => input.current?.click()}>
            {staged.length === 0 ? "Choose photos" : "Add another photo"}
          </Button>
        ) : null}
        {staged.length > 0 ? (
          <Button disabled={add.isPending} onClick={upload}>
            {add.isPending ? "Uploading…" : staged.length === 1 ? "Upload photo" : `Upload ${staged.length} photos`}
          </Button>
        ) : null}
        <Button variant="secondary" disabled={add.isPending} onClick={onDone}>
          Cancel
        </Button>
      </div>
    </div>
  );
}

/** The Labels card (10, PD16): each label photo with the text read from it. */
export function ProductLabelsCard({ product }: { product: Product }) {
  const photos = useProductPhotos(product.id);
  const labels = (photos.data?.items ?? [])
    .filter((p) => LABEL_ROLES.includes(p.role) && p.status !== "hidden")
    .sort((a, b) => LABEL_ROLES.indexOf(a.role) - LABEL_ROLES.indexOf(b.role));
  if (labels.length === 0) return null;
  return (
    <Card>
      <h2 className="mb-3 text-lg font-medium">Labels</h2>
      <ul aria-label="Labels" className="flex flex-col divide-y divide-neutral-200 dark:divide-neutral-800">
        {labels.map((p) => (
          <li key={p.id} className="flex gap-3 py-3 first:pt-0 last:pb-0">
            {p.urls ? (
              <img
                src={p.urls.small}
                alt={`${ROLE_LABELS[p.role]} of ${productTitle(product)}`}
                loading="lazy"
                className="size-24 flex-none rounded-md bg-neutral-100 object-cover dark:bg-neutral-800"
              />
            ) : (
              <span className="size-24 flex-none">
                <StateTile>{p.status === "failed" ? "Couldn't process this photo" : "Preparing photo…"}</StateTile>
              </span>
            )}
            <div className="min-w-0 text-sm">
              <h3 className="font-medium">{ROLE_LABELS[p.role]}</h3>
              {p.ocr_text ? (
                <p className="mt-1 whitespace-pre-line">{p.ocr_text}</p>
              ) : (
                <p className={`mt-1 ${muted}`}>No text read from this label yet.</p>
              )}
            </div>
          </li>
        ))}
      </ul>
    </Card>
  );
}
