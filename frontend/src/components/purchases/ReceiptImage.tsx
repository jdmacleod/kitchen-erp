import { useState } from "react";
import { receiptImageUrl } from "../../api/ingest";

interface ReceiptImageProps {
  documentId: string;
  alt: string;
  /** A thumbnail this many pixels wide; the full image when omitted. */
  width?: number;
  className?: string;
}

/**
 * The stored receipt, or a sentence saying it could not be shown. A failed load
 * is never left as an empty panel beside lines someone is checking against it.
 */
export function ReceiptImage({ documentId, alt, width, className = "" }: ReceiptImageProps) {
  // Remembered per image, so a new receipt gets its own attempt.
  const [failedFor, setFailedFor] = useState<string | null>(null);
  if (failedFor === documentId) {
    return (
      <p role="status" className={`rounded-md border border-dashed border-neutral-300 p-3 text-sm text-neutral-600 dark:border-neutral-700 dark:text-neutral-400 ${className}`}>
        {width ? "No preview" : "The receipt image couldn't be shown. The lines were still read from it."}
      </p>
    );
  }
  return <img src={receiptImageUrl(documentId, width)} alt={alt} onError={() => setFailedFor(documentId)} className={className} />;
}
