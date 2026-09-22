import { useMemo } from "react";
import type { PricePoint } from "../../api/pricebook";
import { formatMoney } from "../../lib/decimal";
import { formatDate } from "../../lib/format";

// Six strokes, each paired with its own dash pattern and marker shape, so
// series are told apart without colour.
const STROKES = ["#2563eb", "#d97706", "#15803d", "#7c3aed", "#dc2626", "#0f766e"];
const DASHES = ["", "6 3", "2 3", "8 3 2 3", "4 2", "10 4"];

const W = 640;
const H = 280;
const PAD = { top: 16, right: 16, bottom: 40, left: 64 };

interface Series {
  key: string;
  name: string;
  points: (PricePoint & { x: number; y: number })[];
}

interface PriceHistoryChartProps {
  points: PricePoint[];
  /** The unit the normalized prices are in, for the axis label. */
  unit: string | null;
}

/**
 * Price per canonical unit over time, one line per series (a chain-scoped
 * vendor or a single location), drawn with inline SVG. Promotional points get
 * a diamond marker and the word "sale" next to it, never colour alone.
 * Decimal strings become numbers here only to place marks on the canvas.
 */
export function PriceHistoryChart({ points, unit }: PriceHistoryChartProps) {
  const chart = useMemo(() => {
    const usable = points.filter((p) => p.norm_status === "ok" && p.norm_unit_price !== null);
    const skipped = points.length - usable.length;
    if (usable.length === 0) return { series: [] as Series[], skipped, ticksY: [] as number[], ticksX: [] as number[], scale: null };

    const times = usable.map((p) => new Date(p.observed_at).getTime());
    const prices = usable.map((p) => Number(p.norm_unit_price));
    let tMin = Math.min(...times);
    let tMax = Math.max(...times);
    if (tMin === tMax) {
      tMin -= 86_400_000;
      tMax += 86_400_000;
    }
    const pMax = Math.max(...prices) * 1.1 || 1;
    const pMin = 0;
    const sx = (t: number) => PAD.left + ((t - tMin) / (tMax - tMin)) * (W - PAD.left - PAD.right);
    const sy = (v: number) => H - PAD.bottom - ((v - pMin) / (pMax - pMin)) * (H - PAD.top - PAD.bottom);

    const bySeries = new Map<string, Series>();
    for (const p of usable) {
      let s = bySeries.get(p.series);
      if (!s) {
        s = { key: p.series, name: p.price_scope === "chain" ? p.vendor_name : p.location_name, points: [] };
        bySeries.set(p.series, s);
      }
      s.points.push({ ...p, x: sx(new Date(p.observed_at).getTime()), y: sy(Number(p.norm_unit_price)) });
    }
    for (const s of bySeries.values()) s.points.sort((a, b) => a.x - b.x);

    const ticksY = [0, 0.25, 0.5, 0.75, 1].map((f) => pMin + f * (pMax - pMin));
    const ticksX = [0, 0.5, 1].map((f) => tMin + f * (tMax - tMin));
    return { series: [...bySeries.values()], skipped, ticksY, ticksX, scale: { sx, sy } };
  }, [points]);

  if (!chart.scale) {
    return (
      <p className="text-sm text-neutral-600 dark:text-neutral-400">
        {points.length === 0 ? "No prices recorded yet." : "No normalized prices to chart yet; see the needs-a-bridge list."}
      </p>
    );
  }
  const { sx, sy } = chart.scale;
  const fmtY = (v: number) => formatMoney(v.toFixed(6), 2, 4);

  return (
    <figure className="flex flex-col gap-2">
      <div className="overflow-x-auto">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          role="img"
          aria-label={`Price per ${unit ?? "unit"} over time, ${chart.series.length} series`}
          className="h-auto w-full min-w-[420px] text-neutral-700 dark:text-neutral-300"
          data-testid="price-history-chart"
        >
          {chart.ticksY.map((v) => (
            <g key={v}>
              <line x1={PAD.left} x2={W - PAD.right} y1={sy(v)} y2={sy(v)} stroke="currentColor" strokeOpacity="0.15" />
              <text x={PAD.left - 6} y={sy(v) + 4} textAnchor="end" fontSize="11" fill="currentColor">
                {fmtY(v)}
              </text>
            </g>
          ))}
          {chart.ticksX.map((t, i) => (
            <text key={t} x={sx(t)} y={H - PAD.bottom + 18} textAnchor={i === 0 ? "start" : i === 2 ? "end" : "middle"} fontSize="11" fill="currentColor">
              {formatDate(new Date(t).toISOString())}
            </text>
          ))}
          <text x={PAD.left} y={H - 4} fontSize="11" fill="currentColor">
            per {unit ?? "unit"}
          </text>
          {chart.series.map((s, i) => (
            <g key={s.key} data-testid="price-series" data-series={s.key}>
              {s.points.length > 1 ? (
                <polyline
                  points={s.points.map((p) => `${p.x},${p.y}`).join(" ")}
                  fill="none"
                  stroke={STROKES[i % STROKES.length]}
                  strokeWidth="2"
                  strokeDasharray={DASHES[i % DASHES.length] || undefined}
                />
              ) : null}
              {s.points.map((p) => (
                <g key={p.observation_id} data-testid={p.is_promo ? "promo-point" : "price-point"}>
                  <title>
                    {`${s.name}: ${formatMoney(p.norm_unit_price, 2, 6)} per ${p.norm_unit} on ${formatDate(p.observed_at)}${p.is_promo ? " (sale)" : ""}`}
                  </title>
                  {p.is_promo ? (
                    <>
                      <path
                        d={`M${p.x},${p.y - 6} L${p.x + 6},${p.y} L${p.x},${p.y + 6} L${p.x - 6},${p.y} Z`}
                        fill="#fff"
                        stroke={STROKES[i % STROKES.length]}
                        strokeWidth="2"
                      />
                      <text x={p.x + 8} y={p.y - 6} fontSize="10" fill="currentColor">
                        sale
                      </text>
                    </>
                  ) : (
                    <circle cx={p.x} cy={p.y} r="3.5" fill={STROKES[i % STROKES.length]} />
                  )}
                </g>
              ))}
            </g>
          ))}
        </svg>
      </div>
      <figcaption className="flex flex-wrap gap-x-4 gap-y-1 text-xs">
        {chart.series.map((s, i) => (
          <span key={s.key} className="inline-flex items-center gap-1.5">
            <svg width="28" height="10" aria-hidden="true">
              <line x1="0" x2="28" y1="5" y2="5" stroke={STROKES[i % STROKES.length]} strokeWidth="2" strokeDasharray={DASHES[i % DASHES.length] || undefined} />
            </svg>
            {s.name}
          </span>
        ))}
        <span className="inline-flex items-center gap-1.5 text-neutral-600 dark:text-neutral-400">
          <svg width="14" height="14" aria-hidden="true">
            <path d="M7,1 L13,7 L7,13 L1,7 Z" fill="#fff" stroke="currentColor" strokeWidth="1.5" />
          </svg>
          sale price
        </span>
        {chart.skipped > 0 ? (
          <span className="text-neutral-600 dark:text-neutral-400">
            {chart.skipped} not normalized and not charted
          </span>
        ) : null}
      </figcaption>
    </figure>
  );
}
