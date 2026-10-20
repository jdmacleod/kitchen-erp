import { Link } from "react-router";
import { useProduct } from "../../api/catalog";
import type { Proposal } from "../../api/proposals";
import { formatDate } from "../../lib/format";
import { Alert, focusRing } from "../ui";

/** " Identified n receipt lines." after accepting a page clipped for them (04, 2D). */
export function identifiedNote(proposal: Pick<Proposal, "result">): string {
  const n = proposal.result?.identified_lines ?? 0;
  return n > 0 ? ` Identified ${n} receipt ${n === 1 ? "line" : "lines"}.` : "";
}

/** A pending page clipped for waiting receipt lines says accepting identifies them. */
function ForLinesNotice({ proposal }: { proposal: Proposal }) {
  const lines = proposal.for_lines;
  if (!lines || lines.waiting === 0) return null;
  return (
    <Alert tone="info" className="mb-4">
      Clipped for <span className="font-mono">{lines.raw_text_norm}</span> at {lines.vendor_name}:
      accepting also identifies its {lines.waiting} waiting {lines.waiting === 1 ? "line" : "lines"}
      .
    </Alert>
  );
}

/** Where a proposal stands: pending (and what accepting it also does), superseded, decided. */
export function StatusNotice({ proposal }: { proposal: Proposal }) {
  const product = useProduct(
    proposal.status === "accepted" ? (proposal.product_id ?? undefined) : undefined,
  );
  if (proposal.status === "pending") return <ForLinesNotice proposal={proposal} />;
  const when = proposal.decided_at ? formatDate(proposal.decided_at) : "";
  if (proposal.status === "superseded") {
    const newer = proposal.result?.superseded_by;
    return (
      <Alert tone="info" className="mb-4">
        A newer capture replaced this
        {newer ? (
          <>
            {" · "}
            <Link
              to={`/catalog/products/review/${newer}`}
              className={`rounded-sm font-medium underline ${focusRing}`}
            >
              Open it
            </Link>
          </>
        ) : null}
      </Alert>
    );
  }
  if (proposal.status === "accepted") {
    return (
      <Alert tone="info" className="mb-4">
        Accepted {when}
        {identifiedNote(proposal)}
        {proposal.product_id ? (
          <>
            {" · "}
            <Link
              to={`/catalog/products/${proposal.product_id}`}
              className={`rounded-sm font-medium underline ${focusRing}`}
            >
              Open {product.data?.name ?? "the product"}
            </Link>
          </>
        ) : null}
      </Alert>
    );
  }
  return (
    <Alert tone="info" className="mb-4">
      Rejected {when}. The capture is kept.
    </Alert>
  );
}
