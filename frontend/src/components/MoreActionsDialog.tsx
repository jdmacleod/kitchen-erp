import { useId } from "react";
import { Dialog } from "./Dialog";
import { Button } from "./ui";

export interface MoreAction {
  label: string;
  open: () => void;
  variant?: "secondary" | "danger";
  disabled?: boolean;
}

/** Below 1024px a page's secondary actions share one "More actions" sheet (design D15). */
export function MoreActionsDialog({ actions, onClose }: { actions: MoreAction[]; onClose: () => void }) {
  const titleId = useId();
  return (
    <Dialog open onClose={onClose} labelledBy={titleId} placement="sheet" className="p-5">
      <h2 id={titleId} className="mb-3 font-display text-xl">
        More actions
      </h2>
      <div className="flex flex-col gap-2">
        {actions.map((a) => (
          <Button key={a.label} variant={a.variant ?? "secondary"} disabled={a.disabled} onClick={a.open}>
            {a.label}
          </Button>
        ))}
        <Button variant="ghost" onClick={onClose}>
          Close
        </Button>
      </div>
    </Dialog>
  );
}
