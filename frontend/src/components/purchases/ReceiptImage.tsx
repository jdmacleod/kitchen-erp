import { useState } from "react";
import { receiptImageUrl } from "../../api/ingest";
import { focusRing } from "../ui";

interface ReceiptImageProps {
  documentId: string;
  alt: string;
  /** A thumbnail this many pixels wide; the full image when omitted. */
  width?: number;
  className?: string;
  /** Make the image a link that opens the full receipt; the fallback is never a link. */
  link?: { label: string };
}

/**
 * The stored receipt, or a sentence saying it could not be shown. A failed load
 * is never left as an empty panel beside lines someone is checking against it.
 */
export function ReceiptImage({ documentId, alt, width, className = "", link }: ReceiptImageProps) {
  // Remembered per image, so a new receipt gets its own attempt; `attempt`
  // remounts the <img> for Try again (an error response is not cached).
  const [failedFor, setFailedFor] = useState<string | null>(null);
  const [attempt, setAttempt] = useState(0);
  if (failedFor === documentId) {
    return (
      <div role="status" className={`flex flex-col items-start gap-1 rounded-md border border-dashed border-neutral-300 p-2 text-sm text-neutral-600 dark:border-neutral-700 dark:text-neutral-400 ${className}`}>
        <span>{width ? "No preview" : "The receipt image couldn't be shown. The lines were still read from it."}</span>
        <button
          type="button"
          onClick={() => {
            setFailedFor(null);
            setAttempt((n) => n + 1);
          }}
          className={`inline-flex min-h-11 items-center rounded-md px-1 text-xs font-medium text-blue-700 underline lg:min-h-6 dark:text-blue-300 ${focusRing}`}
        >
          Try again
        </button>
      </div>
    );
  }
  const img = <img key={attempt} src={receiptImageUrl(documentId, width)} alt={alt} onError={() => setFailedFor(documentId)} className={className} />;
  if (!link) return img;
  return (
    <a href={receiptImageUrl(documentId)} target="_blank" rel="noopener" aria-label={link.label} className={`shrink-0 rounded-md ${focusRing}`}>
      {img}
    </a>
  );
}
