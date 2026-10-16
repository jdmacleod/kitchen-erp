import { asksSymbology, type BarcodeSymbology } from "../../lib/barcode";
import { RadioGroup } from "./fields";

const OPTIONS = [
  { value: "ean8", label: "EAN-8" },
  { value: "upce", label: "UPC-E" },
] as const;

export const SYMBOLOGY_REQUIRED = "Say whether the barcode is EAN-8 or UPC-E.";

interface BarcodeSymbologyPromptProps {
  name: string;
  /** The barcode as typed; the question shows only while it applies. */
  barcode: string;
  /** The product's saved barcode when editing: an unchanged code isn't asked about. */
  savedBarcode?: string | null;
  value: BarcodeSymbology | null;
  onChange: (value: BarcodeSymbology) => void;
  disabled?: boolean;
}

/**
 * The question an eight-digit code valid both as EAN-8 and as UPC-E needs
 * answered (spec 03, 1H): each reading is a different product, so the code is
 * never read silently. Rendered under the barcode field; nothing while it doesn't apply.
 */
export function BarcodeSymbologyPrompt({
  name,
  barcode,
  savedBarcode,
  value,
  onChange,
  disabled,
}: BarcodeSymbologyPromptProps) {
  if (!asksSymbology(barcode, savedBarcode)) return null;
  return (
    <RadioGroup<BarcodeSymbology | "">
      name={name}
      legend="Is this EAN-8 or UPC-E?"
      options={OPTIONS}
      value={value ?? ""}
      onChange={(v) => {
        if (v) onChange(v);
      }}
      disabled={disabled}
      hint="These digits are a valid code either way, for different products. EAN-8 prints its digits in two groups of four under the bars; UPC-E prints six under the bars and one more on each side."
    />
  );
}
