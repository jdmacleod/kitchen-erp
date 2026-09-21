import { useState } from "react";
import { validateOpeningHours } from "../../api/geo";
import { errorMessage } from "../../api/client";
import {
  DAY_CODES,
  DAY_SPANS,
  MONTH_CODES,
  describeOpeningHours,
  presetTimesComplete,
  presetToString,
  type DayCode,
  type DaySpan,
  type MonthCode,
  type Preset,
} from "../../lib/openingHours";
import { Field } from "../ui";
import { RadioGroup, SelectField, hintClass, inputClass, labelClass } from "../catalog/fields";

type Mode = "none" | "daily" | "weekly" | "seasonal" | "raw";

const MODES: { value: Mode; label: string }[] = [
  { value: "none", label: "Not set" },
  { value: "daily", label: "Daily hours" },
  { value: "weekly", label: "Weekly market" },
  { value: "seasonal", label: "Seasonal" },
  { value: "raw", label: "Raw" },
];

interface OpeningHoursInputProps {
  idPrefix: string;
  /** The opening_hours string; "" means not set. */
  value: string;
  onChange: (value: string) => void;
  /** Called after each server validation, so a form can block submit on an invalid string. */
  onValidated?: (valid: boolean) => void;
  disabled?: boolean;
  /** Shown as the "Not set" hint, e.g. "A stall without hours inherits the market's." */
  noneHint?: string;
}

const defaultDaily: Preset = { kind: "daily", days: "Mo-Su", open: "08:00", close: "20:00" };
const defaultWeekly: Preset = { kind: "weekly", day: "Sa", open: "08:00", close: "13:00" };
const defaultSeasonal: Preset = { kind: "seasonal", from: "Apr", to: "Oct", day: "Sa", open: "08:00", close: "13:00" };

/**
 * Writes OpenStreetMap opening_hours syntax for the common cases and lets the
 * raw string through for anything else. The string is validated by the server
 * (POST /opening-hours/validate) when the user leaves the field.
 */
