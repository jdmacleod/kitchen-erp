// Exact decimal arithmetic on strings, for money and quantities in the browser.
//
// Values are decimal strings such as "2.31" or "-0.5". They are never turned
// into JavaScript numbers: every operation works on the digits as a BigInt with
// a separate scale, so "0.1" + "0.2" is "0.3" and 2.31 × 3.99 is exactly 9.2169.
//
// Rounding: the one rule here is ROUND_HALF_EVEN, the same rule the server
// applies to the price book. `div` and `roundTo` take an explicit number of
// places; purchase-entry arithmetic uses MONEY_PLACES (4), so a computed unit
// price or line total carries at most four decimals before it is sent.

export const MONEY_PLACES = 4;

interface Dec {
  neg: boolean;
  /** Absolute value with the decimal point removed. */
  digits: bigint;
  /** Number of digits after the decimal point. */
  scale: number;
}

const DEC_RE = /^(-)?(\d*)(?:\.(\d*))?$/;

function parse(text: string): Dec | null {
  const m = DEC_RE.exec(text.trim());
  if (!m) return null;
  const int = m[2] ?? "";
  const frac = m[3] ?? "";
  if (int === "" && frac === "") return null;
  return { neg: m[1] === "-", digits: BigInt((int || "0") + frac), scale: frac.length };
}

function must(text: string): Dec {
  const d = parse(text);
  if (d === null) throw new Error(`Not a decimal: ${JSON.stringify(text)}`);
  return d;
}

function pow10(n: number): bigint {
  return 10n ** BigInt(n);
}

function render(d: Dec): string {
  let s = d.digits.toString();
  if (d.scale > 0) {
    s = s.padStart(d.scale + 1, "0");
    s = `${s.slice(0, s.length - d.scale)}.${s.slice(s.length - d.scale)}`;
  }
  return d.neg && d.digits !== 0n ? `-${s}` : s;
}

/** Signed magnitude, scaled to a common scale. */
function signed(d: Dec, scale: number): bigint {
  const v = d.digits * pow10(scale - d.scale);
  return d.neg ? -v : v;
}

function fromSigned(v: bigint, scale: number): Dec {
  return { neg: v < 0n, digits: v < 0n ? -v : v, scale };
}

/** Round a magnitude given as `digits / 10^from` to `to` places, half to even. */
function roundDigits(digits: bigint, from: number, to: number): bigint {
  if (from <= to) return digits * pow10(to - from);
  const divisor = pow10(from - to);
  let q = digits / divisor;
  const r = digits % divisor;
  const twice = r * 2n;
  if (twice > divisor || (twice === divisor && q % 2n === 1n)) q += 1n;
  return q;
}

/** True for any decimal string: optional sign, digits, optional fraction. */
export function isDecimal(text: string): boolean {
  return parse(text) !== null;
}

/** True for a decimal that is zero or more (an amount of money can be 0). */
export function isNonNegativeDecimal(text: string): boolean {
  const d = parse(text);
  return d !== null && (!d.neg || d.digits === 0n);
}

export function isZero(text: string): boolean {
  return must(text).digits === 0n;
}

export function add(a: string, b: string): string {
  const x = must(a);
  const y = must(b);
  const scale = Math.max(x.scale, y.scale);
  return render(fromSigned(signed(x, scale) + signed(y, scale), scale));
}

export function sub(a: string, b: string): string {
  const x = must(a);
  const y = must(b);
  const scale = Math.max(x.scale, y.scale);
  return render(fromSigned(signed(x, scale) - signed(y, scale), scale));
}

/** Exact product; the scale is the sum of the operands' scales. */
export function mul(a: string, b: string): string {
  const x = must(a);
  const y = must(b);
  return render({ neg: x.neg !== y.neg, digits: x.digits * y.digits, scale: x.scale + y.scale });
}

/** Quotient rounded half-even to `places`. Throws on a zero divisor. */
export function div(a: string, b: string, places = MONEY_PLACES): string {
  const x = must(a);
  const y = must(b);
  if (y.digits === 0n) throw new Error("Division by zero.");
  // x/y = (x.digits · 10^-x.scale) / (y.digits · 10^-y.scale); scale the
  // numerator so the integer quotient already has `places` decimals.
  const n = x.digits * pow10(places + y.scale);
  const d = y.digits * pow10(x.scale);
  let q = n / d;
  const r = n % d;
  const twice = r * 2n;
  if (twice > d || (twice === d && q % 2n === 1n)) q += 1n;
  return render({ neg: x.neg !== y.neg, digits: q, scale: places });
}

/** Round half-even to `places`, padding with zeros when shorter. */
export function roundTo(a: string, places: number): string {
  const x = must(a);
  return render({ neg: x.neg, digits: roundDigits(x.digits, x.scale, places), scale: places });
}

/** -1, 0, or 1 as a is less than, equal to, or greater than b. */
export function cmp(a: string, b: string): -1 | 0 | 1 {
  const x = must(a);
  const y = must(b);
  const scale = Math.max(x.scale, y.scale);
  const l = signed(x, scale);
  const r = signed(y, scale);
  return l < r ? -1 : l > r ? 1 : 0;
}

/** Drop trailing zeros but keep at least `minPlaces` decimals: "3.9900" → "3.99". */
export function stripZeros(a: string, minPlaces = 0): string {
  const x = must(a);
  let digits = x.digits;
  let scale = x.scale;
  while (scale > minPlaces && digits % 10n === 0n) {
    digits /= 10n;
    scale -= 1;
  }
  if (scale < minPlaces) {
    digits *= pow10(minPlaces - scale);
    scale = minPlaces;
  }
  return render({ neg: x.neg, digits, scale });
}

/**
 * Money for display: rounded half-even to `maxPlaces`, trailing zeros dropped
 * down to `minPlaces`, with a currency sign. Display only; never sent.
 */
export function formatMoney(a: string | null | undefined, minPlaces = 2, maxPlaces = 2): string {
  if (a === null || a === undefined || parse(a) === null) return "";
  const rounded = stripZeros(roundTo(a, maxPlaces), minPlaces);
  return rounded.startsWith("-") ? `-$${rounded.slice(1)}` : `$${rounded}`;
}
