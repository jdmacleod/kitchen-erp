# Contributing

## First, before anything else

```bash
make setup
```

That installs the pre-commit hooks. Without them, nothing stands between your
working tree and a public commit of somebody's home address, loyalty number, or
grocery history. `SECURITY.md` explains what each safeguard covers; this file is
the short version of what it means for you.

## Never attach a real receipt to an issue or PR

Not a photograph of one. Not "just the total line". Not the OCR text. Not in a
private fork, which becomes public the moment the PR is opened. The receipts this
project reads carry store locations, loyalty numbers, masked tender lines, times
of day, and a complete list of what one household eats. Once a file is in git
history it is there permanently, short of a `git filter-repo` rewrite that breaks
every clone.

If a receipt will not parse, describe the shape of the problem and write a
**synthetic fixture** that reproduces it — an invented vendor, invented items, the
same layout. `backend/tests/fixtures/receipts/` is full of examples. That fixture
is more useful than the original anyway: it can live in the test suite.

## The synthetic data rules

Every one of these is enforced by `tools/scan_pii.py`, which runs on commit and in
CI. The full list is in `SECURITY.md`; in practice you need:

- Email addresses at `example.com`, `example.org`, or `example.net`.
- Phone numbers in the 555 exchange.
- Masked tender lines ending `0000`.
- **Invented** vendors, stalls, and street addresses. Never a real shop you go to.
- Coordinates in the synthetic grid: latitude 33.0 to 34.0, longitude -121.0 to
  -120.0. That is open ocean inside the tile extract's bounding box, so the map
  still renders and nothing real is there.
- Receipt fixtures as YAML text. The image is generated from the text at test
  time; no image is committed.

If the scanner flags something genuinely benign, suppress that **one line** with a
written reason:

```python
barcode = "012345678905"  # pii-scan: allow documented placeholder UPC
```

Never suppress a whole file, never disable a rule to make CI green, and never
commit with `--no-verify`. If you think a rule is wrong, say so in the PR and
leave it failing — a rule that people route around is worse than no rule.

## If you think real data has entered the tree

Stop. Do not push, and do not try to rewrite history on your own. Say so in the
PR or through private vulnerability reporting, and let the maintainer handle it:
removing something from history needs `git filter-repo` and a forced push that
every clone has to follow.

## Working on the code

```bash
docker compose up -d                    # db, api, worker, web
docker compose exec api kerp migrate    # also seeds the unit table
docker compose exec api pytest
```

`make demo-up && make demo-seed` gives you a throwaway stack full of synthetic
data on port 8081, which is the pleasant way to click around without touching
anything of your own.

What the reviewer will look for, beyond the tests passing:

- **Money and quantities are `Decimal` and `numeric`.** No floats anywhere between
  input, storage, arithmetic, and API output. Decimals serialize as strings.
- **Facts are immutable.** `price_observation`, `price_observation_void`, and
  `ingest_stage_result` are append-only, in the database and in the design.
  Corrections void and re-observe; they never edit.
- **Derivations are rebuildable.** Anything in `price_norm` must survive being
  truncated and recomputed.
- **Conversion never guesses.** `convert()` returns a canonical quantity or a typed
  failure. No default densities, no silent fallbacks.
- **Text from receipts, OCR, and language models is untrusted data.** It is parsed
  against a schema, never interpolated into SQL, never executed, never treated as
  instructions.
- **Routers stay thin**; logic belongs in `services/`. `app/units/` stays pure: no
  I/O, no database, property-tested.
- **Every migration is reversible**, and every endpoint has a request schema, a
  response schema, and at least one test.

Commit in small units that each leave the suite green.

## Licence

Kitchen ERP is MIT. By contributing you agree your contribution is licensed the
same way. `docs/licensing.md` records what may be borrowed from the reference
projects — two of them are AGPL-3.0 and their code must never enter this tree —
and which third-party data carries attribution obligations.
