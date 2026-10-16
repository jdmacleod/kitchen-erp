import { productTitle } from "../../api/catalog";
import type { Observation } from "../../api/purchases";

/** What a price that could not be normalized is missing, in one sentence, and where to add it. */
export function bridgeFix(
  observation: Observation,
): { reason: string; label: string; to: string } | null {
  const { product, norm } = observation;
  if (!norm || norm.status === "ok") return null;
  const ingredient = `/catalog/ingredients/${product.ingredient.id}`;
  switch (norm.status) {
    case "no_density":
      return {
        reason: `It can't be compared yet: ${product.ingredient.name} has no density.`,
        label: "Add a density",
        to: `${ingredient}#density-heading`,
      };
    case "unknown_measure":
      return {
        reason: `It can't be compared yet: ${product.ingredient.name} has no measure named “${observation.unit}”.`,
        label: "Add the measure",
        to: `${ingredient}#measures-heading`,
      };
    case "no_pack":
      return {
        reason: `It can't be compared yet: ${productTitle(product)} has no pack size.`,
        label: "Set the pack",
        to: `/catalog/products/${product.id}`,
      };
    case "no_bunch_weight":
      // Per bunch until a bunch is weighed (issue 371).
      return {
        reason: `It compares only per bunch until one bunch of ${product.ingredient.name} is weighed.`,
        label: "Weigh one bunch",
        to: "/catalog/bridges#products",
      };
    default:
      return {
        reason: "It can't be compared yet: the quantity could not be used.",
        label: "Check the product",
        to: `/catalog/products/${product.id}`,
      };
  }
}
