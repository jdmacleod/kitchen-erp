/** What a product says about itself beyond its name and pack: its store brand and its category details. */

/** A produce size grade; it sets products apart only when sold by the piece. */
export type ProduceGrade = "small" | "medium" | "large" | "extra_large" | "jumbo";
export const PRODUCE_GRADES: { value: ProduceGrade; label: string }[] = [
  { value: "small", label: "Small" },
  { value: "medium", label: "Medium" },
  { value: "large", label: "Large" },
  { value: "extra_large", label: "Extra large" },
  { value: "jumbo", label: "Jumbo" },
];

/** What a product's category lets it say about itself; meat's fields are not edited here. */
export interface ProductAttributes {
  organic?: boolean;
  grade?: ProduceGrade;
  [key: string]: unknown;
}

export type BrandTier =
  "value" | "standard" | "premium" | "organic" | "natural" | "prepared" | "specialty";

/** The store brand a product's brand names: whose it is and its tier (2R). */
export interface HouseBrand {
  key: string;
  name: string;
  tier: BrandTier;
  current: boolean;
  replaced_by_name?: string | null;
  family: { key: string; name: string; kind: "retailer" | "wholesaler" | "cooperative" };
}
