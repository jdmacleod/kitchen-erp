import { useUnits, type Unit } from "../api/catalog";

// Foods run from about 0.05 g/ml (dried leaves, popcorn) to about 2 g/ml
// (salt, honey); anything outside this band is a typo or an inverted entry.
const MIN_G_PER_ML = 0.05;
const MAX_G_PER_ML = 2.5;
// A label that starts with a mass unit ("oz, boneless") should weigh about that unit.
const MASS_TOLERANCE = 0.25;

/** The unit a measure label starts with: "tbsp juice" gives tbsp, "fl oz" gives fl oz. */
function leadingUnit(label: string, units: Unit[]): Unit | null {
  const words = label
    .toLowerCase()
    .split(/[\s,()]+/)
    .filter(Boolean);
  for (const n of [2, 1]) {
    const head = words.slice(0, n).join(" ");
    if (words.length < n || !head) continue;
    const unit = units.find((u) => u.code === head || u.aliases.includes(head));
    if (unit) return unit;
  }
  return null;
}

/**
 * Why a named measure looks wrong, or null. Only measures whose label starts
 * with a volume or mass unit can be checked: "1 tbsp juice = 0.33 g" would be
 * 0.02 g/ml, lighter than any food, and usually means "tbsp per lemon" was
 * typed where "grams per tbsp" was asked for. Advisory only; the value saves.
 */
export function measureLooksOff(
  label: string,
  qty: string,
  canonicalUnit: string,
  units: Unit[],
): string | null {
  const grams = Number(qty);
  const canonical = units.find((u) => u.code === canonicalUnit);
  if (canonical?.dimension !== "mass" || !(grams > 0)) return null;
  const unit = leadingUnit(label, units);
  if (!unit || unit === canonical) return null;
  const inGrams = grams * Number(canonical.to_base_factor);
  if (unit.dimension === "volume") {
    const density = inGrams / Number(unit.to_base_factor);
    if (density >= MIN_G_PER_ML && density <= MAX_G_PER_ML) return null;
    return (
      `One ${label} at ${qty} ${canonicalUnit} would be ${density.toPrecision(2)} g/ml, ` +
      `${density < MIN_G_PER_ML ? "lighter" : "heavier"} than any food. ` +
      `Check that ${qty} ${canonicalUnit} is what one ${label} weighs.`
    );
  }
  if (unit.dimension === "mass") {
    const expected = Number(unit.to_base_factor);
    if (Math.abs(inGrams - expected) <= expected * MASS_TOLERANCE) return null;
    return `One ${label} at ${qty} ${canonicalUnit} doesn't match 1 ${unit.code} (${Number(expected.toPrecision(3))} g).`;
  }
  return null;
}

/** `measureLooksOff` for one ingredient's unit, with the unit table loaded. */
export function useMeasureCheck(canonicalUnit: string) {
  const units = useUnits().data ?? [];
  return (label: string, qty: string) => measureLooksOff(label, qty, canonicalUnit, units);
}