export function OpeningHoursInput({ idPrefix, value, onChange, onValidated, disabled, noneHint }: OpeningHoursInputProps) {
  const [mode, setMode] = useState<Mode>(value ? "raw" : "none");
  const [preset, setPreset] = useState<Preset>(defaultDaily);
  const [check, setCheck] = useState<{ text: string; valid: boolean; error: string | null } | "checking" | null>(null);

  const write = (next: Preset) => {
    setPreset(next);
    if (presetTimesComplete(next)) onChange(presetToString(next));
  };

  const switchMode = (next: Mode) => {
    setMode(next);
    setCheck(null);
    if (next === "none") onChange("");
    else if (next === "daily") write(preset.kind === "daily" ? preset : defaultDaily);
    else if (next === "weekly") write(preset.kind === "weekly" ? preset : defaultWeekly);
    else if (next === "seasonal") write(preset.kind === "seasonal" ? preset : defaultSeasonal);
    // "raw" keeps whatever string is there for hand editing.
  };

  const validate = async () => {
    const text = value.trim();
    if (!text) {
      setCheck(null);
      onValidated?.(true);
      return;
    }
    setCheck("checking");
    try {
      const result = await validateOpeningHours(text);
      setCheck({ text, valid: result.valid, error: result.error });
      onValidated?.(result.valid);
    } catch (e) {
      setCheck({ text, valid: false, error: errorMessage(e) });
      onValidated?.(false);
    }
  };

  const checkId = `${idPrefix}-hours-check`;
  const previewId = `${idPrefix}-hours-preview`;

  return (
    <fieldset className="flex flex-col gap-3" onBlur={(e) => {
      // Validate when focus leaves the whole builder, not on every field hop.
      if (!e.currentTarget.contains(e.relatedTarget as Node | null)) void validate();
    }}>
      <legend className={labelClass}>Opening hours</legend>
      <RadioGroup name={`${idPrefix}-hours-mode`} legend="How to enter hours" options={MODES} value={mode} onChange={switchMode} disabled={disabled} />

      {mode === "none" && noneHint ? <p className={hintClass}>{noneHint}</p> : null}

      {mode === "daily" && preset.kind === "daily" ? (
        <div className="grid gap-3 sm:grid-cols-3">
          <SelectField
            id={`${idPrefix}-hours-days`}
            label="Days"
            value={preset.days}
            disabled={disabled}
            onChange={(e) => write({ ...preset, days: e.target.value as DaySpan })}
          >
            {DAY_SPANS.map((s) => (
              <option key={s.value} value={s.value}>
                {s.label}
              </option>
            ))}
          </SelectField>
          <TimeField id={`${idPrefix}-hours-open`} label="Opens" value={preset.open} disabled={disabled} onChange={(v) => write({ ...preset, open: v })} />
          <TimeField id={`${idPrefix}-hours-close`} label="Closes" value={preset.close} disabled={disabled} onChange={(v) => write({ ...preset, close: v })} />
        </div>
      ) : null}

      {mode === "weekly" && preset.kind === "weekly" ? (
        <div className="grid gap-3 sm:grid-cols-3">
          <DaySelect id={`${idPrefix}-hours-day`} value={preset.day} disabled={disabled} onChange={(day) => write({ ...preset, day })} />
          <TimeField id={`${idPrefix}-hours-open`} label="Opens" value={preset.open} disabled={disabled} onChange={(v) => write({ ...preset, open: v })} />
          <TimeField id={`${idPrefix}-hours-close`} label="Closes" value={preset.close} disabled={disabled} onChange={(v) => write({ ...preset, close: v })} />
        </div>
      ) : null}

      {mode === "seasonal" && preset.kind === "seasonal" ? (
        <div className="grid gap-3 sm:grid-cols-3">
          <MonthSelect id={`${idPrefix}-hours-from`} label="From" value={preset.from} disabled={disabled} onChange={(from) => write({ ...preset, from })} />
          <MonthSelect id={`${idPrefix}-hours-to`} label="To" value={preset.to} disabled={disabled} onChange={(to) => write({ ...preset, to })} />
          <DaySelect id={`${idPrefix}-hours-day`} value={preset.day} disabled={disabled} onChange={(day) => write({ ...preset, day })} />
          <TimeField id={`${idPrefix}-hours-open`} label="Opens" value={preset.open} disabled={disabled} onChange={(v) => write({ ...preset, open: v })} />
          <TimeField id={`${idPrefix}-hours-close`} label="Closes" value={preset.close} disabled={disabled} onChange={(v) => write({ ...preset, close: v })} />
        </div>
      ) : null}

      {mode === "raw" ? (
        <div className="flex flex-col gap-1">
          <label htmlFor={`${idPrefix}-hours-raw`} className={labelClass}>
            opening_hours
          </label>
          <textarea
            id={`${idPrefix}-hours-raw`}
            rows={2}
            spellCheck={false}
            disabled={disabled}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            aria-describedby={`${previewId} ${checkId}`}
            className={`font-mono text-sm ${inputClass}`}
            placeholder="Mo-Fr 08:00-21:00; Sa,Su 09:00-20:00"
          />
          <p className={hintClass}>OpenStreetMap syntax. Seasons, exceptions, and holidays all fit here.</p>
        </div>
      ) : null}

      {mode !== "none" ? (
        <p id={previewId} className="text-sm">
          <span className="text-neutral-600 dark:text-neutral-400">Writes </span>
          <code data-testid={`${idPrefix}-hours-string`} className="rounded bg-neutral-100 px-1 py-0.5 text-xs dark:bg-neutral-800">
            {value || "(nothing yet)"}
          </code>
          {value ? <span className="text-neutral-600 dark:text-neutral-400"> — {describeOpeningHours(value)}</span> : null}
        </p>
      ) : null}

      <p id={checkId} role="status" className={`text-xs ${check !== null && check !== "checking" && !check.valid ? "text-red-700 dark:text-red-300" : hintClass}`}>
        {check === "checking"
          ? "Checking…"
          : check === null
            ? ""
            : check.valid
              ? "Valid opening hours."
              : (check.error ?? "Invalid opening hours.")}
      </p>
    </fieldset>
  );
}

function TimeField({ id, label, value, onChange, disabled }: { id: string; label: string; value: string; onChange: (v: string) => void; disabled?: boolean }) {
  return <Field id={id} label={label} type="time" value={value} disabled={disabled} required onChange={(e) => onChange(e.target.value)} />;
}

function DaySelect({ id, value, onChange, disabled }: { id: string; value: DayCode; onChange: (v: DayCode) => void; disabled?: boolean }) {
  return (
    <SelectField id={id} label="Day" value={value} disabled={disabled} onChange={(e) => onChange(e.target.value as DayCode)}>
      {DAY_CODES.map((d) => (
        <option key={d} value={d}>
          {d}
        </option>
      ))}
    </SelectField>
  );
}

function MonthSelect({ id, label, value, onChange, disabled }: { id: string; label: string; value: MonthCode; onChange: (v: MonthCode) => void; disabled?: boolean }) {
  return (
    <SelectField id={id} label={label} value={value} disabled={disabled} onChange={(e) => onChange(e.target.value as MonthCode)}>
      {MONTH_CODES.map((m) => (
        <option key={m} value={m}>
          {m}
        </option>
      ))}
    </SelectField>
  );
}
