/**
 * What became of a price's normalization: "ok", or what it is missing.
 * `no_bunch_weight` is a price per bunch that nothing has weighed yet (issue 371):
 * it carries a price per bunch, and compares only with other per-bunch prices.
 */
export type NormStatus =
  "ok" | "no_density" | "unknown_measure" | "no_pack" | "no_qty" | "no_bunch_weight";
