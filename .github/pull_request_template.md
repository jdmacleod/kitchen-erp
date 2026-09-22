## What this changes, and why

<!-- If it changes behaviour the specification describes, say which document and
     section. If the specification turned out to be wrong, say that instead of
     diverging from it quietly. -->

## Checklist

- [ ] No real receipt text, addresses, coordinates, loyalty numbers, or
      screenshots of real data — in the diff, the tests, the commit messages, or
      this description.
- [ ] New fixtures follow the synthetic rules in `SECURITY.md` (invented vendors,
      `example.com` addresses, 555 phones, the Pacific coordinate grid).
- [ ] `make check` passes, and the hooks ran (no `--no-verify`).
- [ ] Money and quantities are `Decimal` / `numeric` end to end; no floats.
- [ ] Any new endpoint has a request schema, a response schema, and a test.
- [ ] Any migration is reversible.
- [ ] Any scanner suppression is one line with a written reason.
