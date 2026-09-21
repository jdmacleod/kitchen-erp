import { EmptyState, PageHeader } from "../components/ui";
import { usePageTitle } from "../lib/usePageTitle";

interface CatalogPageProps {
  title: string;
  emptyTitle: string;
  description: string;
}

/** A catalog area that has no records yet. Forms arrive in later sub-phases. */
export function CatalogPage({ title, emptyTitle, description }: CatalogPageProps) {
  usePageTitle(title);
  return (
    <>
      <PageHeader title={title} />
      <EmptyState title={emptyTitle}>{description}</EmptyState>
    </>
  );
}

export const catalogPages = {
  ingredients: {
    title: "Ingredients",
    emptyTitle: "No ingredients yet",
    description: "Ingredients are the things you cook with. Adding them arrives in a later sub-phase.",
  },
  products: {
    title: "Products",
    emptyTitle: "No products yet",
    description: "Products are the packaged forms of ingredients that vendors sell.",
  },
  vendors: {
    title: "Vendors",
    emptyTitle: "No vendors yet",
    description: "Vendors are the shops, markets, and stands you buy from.",
  },
  map: {
    title: "Map",
    emptyTitle: "No locations to show",
    description: "Vendor locations and home bases will appear here once they exist.",
  },
  purchases: {
    title: "Purchases",
    emptyTitle: "No purchases yet",
    description: "Receipts and manual purchases arrive in Phase 2.",
  },
} as const;
