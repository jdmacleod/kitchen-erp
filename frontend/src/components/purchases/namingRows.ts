import { isPositiveDecimal } from "../../api/catalog";
import type { IngredientMatch } from "../../api/catalog";
import type { NameChange, NamingRow } from "../../api/naming";
import { stripZeros } from "../../lib/decimal";
import { choiceFromMatch, needsCategory, type IngredientChoice } from "../catalog/IngredientPicker";
import { nameFromWords } from "./NameChanges";

/** A product that already has the row's name and ingredient (issue 179). */
export interface ExistingProduct {
  id: string;
  name: string;
  brand: string | null;
}

/** What a person has changed on a row; anything absent is still the suggestion. */
export interface RowEdit {
  name?: string;
  ingredient?: IngredientChoice | null;
  packQty?: string;
  packUnit?: string;
  ticked?: boolean;
  /** Identify the lines as this existing product instead of creating one. */
  use?: ExistingProduct | null;
  /** Create a new product even though one has the same name and ingredient. */
  duplicate?: boolean;
  /** Changed words the person put back as printed (issue 384). */
  undone?: number[];
  brand?: string;
}

export interface RowValue {
  name: string;
  ingredient: IngredientChoice | null;
  packQty: string;
  packUnit: string;
  ticked: boolean;
  use: ExistingProduct | null;
  duplicate: boolean;
  /** Which of the fields shown came from the model. */
  fromModel: { name: boolean; ingredient: boolean };
  /** The model's name, offered beside one the wording already read (issue 387). */
  modelOffer: string | null;
  /** The product's brand: typed, else the one the wording starts with (issue 390). */
  brand: string;
  /** The changed words to show as toggles, while the name is still built from them. */
  changes: NameChange[];
  undone: ReadonlySet<number>;
}

export function rowKey(row: NamingRow): string {
  return `${row.vendor.id}:${row.raw_text_norm ?? ""}`;
}

/** The name shown: typed, else the model's, else the wording's with undone words put back. */
function nameOf(row: NamingRow, edit: RowEdit | undefined, modelName: string | null): string {
  if (edit?.name !== undefined) return edit.name;
  if (modelName !== null) return modelName;
  const undone = new Set(edit?.undone ?? []);
  return undone.size > 0 ? nameFromWords(row.name_words, row.name_changes, undone) : row.name;
}

/** The ingredient shown: chosen, else the wording's, else the model's. */
function ingredientOf(
  row: NamingRow,
  edit: RowEdit | undefined,
  modelIngredient: IngredientMatch | null,
): IngredientChoice | null {
  if (edit?.ingredient !== undefined) return edit.ingredient;
  const match = row.ingredient ?? modelIngredient;
  return match ? choiceFromMatch(match) : null;
}

/** Whether the model's name fills the Name or is only offered beside it (issue 387). */
function modelName(row: NamingRow, fresh: string | null) {
  const wordingRead = row.name_changes.length > 0 || row.brand !== null;
  if (!wordingRead) return { name: fresh, offer: null };
  return { name: null, offer: fresh !== null && fresh !== row.name ? fresh : null };
}

/**
 * The model's answers for the fields a person hasn't edited and the wording left
 * open. A name the wording already read (issues 384, 390) isn't replaced: the model's
 * is offered beside it instead (issue 387).
 */
function modelAnswers(row: NamingRow, edit: RowEdit | undefined) {
  const model = row.model?.status === "done" ? row.model : null;
  const fresh = edit?.name === undefined && model?.name ? model.name : null;
  const { name, offer } = modelName(row, fresh);
  return {
    name,
    offer,
    ingredient:
      edit?.ingredient === undefined && !row.ingredient && model?.ingredient
        ? model.ingredient
        : null,
  };
}

/** The fields the wording or a person sets directly. */
function plainFields(row: NamingRow, edit: RowEdit | undefined) {
  return {
    packQty: edit?.packQty ?? (row.pack_qty ? stripZeros(row.pack_qty, 0) : ""),
    packUnit: edit?.packUnit ?? row.pack_unit ?? "",
    ticked: edit?.ticked ?? false,
    use: edit?.use ?? null,
    duplicate: edit?.duplicate ?? false,
    undone: new Set(edit?.undone ?? []),
  };
}

/**
 * A row as shown: what a person typed, else the model's answer for a field the
 * wording couldn't fill well, else the wording's suggestion. The model never
 * replaces a field a person edited (criterion 65).
 */
export function valueOf(row: NamingRow, edit: RowEdit | undefined): RowValue {
  const model = modelAnswers(row, edit);
  const fromWords = edit?.name === undefined && model.name === null;
  return {
    ...plainFields(row, edit),
    name: nameOf(row, edit, model.name),
    brand: edit?.brand ?? row.brand ?? "",
    ingredient: ingredientOf(row, edit, model.ingredient),
    fromModel: { name: model.name !== null, ingredient: model.ingredient !== null },
    modelOffer: model.offer,
    changes: fromWords ? row.name_changes : [],
  };
}

/** The learned expansions a person undid on a row, to be offered less (issue 384). */
export function forgotten(row: NamingRow, v: RowValue) {
  return row.name_changes
    .filter((c) => c.source === "learned" && v.undone.has(c.index))
    .map((c) => ({ word: c.printed, expansion: c.shown }));
}

/** Why a ticked row can't be sent yet, or null. */
export function problemWith(v: RowValue): string | null {
  if (v.use) return null;
  if (!v.name.trim()) return "Give the product a name.";
  if (!v.ingredient) return "Choose an ingredient, or create one by name.";
  if (needsCategory(v.ingredient)) return "Choose a category for the new ingredient.";
  const hasQty = v.packQty.trim() !== "";
  if (hasQty !== (v.packUnit !== ""))
    return "Give both a pack quantity and a pack unit, or neither.";
  if (hasQty && !isPositiveDecimal(v.packQty.trim()))
    return "Pack quantity must be a positive number.";
  return null;
}
