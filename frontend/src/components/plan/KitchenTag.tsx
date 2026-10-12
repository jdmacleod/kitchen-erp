import { Badge } from "../catalog/fields";

/**
 * The kitchen's name as a neutral pill after a page or panel title (10, The tag;
 * UI-2.6). Only kitchen-scoped panels carry one: the list, the Home list panel,
 * trips, and Compare and the cost table with their filter on. Never herb.
 */
export function KitchenTag({ name }: { name: string }) {
  return (
    <span data-testid="kitchen-tag">
      <Badge tone="neutral">
        <span className="sr-only">Kitchen:</span>{" "}
        {name}
      </Badge>
    </span>
  );
}
