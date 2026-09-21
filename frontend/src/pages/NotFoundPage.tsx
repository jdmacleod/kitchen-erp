import { Link } from "react-router";
import { EmptyState, PageHeader, focusRing } from "../components/ui";
import { usePageTitle } from "../lib/usePageTitle";

export function NotFoundPage() {
  usePageTitle("Not found");
  return (
    <>
      <PageHeader title="Not found" />
      <EmptyState title="There is nothing at this address">
        <Link to="/" className={`mt-3 inline-block rounded-md underline ${focusRing}`}>
          Back to the catalog
        </Link>
      </EmptyState>
    </>
  );
}
