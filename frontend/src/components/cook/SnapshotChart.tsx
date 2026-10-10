import { useMemo } from "react";
import type { CostHistoryItem, RecipeCost } from "../../api/recipes";
import { formatMoney } from "../../lib/decimal";
import { formatDate } from "../../lib/format";
import { SERIES_COLOURS } from "../../lib/palette";

const W = 640;
const H = 240;
const PAD = { top: 16, right: 16, bottom: 40, left: 64 };

export interface SnapshotPoint {
  id: string;
  computed_at: string;
  consumed_cost: string;
  provisional: boolean;
}

/** The committed snapshots, then the current provisional one when there is one. */
export function snapshotPoints(history: CostHistoryItem[], current: RecipeCost | null | undefined): SnapshotPoint[] {
  const points: SnapshotPoint[] = history
    .filter((h) => h.totals.consumed_cost !== null)
    .map((h) => ({ id: h.id, computed_at: h.computed_at, consumed_cost: h.totals.consumed_cost as string, provisional: false }));
  if (current?.provisional && current.totals.consumed_cost !== null && !points.some((p) => p.id === current.id)) {
    points.push({ id: current.id, computed_at: current.computed_at, consumed_cost: current.totals.consumed_cost, provisional: true });
  }
  return points.sort((a, b) => new Date(a.computed_at).getTime() - new Date(b.computed_at).getTime());
}

/**
 * The cost history (10, Cost history; UI-7.13): the consumed cost of each committed
 * snapshot under the current basis, by date, with the provisional one drawn hollow
 * and labelled. Hidden with fewer than two points (D22). One series, from the chart
 * palette; decimal strings become numbers here only to place marks on the canvas.
 */
export function SnapshotChart({ points }: { points: SnapshotPoint[] }) {
  const chart = useMemo(() => {
    if (points.length < 2) return null;
    const times = points.map((p) => new Date(p.computed_at).getTime());
    const costs = points.map((p) => Number(p.consumed_cost));
    let tMin = Math.min(...times);
    let tMax = Math.max(...times);
    if (tMin === tMax) {
      tMin -= 86_400_000;
      tMax += 86_400_000;
    }
    const cMax = Math.max(...costs) * 1.1 || 1;
    const sx = (t: number) => PAD.left + ((t - tMin) / (tMax - tMin)) * (W - PAD.left - PAD.right);
    const sy = (v: number) => H - PAD.bottom - (v / cMax) * (H - PAD.top - PAD.bottom);
    const placed = points.map((p, i) => ({ ...p, x: sx(times[i]), y: sy(costs[i]) }));
    const ticksY = [0, 0.25, 0.5, 0.75, 1].map((f) => f * cMax);
    const ticksX = [0, 0.5, 1].map((f) => tMin + f * (tMax - tMin));
    return { placed, ticksY, ticksX, sx, sy };
  }, [points]);

  if (!chart) return null;
  const stroke = SERIES_COLOURS[0];
  const fmtY = (v: number) => formatMoney(v.toFixed(6), 2, 2);

  return (
    <figure className="flex flex-col gap-2" data-testid="snapshot-chart">
      <figcaption className="text-sm font-medium">Cost history</figcaption>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`Consumed cost over ${points.length} snapshots`}
        className="h-auto w-full text-neutral-700 dark:text-neutral-300"
      >
        {chart.ticksY.map((v) => (
          <g key={v}>
            <line x1={PAD.left} x2={W - PAD.right} y1={chart.sy(v)} y2={chart.sy(v)} stroke="currentColor" strokeOpacity="0.15" />
            <text x={PAD.left - 6} y={chart.sy(v) + 4} textAnchor="end" fontSize="11" fill="currentColor">
              {fmtY(v)}
            </text>
          </g>
        ))}
        {chart.ticksX.map((t, i) => (
          <text key={t} x={chart.sx(t)} y={H - PAD.bottom + 18} textAnchor={i === 0 ? "start" : i === 2 ? "end" : "middle"} fontSize="11" fill="currentColor">
            {formatDate(new Date(t).toISOString())}
          </text>
        ))}
        <polyline points={chart.placed.map((p) => `${p.x},${p.y}`).join(" ")} fill="none" stroke={stroke} strokeWidth="2" />
        {chart.placed.map((p) => (
          <g key={p.id} data-testid={p.provisional ? "provisional-point" : "snapshot-point"}>
            <title>{`${formatMoney(p.consumed_cost)} on ${formatDate(p.computed_at)}${p.provisional ? " (provisional)" : ""}`}</title>
            {p.provisional ? (
              <>
                <circle cx={p.x} cy={p.y} r="4" fill="var(--color-white)" stroke={stroke} strokeWidth="2" />
                <text x={p.x + 8} y={p.y - 6} fontSize="10" fill="currentColor">
                  provisional
                </text>
              </>
            ) : (
              <circle cx={p.x} cy={p.y} r="3.5" fill={stroke} />
            )}
          </g>
        ))}
      </svg>
      <p className="flex flex-wrap gap-x-4 gap-y-1 text-xs text-neutral-600 dark:text-neutral-400">
        <span className="inline-flex items-center gap-1.5">
          <svg width="14" height="14" aria-hidden="true">
            <circle cx="7" cy="7" r="3.5" fill={stroke} />
          </svg>
          committed snapshot
        </span>
        <span className="inline-flex items-center gap-1.5">
          <svg width="14" height="14" aria-hidden="true">
            <circle cx="7" cy="7" r="4" fill="var(--color-white)" stroke={stroke} strokeWidth="2" />
          </svg>
          provisional
        </span>
      </p>
    </figure>
  );
}
