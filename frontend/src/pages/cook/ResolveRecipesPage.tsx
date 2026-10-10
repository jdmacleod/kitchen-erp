import { Link } from "react-router";
import { PageHeader, focusRing } from "../../components/ui";
import { usePageTitle } from "../../lib/usePageTitle";

const muted = "text-neutral-600 dark:text-neutral-400";

/**
 * Resolve recipe names (`/cook/recipes/resolve`; 10, Resolve recipe names). The
 * inbox row and the list's waiting line link here, so the route exists now; the
 * queue, proposals and picker build with the next package (UI-7.16).
 */
export function ResolveRecipesPage() {
  usePageTitle("Resolve recipe names");
  return (
    <>
      <nav aria-label="Breadcrumb" className={`mb-2 text-sm ${muted}`}>
        <span>Cook</span> <span aria-hidden="true">/</span>{" "}
        <Link to="/cook/recipes" className={`inline-flex min-h-11 items-center rounded underline lg:min-h-0 ${focusRing}`}>
          Recipes
        </Link>
      </nav>
      <PageHeader title="Resolve recipe names" description="Say once which ingredient each name means. It's remembered for every recipe." />
      <p role="status" className={`text-sm ${muted}`}>
        Coming with the next update.
      </p>
    </>
  );
}
