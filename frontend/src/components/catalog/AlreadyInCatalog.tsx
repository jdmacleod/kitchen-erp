import { formatPack } from "../../api/catalog";
import { isApiError } from "../../api/client";
import { Alert, Button } from "../ui";

/** A product the catalog already has, from a 409 product_exists (issue 348). */
export interface SameProduct {
  id: string;
  name: string;
  brand: string | null;
  pack_qty: string | null;
  pack_unit: string | null;
  verdict: "same" | "other_size" | "variant";
}

const VERDICT: Record<SameProduct["verdict"], string> = {
  same: "the same product",
  other_size: "another size",
  variant: "a variant",
};

/** The products a refused create names, or null when the error is something else. */
export function sameFromError(error: unknown): SameProduct[] | null {
  if (!isApiError(error) || error.code !== "product_exists") return null;
  const found = error.details?.candidates;
  return Array.isArray(found) ? (found as SameProduct[]) : [];
}

/** The error to show as an error: everything but "already in your catalog". */
export function otherError(error: unknown): unknown {
  return sameFromError(error) ? null : error;
}

/**
 * The notice for a create refused as "already in your catalog", or nothing: a
 * form passes its last error and what it sent.
 */
export function RefusedCreate<T extends object>({
  error,
  sent,
  busy,
  onUse,
  resend,
}: {
  error: unknown;
  sent: T | null;
  busy?: boolean;
  onUse: (product: SameProduct) => void;
  resend: (input: T & { allow_duplicate: true }) => void;
}) {
  const same = sameFromError(error);
  if (!same || !sent) return null;
  return (
    <AlreadyInCatalog
      products={same}
      busy={busy}
      onUse={onUse}
      onCreateAnyway={() => resend({ ...sent, allow_duplicate: true })}
    />
  );
}

/**
 * "Already in your catalog" (issue 348): the products a new one looks like, each
 * with "Use this one", and "Create anyway" for a product that really is new.
 */
export function AlreadyInCatalog({
  products,
  onUse,
  onCreateAnyway,
  busy,
}: {
  products: SameProduct[];
  onUse: (product: SameProduct) => void;
  onCreateAnyway: () => void;
  busy?: boolean;
}) {
  return (
    <Alert tone="info">
      <div className="flex flex-col gap-2" data-testid="already-in-catalog">
        <p className="font-medium">Already in your catalog</p>
        <ul className="flex flex-col gap-2">
          {products.map((p) => {
            const pack = formatPack(p.pack_qty, p.pack_unit);
            return (
              <li key={p.id} className="flex flex-wrap items-center gap-2">
                <span>
                  <span className="font-medium">{p.name}</span>
                  {p.brand ? ` · ${p.brand}` : ""}
                  {pack ? ` · ${pack}` : ""} ({VERDICT[p.verdict]})
                </span>
                <Button
                  variant="secondary"
                  className="min-h-11 lg:min-h-8 px-2"
                  disabled={busy}
                  onClick={() => onUse(p)}
                  aria-label={`Use ${p.name}`}
                >
                  Use this one
                </Button>
              </li>
            );
          })}
        </ul>
        <div>
          <Button variant="ghost" disabled={busy} onClick={onCreateAnyway}>
            Create anyway
          </Button>
        </div>
      </div>
    </Alert>
  );
}
