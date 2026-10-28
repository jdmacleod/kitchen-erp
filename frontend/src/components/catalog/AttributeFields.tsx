import { attributeFields, type AttributeValues } from "../../lib/productAttributes";
import { Field, focusRing } from "../ui";
import { SelectField } from "./fields";

/** A product's category details (03, 1H): variety for produce, species and cut facts for
 * meat and seafood, organic for every food. */
export function AttributeFields({
  idPrefix,
  categoryKey,
  values,
  onChange,
}: {
  idPrefix: string;
  categoryKey: string | null | undefined;
  values: AttributeValues;
  onChange: (next: AttributeValues) => void;
}) {
  const set = (key: string, value: unknown) => onChange({ ...values, [key]: value });
  const text = (key: string) => {
    const v = values[key];
    return typeof v === "string" || typeof v === "number" ? String(v) : "";
  };
  return (
    <fieldset className="grid items-start gap-4 sm:grid-cols-2">
      <legend className="sr-only">Details</legend>
      {attributeFields(categoryKey).map((field) => {
        const id = `${idPrefix}-attr-${field.key.replace(/_/g, "-")}`;
        if (field.type === "checkbox")
          return (
            <label
              key={field.key}
              className="inline-flex min-h-11 items-center gap-2 text-sm sm:mt-6 lg:min-h-10"
            >
              <input
                id={id}
                type="checkbox"
                checked={values[field.key] === true}
                onChange={(e) => set(field.key, e.target.checked)}
                className={`size-4 ${focusRing}`}
              />
              {field.label}
            </label>
          );
        if (field.type === "select")
          return (
            <SelectField
              key={field.key}
              id={id}
              label={field.label}
              hint={field.hint}
              value={text(field.key)}
              onChange={(e) => set(field.key, e.target.value)}
            >
              <option value="">None</option>
              {field.options.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </SelectField>
          );
        return (
          <Field
            key={field.key}
            id={id}
            label={field.label}
            hint={field.hint}
            autoComplete="off"
            inputMode={field.type === "number" ? "numeric" : undefined}
            maxLength={field.type === "text" ? field.maxLength : undefined}
            value={text(field.key)}
            onChange={(e) => set(field.key, e.target.value)}
          />
        );
      })}
    </fieldset>
  );
}
