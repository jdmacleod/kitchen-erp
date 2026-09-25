import { useState } from "react";
import { errorMessage } from "../../api/client";
import { VENDOR_KINDS, useVendors, vendorKindLabel, type VendorKind, type VendorRef } from "../../api/geo";
import { useDebouncedValue } from "../../lib/useDebouncedValue";
import { Button } from "../ui";
import { Combobox } from "../catalog/Combobox";
import { Badge, SelectField, labelClass } from "../catalog/fields";

/** Either an existing vendor or one to create in the same request. */
export type VendorChoice = { kind: "existing"; vendor: VendorRef } | { kind: "new"; name: string; vendorKind: VendorKind };

type Option = { kind: "existing"; vendor: VendorRef } | { kind: "new"; name: string };

interface VendorPickerProps {
  id: string;
  label?: string;
  value: VendorChoice | null;
  onChange: (choice: VendorChoice | null) => void;
  allowCreate?: boolean;
  disabled?: boolean;
}

/**
 * Pick a vendor by typing part of its name (GET /vendors?q=), or create one
 * inline; a new vendor needs a kind, chosen right here.
 */
export function VendorPicker({ id, label = "Vendor", value, onChange, allowCreate = true, disabled }: VendorPickerProps) {
  const [text, setText] = useState("");
  const debounced = useDebouncedValue(text, 200);
  const trimmed = text.trim();
  const vendors = useVendors(debounced, false, debounced.trim().length > 0);
  const found = trimmed ? (vendors.data ?? []) : [];
  const settled = debounced === text && !vendors.isFetching;

  const options: Option[] = found.map((v) => ({
    kind: "existing",
    vendor: { id: v.id, name: v.name, kind: v.kind, price_scope: v.price_scope },
  }));
  const exact = found.some((v) => v.name.toLowerCase() === trimmed.toLowerCase());
  if (allowCreate && trimmed && settled && !exact) options.push({ kind: "new", name: trimmed });

  let status: string | undefined;
  if (trimmed) {
    if (vendors.isError) status = errorMessage(vendors.error);
    else if (!settled) status = "Searching…";
    else if (found.length === 0 && !allowCreate) status = "No vendors match.";
  }

  if (value) {
    return (
      <div className="flex flex-col gap-2">
        <span className={labelClass} id={`${id}-label`}>
          {label}
        </span>
        <div
          role="group"
          aria-labelledby={`${id}-label`}
          className="flex min-h-11 lg:min-h-10 flex-wrap items-center justify-between gap-2 rounded-md border border-neutral-300 bg-neutral-50 px-3 py-1 text-sm dark:border-neutral-700 dark:bg-neutral-900"
        >
          <span data-testid={`${id}-choice`}>
            {value.kind === "existing" ? (
              <>
                <span className="font-medium">{value.vendor.name}</span>
                <span className="text-neutral-600 dark:text-neutral-400"> · {vendorKindLabel[value.vendor.kind]}</span>
              </>
            ) : (
              <>
                <span className="font-medium">{value.name}</span> <Badge>new vendor</Badge>
              </>
            )}
          </span>
          <Button variant="ghost" className="min-h-11 lg:min-h-8 px-2" disabled={disabled} onClick={() => { setText(""); onChange(null); }}>
            Change
          </Button>
        </div>
        {value.kind === "new" ? (
          <SelectField
            id={`${id}-new-kind`}
            label="Vendor kind"
            value={value.vendorKind}
            disabled={disabled}
            onChange={(e) => onChange({ ...value, vendorKind: e.target.value as VendorKind })}
          >
            {VENDOR_KINDS.map((k) => (
              <option key={k} value={k}>
                {vendorKindLabel[k]}
              </option>
            ))}
          </SelectField>
        ) : null}
      </div>
    );
  }

  return (
    <Combobox<Option>
      id={id}
      label={label}
      placeholder="Type to search vendors"
      listLabel="Vendors"
      inputValue={text}
      onInputChange={setText}
      items={options}
      getKey={(o) => (o.kind === "existing" ? o.vendor.id : "__new__")}
      status={status}
      disabled={disabled}
      onSelect={(o) => {
        setText("");
        onChange(o.kind === "existing" ? o : { kind: "new", name: o.name, vendorKind: "stand" });
      }}
      renderItem={(o) =>
        o.kind === "existing" ? (
          <span>
            <span className="font-medium">{o.vendor.name}</span>
            <span className="text-neutral-600 dark:text-neutral-400"> · {vendorKindLabel[o.vendor.kind]}</span>
          </span>
        ) : (
          <span>
            Create vendor <span className="font-medium">“{o.name}”</span>
          </span>
        )
      }
    />
  );
}
