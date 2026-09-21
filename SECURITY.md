# Personal data and this repository

Kitchen ERP is a household system. A running deployment holds receipt photographs,
itemized purchase histories, the household's email addresses, home coordinates, and
the coordinates of every shop and stand the household visits. The repository that
describes the system is meant to be public. The rule that reconciles those two facts:

**Nothing that came from a real receipt, a real retailer export, a real home, or a
real person is ever committed.** Not in a fixture, not in a test name, not in a
commit message, not in a screenshot, not in a branch that will be deleted.

## What counts as personal data here

- Receipt images in any format, and OCR text taken from a real receipt.
- Retailer exports: the right-to-know responses that `unbagged` parses, and
  anything derived from them. That data lives in the sibling `unbagged` repository's
  own gitignored `data/` directory and is never copied into this tree.
- Home base names and coordinates, and the coordinates of habitually visited
  vendor locations. Coordinates identify a household as surely as an address.
- Loyalty card numbers, masked tender lines, order numbers, email addresses,
  phone numbers, street addresses.
- Absolute paths under a home directory, which leak the account name and the
  names of neighbouring projects.

Public facts about a chain store (its name, its published address) are not
personal data on their own, but a list of the stores one household visits is.
Invent vendors for anything committed.

## Where real data may live

Only under `data/` at the repository root, which is gitignored, dockerignored,
bind-mounted at runtime, and refused by a commit hook even when force-added.
`data/imports/` is the place for a retailer export you intend to load into your
own deployment. Nothing under `data/` is read by any test, fixture generator,
or documentation build.

## The safeguards, and what each one covers

| Safeguard | Covers |
|---|---|
| `.gitignore` | Denies `data/`, backups, reference clones, every image, spreadsheet, archive, PDF and database suffix. One re-inclusion, `frontend/public/`, which CI size-checks. |
| `.dockerignore` | The same denials for the build context, so real data never reaches an image layer or a remote builder. |
| `tools/no_data_dir.py` (pre-commit) | Refuses any staged path under `data/`, a backup, or a reference clone, even via `git add -f`. |
| `tools/scan_pii.py` (pre-commit and CI) | Regex rules for emails, phones, street addresses, ZIP fragments, card numbers, masked tender lines, loyalty-length digit runs without a GS1 check digit, coordinate pairs, and home-directory paths, plus a literal denylist. Scans the tree and, in CI, every added line and commit message in history. |
| `tools/denylist.txt` (gitignored) | Literal strings that must never appear: your loyalty numbers, your addresses, the first three decimals of your home coordinates. Built with `tools/build_denylist.py`, which prints counts only. |
| gitleaks (pre-commit and CI) | Secrets and tokens. |
| CI stray-file check | Fails if any data-shaped suffix is tracked outside `frontend/public/`. |
| CI re-inclusion check | Fails if `.gitignore` gains a `!` rule without a matching check. |

Every suppression of the scanner is a line with a written reason:

    # pii-scan: allow documented placeholder UPC

Never suppress a whole file, never disable a rule to make CI green, never commit
with `--no-verify`.

## Synthetic data rules for tests and fixtures

- Email addresses use `example.com`, `example.org`, or `example.net`.
- Phone numbers use the 555 exchange.
- Masked tender lines end in `0000`.
- Vendors, stalls, and street addresses are invented. A fixture that carries an
  invented address suppresses the finding with a reason naming it as invented.
- Geography for tests and fixtures uses a **synthetic grid**: coordinates in the
  Pacific Ocean west of the Californian coast, latitude 33.0 to 34.0 and longitude
  -121.0 to -120.0. That region is inside the tile extract's bounding box, so the
  map still renders it, and it contains no home and no shop. The coordinate rule
  stands down inside tests and fixtures on that condition; the denylist's home
  coordinate prefixes still apply everywhere.
- Receipt fixtures are text (YAML) with the receipt image generated from that
  text at test time. No image is committed.
- The USDA reference table, map tiles, and any retailer export are downloaded or
  copied into `data/` locally and never committed.

## Reporting

If you believe real data has entered the working tree or history: stop, do not
push, and do not try to rewrite history alone. Removing a file from history needs
`git filter-repo` and a forced push that every clone must follow.

Security issues in the application itself, such as path traversal in receipt
upload, injection through OCR or model output, or an outbound request that
carries household data, are in scope for the same channel: GitHub's private
vulnerability reporting on this repository. Do not attach a real receipt to a
report.
