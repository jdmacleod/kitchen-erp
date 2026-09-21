import { Link } from "react-router";
import { EmptyState, PageHeader, focusRing } from "../components/ui";
import { usePageTitle } from "../lib/usePageTitle";

export function NotPermittedPage() {
  usePageTitle("Not permitted");
  return (
    <>
      <PageHeader title="Not permitted" />
      <EmptyState title="This page is for administrators">
        <p>Ask an admin of this household if you need access.</p>
        <Link to="/" className={`mt-3 inline-block rounded-md underline ${focusRing}`}>
          Back to the catalog
        </Link>
      </EmptyState>
    </>
  );
}
