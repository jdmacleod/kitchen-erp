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
  purchases: {
    title: "Purchases",
    emptyTitle: "No purchases yet",
    description: "Receipts and manual purchases arrive in Phase 2.",
  },
} as const;
