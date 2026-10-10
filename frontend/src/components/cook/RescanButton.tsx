import { useCallback } from "react";
import { errorMessage } from "../../api/client";
import { rescanSentence, useRescanRecipes } from "../../api/recipes";
import { useNotice } from "../Notice";
import { Button } from "../ui";

/**
 * The Cook pages' one primary action (UI-7.6): runs the scan inline, reads
 * "Rescanning…" while it runs, and ends with the Notice saying what changed.
 */
export function useRescanWithNotice() {
  const rescan = useRescanRecipes();
  const notice = useNotice();
  const run = useCallback(() => {
    if (rescan.isPending) return;
    rescan.mutate(undefined, {
      onSuccess: (scan) => notice.show({ tone: "success", message: rescanSentence(scan) }),
      onError: (e) => notice.show({ tone: "error", message: `Couldn't rescan: ${errorMessage(e)}` }),
    });
  }, [rescan, notice]);
  return { run, pending: rescan.isPending };
}

export function RescanButton({ className = "" }: { className?: string }) {
  const { run, pending } = useRescanWithNotice();
  return (
    <Button className={className} onClick={run} disabled={pending} aria-busy={pending || undefined}>
      {pending ? "Rescanning…" : "Rescan recipes"}
    </Button>
  );
}
