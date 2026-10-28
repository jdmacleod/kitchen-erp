import { PRODUCE_GRADES, type ProductAttributes } from "../api/productDetails";

/**
 * The details a product's category lets it record (03, 1H amendment 2026-10-27): an
 * ingredient is a commodity, and a product says what tells its items apart. Produce has
 * a variety, organic and a size grade; meat and seafood their species and cut facts;
 * every other food organic alone. The server validates; these only lay out the form.
 */

interface Option {
  value: string;
  label: string;
}

export type AttributeField =
  | { key: string; label: string; type: "checkbox" }
  | { key: string; label: string; type: "text"; hint?: string; maxLength: number }
  | { key: string; label: string; type: "number"; hint?: string; min: number; max: number }
  | { key: string; label: string; type: "select"; hint?: string; options: Option[] };

const opts = (pairs: [string, string][]): Option[] =>
  pairs.map(([value, label]) => ({ value, label }));

/** Options whose label is the value in sentence case: "sea bass" is "Sea bass". */
const named = (values: string): Option[] =>
  values.split(",").map((value) => ({ value, label: value[0].toUpperCase() + value.slice(1) }));

const MEAT_SPECIES = named("beef,pork,chicken,turkey,lamb,goat,duck,other");

// Market species, after the FDA Seafood List (backend catalog/attributes.py).
const SEAFOOD_SPECIES = named(
  "salmon,tuna,shrimp,cod,halibut,branzino,sea bass,snapper,trout,tilapia,catfish," +
    "mackerel,sardine,anchovy,swordfish,mahi mahi,pollock,haddock,sole,flounder,scallop," +
    "crab,lobster,clam,mussel,oyster,squid,octopus,other",
);

const ORGANIC: AttributeField = { key: "organic", label: "Organic", type: "checkbox" };

export const ATTRIBUTE_FIELDS: Record<string, AttributeField[]> = {
  produce: [
    ORGANIC,
    {
      key: "variety",
      label: "Variety",
      type: "text",
      maxLength: 40,
      hint: "The cultivar, as the store names it: Honeycrisp, Yukon Gold.",
    },
    {
      key: "grade",
      label: "Size grade",
      type: "select",
      options: PRODUCE_GRADES,
      hint: "Only for produce sold by the piece.",
    },
  ],
  meat: [
    { key: "species", label: "Species", type: "select", options: MEAT_SPECIES },
    { key: "cut", label: "Cut", type: "text", maxLength: 60, hint: "Thigh, shoulder, ribeye." },
    {
      key: "bone",
      label: "Bone",
      type: "select",
      options: opts([
        ["bone_in", "Bone-in"],
        ["boneless", "Boneless"],
      ]),
    },
    {
      key: "skin",
      label: "Skin",
      type: "select",
      options: opts([
        ["skin_on", "Skin-on"],
        ["skinless", "Skinless"],
      ]),
    },
    {
      key: "form",
      label: "Form",
      type: "select",
      options: opts([
        ["whole", "Whole"],
        ["sliced", "Sliced"],
        ["cubed", "Cubed"],
        ["ground", "Ground"],
      ]),
    },
    {
      key: "grade",
      label: "Grade",
      type: "text",
      maxLength: 40,
      hint: "USDA Prime, Choice or Select.",
    },
    {
      key: "lean_pct",
      label: "Lean %",
      type: "number",
      min: 50,
      max: 99,
      hint: "80 for 80/20.",
    },
    ORGANIC,
  ],
  seafood: [
    { key: "species", label: "Species", type: "select", options: SEAFOOD_SPECIES },
    {
      key: "species_detail",
      label: "Market name",
      type: "text",
      maxLength: 40,
      hint: "Atlantic, sockeye, yellowfin.",
    },
    {
      key: "origin",
      label: "Origin",
      type: "select",
      options: opts([
        ["wild", "Wild"],
        ["farmed", "Farmed"],
      ]),
    },
    {
      key: "count_size",
      label: "Count size",
      type: "text",
      maxLength: 8,
      hint: "Pieces per pound: 26/30, U15.",
    },
    {
      key: "form",
      label: "Form",
      type: "select",
      options: named("whole,fillet,loin,steak,sliced,peeled,smoked,canned"),
    },
    ORGANIC,
  ],
};

/** The fields a category's products take: organic alone for foods without their own. */
export function attributeFields(categoryKey: string | null | undefined): AttributeField[] {
  return ATTRIBUTE_FIELDS[categoryKey ?? ""] ?? [ORGANIC];
}

/** The form's working copy: every stored attribute, so ones it doesn't show are kept. */
export type AttributeValues = Record<string, unknown>;

/** The values a category's fields show, for a product moving to that category. */
export function keepFields(
  categoryKey: string | null | undefined,
  values: AttributeValues,
): AttributeValues {
  const keys = new Set(attributeFields(categoryKey).map((f) => f.key));
  return Object.fromEntries(Object.entries(values).filter(([key]) => keys.has(key)));
}

/**
 * What to send: the working copy with empty fields dropped and Lean % as a number.
 * Keys the category's fields don't show are kept as they were.
 */
export function attributesToSend(values: AttributeValues): ProductAttributes {
  const out: ProductAttributes = {};
  for (const [key, value] of Object.entries(values)) {
    if (value === "" || value === null || value === undefined || value === false) continue;
    out[key] = key === "lean_pct" ? Number(value) : value;
  }
  return out;
}

/** Whether two attribute sets say the same thing (order aside). */
export function sameAttributes(a: ProductAttributes, b: ProductAttributes): boolean {
  const ka = Object.keys(a).sort();
  const kb = Object.keys(b).sort();
  return (
    ka.length === kb.length && ka.every((k, i) => k === kb[i] && String(a[k]) === String(b[k]))
  );
}

/** What is wrong with the details before they are sent, or null. */
export function attributeProblem(
  categoryKey: string | null | undefined,
  values: AttributeValues,
): string | null {
  const set = attributesToSend(values);
  if ("lean_pct" in set) {
    const lean = set.lean_pct;
    if (typeof lean !== "number" || !Number.isInteger(lean) || lean < 50 || lean > 99)
      return "Lean % is a whole number from 50 to 99.";
  }
  const detailed = Object.keys(set).some((k) => k !== "organic");
  if ((categoryKey === "meat" || categoryKey === "seafood") && detailed && !set.species)
    return "Choose the species before its other details.";
  return null;
}
