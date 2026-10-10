import type { HouseBrand } from "../../api/catalog";
import { Badge } from "./fields";

/**
 * Whose own brand a product is, and its tier (2R): "Larkspur Markets brand · standard",
 * or in a list, "Store brand · standard" with the family in the tooltip. A wholesale
 * label reads "Store label". Information, so neutral; a national or unknown brand
 * shows nothing.
 */
export function StoreBrandBadge({ brand, compact = false }: { brand: HouseBrand | null | undefined; compact?: boolean }) {
  if (!brand) return null;
  const retailer = brand.family.kind === "retailer";
  const whose = compact ? (retailer ? "Store brand" : "Store label") : retailer ? `${brand.family.name} brand` : `Store label (${brand.family.name})`;
  const retired = !brand.current && brand.replaced_by_name ? ` · now ${brand.replaced_by_name}` : "";
  return (
    <Badge>
      <span title={`${brand.name}: ${retailer ? `${brand.family.name}'s own brand` : `a label ${brand.family.name} sells to many stores`}`}>
        {whose} · {brand.tier}
        {compact ? "" : retired}
      </span>
    </Badge>
  );
}
