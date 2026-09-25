import type { IngredientPricePoint } from "../../api/pricebook";
import { formatDate } from "../../lib/format";
import { formatUnitPrice } from "./PriceAge";

const W = 240;
const H = 56;
const PAD = 6;

/**
 * A 90-day sparkline of the cheapest price each day (D22). One series, so no
 * legend: the card's title names it and the range beside it is written out.
 * Walnut ink from the theme, a 2px line, the latest point marked, and a hit
 * target per point wider than the mark with its date and price. A table of the
 * same points is there for screen readers. Callers hide it below two points.
 *
 * Coordinates are computed with Number(): this is plotting only, and every price
 * shown as text comes from the API's decimal strings.
 */
export function Sparkline({ points, label }: { points: IngredientPricePoint[]; label: string }) {
  const xs = points.map((p) => new Date(p.observed_at).getTime());
  const ys = points.map((p) => Number(p.norm_unit_price));
  const [x0, x1] = [Math.min(...xs), Math.max(...xs)];
  const [y0, y1] = [Math.min(...ys), Math.max(...ys)];
  const sx = (x: number) => PAD + ((x - x0) / (x1 - x0 || 1)) * (W - 2 * PAD);
  // Higher prices sit higher; a flat series draws through the middle.
  const sy = (y: number) => (y1 === y0 ? H / 2 : H - PAD - ((y - y0) / (y1 - y0)) * (H - 2 * PAD));
  const coords = points.map((_, i) => [sx(xs[i]), sy(ys[i])] as const);
  const d = coords.map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const last = coords[coords.length - 1];
  const hit = Math.max(8, (W - 2 * PAD) / points.length);

  return (
    <figure className="m-0">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={label} className="h-auto w-full text-neutral-800 dark:text-neutral-200">
        <path d={d} fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" vectorEffect="non-scaling-stroke" />
        <circle cx={last[0]} cy={last[1]} r="4" fill="currentColor" stroke="var(--color-white)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
        {points.map((p, i) => (
          <rect key={p.observation_id} x={coords[i][0] - hit / 2} y="0" width={hit} height={H} fill="transparent">
            <title>
              {formatDate(p.observed_at)}: {formatUnitPrice(p.norm_unit_price, p.norm_unit)} at {p.vendor_name}
            </title>
          </rect>
        ))}
      </svg>
      <table className="sr-only">
        <caption>{label}</caption>
        <thead>
          <tr>
            <th scope="col">Day</th>
            <th scope="col">Cheapest</th>
            <th scope="col">Where</th>
          </tr>
        </thead>
        <tbody>
          {points.map((p) => (
            <tr key={p.observation_id}>
              <td>{formatDate(p.observed_at)}</td>
              <td>{formatUnitPrice(p.norm_unit_price, p.norm_unit)}</td>
              <td>{p.vendor_name}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
