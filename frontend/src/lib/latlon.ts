/**
 * Read a latitude and longitude out of one line of text.
 *
 * A location can only be created by dropping a pin on the map (issue #20),
 * which means a vendor added from the Vendors page cannot be used for a
 * purchase until someone separately finds it on a grey field. The thing people
 * actually have to hand is a pair of coordinates — copied out of a map
 * application, or read off the phone they are standing in the shop with — so
 * one field that accepts that pair is the form's answer to "where".
 *
 * Accepted: "33.51, -120.48", "33.51 -120.48", "33.51,-120.48", and the same
 * with a leading "+". Degrees/minutes/seconds are not accepted; nothing this
 * project reads emits them, and guessing at a format is worse than saying no.
 *
 * The digits are never turned into a number. They are carried to the API as the
 * strings they were typed as (non-negotiable 1), and the range check below is a
 * check only — its result is a yes or a no, never a value that is stored.
 */
const PAIR = /^([+-]?\d{1,3}(?:\.\d+)?)\s*[,\s]\s*([+-]?\d{1,3}(?:\.\d+)?)$/;

export interface LatLon {
  lat: string;
  lon: string;
}

export function parseLatLon(text: string): LatLon | null {
  const match = PAIR.exec(text.trim());
  if (!match) return null;
  const [, lat, lon] = match;
  if (!inRange(lat, 90) || !inRange(lon, 180)) return null;
  return { lat, lon };
}

function inRange(value: string, limit: number): boolean {
  const n = Number(value);
  return Number.isFinite(n) && Math.abs(n) <= limit;
}
