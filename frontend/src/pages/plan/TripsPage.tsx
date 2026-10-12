import { EmptyState, PageHeader } from "../../components/ui";
import { usePageTitle } from "../../lib/usePageTitle";

/** `/plan/trips` exists from 4A (UI-8.1); the trips list and the planner arrive with 4B. */
export function TripsPage() {
  usePageTitle("Trips");
  return (
    <>
      <PageHeader title="Trips" description="Which stores, in what order, buying what at each." />
      <EmptyState title="Trips come with the next update">The trip planner is built in the next sub-phase. Your shopping list is ready for it.</EmptyState>
    </>
  );
}
