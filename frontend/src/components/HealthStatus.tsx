import { useHealth } from "../api/queries";

const dot: Record<string, string> = {
  ok: "bg-green-500",
  degraded: "bg-amber-500",
  failed: "bg-red-500",
  unreachable: "bg-neutral-400",
};

export function HealthStatus() {
  const health = useHealth();
  const word = health.isPending ? "checking" : health.isError ? "unreachable" : health.data.status;
  return (
    <p className="flex items-center gap-2 text-xs text-neutral-600 dark:text-neutral-400">
      <span aria-hidden="true" className={`inline-block size-2 rounded-full ${dot[word] ?? dot.unreachable}`} />
      <span>
        System: <span data-testid="health-status">{word}</span>
      </span>
    </p>
  );
}
