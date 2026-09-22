#!/usr/bin/env python3
"""Scan the working tree (and, in CI, git history) for personal data.

Kitchen ERP's deployment holds receipt photographs, purchase histories, home
coordinates, and the household's email addresses. The repository is meant to be
public. This scanner is the backstop against one of those crossing over.

Adapted from the scanner in jdmacleod/unbagged (MIT), by the same author.

Usage:
    scan_pii.py                     scan tracked + untracked-but-not-ignored files
    scan_pii.py PATH [PATH ...]     scan specific paths (how pre-commit invokes it)
    scan_pii.py --history           scan commit messages and diffs of the whole history
    scan_pii.py --history RANGE     scan a revision range, e.g. origin/main..HEAD

Exits non-zero on any finding. Matched text is masked in the output: a scanner that
prints the PII it found into a public CI log has not helped anyone.

False positives are suppressed per line, with a reason:

    barcode = "012345678905"  # pii-scan: allow documented placeholder UPC

The marker may sit on the offending line or the line directly above it, so formats
without comments (JSON, plain text) can still be annotated from the line before.
YAML fixtures can carry the marker as a comment.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

try:
    from tools.denylist import EMPTY as EMPTY_DENYLIST
    from tools.denylist import Denylist, load_denylist
except ImportError:  # invoked as a path rather than as `python -m tools.scan_pii`
    sys.path.insert(0, str(REPO_ROOT))
    from tools.denylist import EMPTY as EMPTY_DENYLIST
    from tools.denylist import Denylist, load_denylist

SUPPRESSION = re.compile(r"pii-scan:\s*allow\b(?P<reason>.*)")
HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")
# \x01 marks a commit boundary. Author and committer lines are deliberately
# omitted: the repo owner's own git identity is not the leak this tool looks for.
LOG_FORMAT = "--format=%x01%H%n%B"

# Never scanned: binary by nature, not ours, or explicitly off limits.
SKIP_DIR_PARTS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".hypothesis",
    "dist",
    "build",
    "htmlcov",
    # Off limits by policy, not just noise. `data/` is the deployment's store;
    # the rest are reference clones under other licences.
    "data",
    "reference",
    "cookcli",
    "grocy",
    "kitchenowl",
    "mealie",
    "paprika-recipes",
}
SKIP_SUFFIXES = {
    ".png",
    ".jpg",
    ".jpeg",
    ".heic",
    ".heif",
    ".gif",
    ".webp",
    ".tif",
    ".tiff",
    ".bmp",
    ".ico",
    ".svg",
    ".pdf",
    ".zip",
    ".gz",
    ".tgz",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".eot",
    ".sqlite",
    ".sqlite3",
    ".db",
    ".pmtiles",
    ".mbtiles",
    ".pbf",
    ".lock",
}

# A fixture directory may hold text the scanner reads. Anything it cannot read
# there is a finding: nothing else reviews it.
OPAQUE_FIXTURE_HINT = (
    "Unreadable file committed in a fixtures directory. Several address rules "
    "stand down inside fixtures, so a file this scanner cannot read is reviewed "
    "by nothing. Commit fixtures as text (YAML/JSON) and generate any image at "
    "test time."
)

# Email domains that cannot belong to a real person.
ALLOWED_EMAIL_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "localhost",
    "noreply.github.com",
    "users.noreply.github.com",
    "anthropic.com",
}
ALLOWED_EMAILS = {"support@github.com"}
ALLOWED_EMAIL_SUFFIXES = (".invalid", ".example", ".test", ".localhost")

US_STATES = (
    "AL|AK|AZ|AR|CA|CO|CT|DE|FL|GA|HI|ID|IL|IN|IA|KS|KY|LA|ME|MD|MA|MI|MN|MS|MO|"
    "MT|NE|NV|NH|NJ|NM|NY|NC|ND|OH|OK|OR|PA|RI|SC|SD|TN|TX|UT|VT|VA|WA|WV|WI|WY|DC"
)
STREET_TYPES = (
    "St|Street|Ave|Avenue|Rd|Road|Blvd|Boulevard|Ln|Lane|Dr|Drive|Ct|Court|Way|Pl|"
    "Place|Ter|Terrace|Cir|Circle|Hwy|Highway|Pkwy|Parkway|Trl|Trail|Loop|Row|Walk|"
    "Mews|Crescent|Alley|Grove"
)


@dataclass(frozen=True)
class Finding:
    origin: str  # file path, or a commit identifier during a history scan
    line_no: int
    rule: str
    matched: str
    hint: str

    def render(self) -> str:
        return f"{self.origin}:{self.line_no}: [{self.rule}] {mask(self.matched)}\n    {self.hint}"


@dataclass(frozen=True)
class Rule:
    name: str
    pattern: re.Pattern[str]
    hint: str
    # Returns True when a raw regex match is a real finding.
    accept: Callable[[re.Match[str]], bool] = lambda _m: True
    # When False, the rule is skipped inside fixture directories.
    applies_in_fixtures: bool = True
    # When False, the rule is skipped inside test files.
    applies_in_tests: bool = True


def mask(text: str) -> str:
    """Show enough to locate the hit, not enough to leak it."""
    text = text.strip()
    if len(text) <= 4:
        return "*" * len(text)
    return f"{text[:2]}{'*' * (len(text) - 4)}{text[-2:]}"


def luhn_ok(digits: str) -> bool:
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        d = int(ch)
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def gs1_ok(digits: str) -> bool:
    """GS1 check digit, as used by UPC-A (12), EAN-13 (13), and GTIN-14 (14).

    Product barcodes are legitimate data in this project and are the same length
    as loyalty-card numbers. A run that carries a valid GS1 check digit is treated
    as a barcode; one that does not is treated as an identifier.
    """
    if len(digits) not in (8, 12, 13, 14):
        return False
    body, check = digits[:-1], int(digits[-1])
    total = 0
    for i, ch in enumerate(reversed(body)):
        total += int(ch) * (3 if i % 2 == 0 else 1)
    return (10 - total % 10) % 10 == check


HEX_RUN = re.compile(r"[0-9a-fA-F]{32,}")


def _not_inside_hash(m: re.Match[str]) -> bool:
    """False when the match sits inside a long hex token: a digest, not a number."""
    text = m.string
    return not any(
        run.start() <= m.start() and m.end() <= run.end()
        for run in HEX_RUN.finditer(text)
    )


def _email_is_real(m: re.Match[str]) -> bool:
    if m.group(0).lower() in ALLOWED_EMAILS:
        return False
    domain = m.group("domain").lower()
    return not (
        domain in ALLOWED_EMAIL_DOMAINS or domain.endswith(ALLOWED_EMAIL_SUFFIXES)
    )


def _phone_is_real(m: re.Match[str]) -> bool:
    # 555 is the reserved-for-fiction exchange.
    return m.group("exchange") != "555"


def _card_is_real(m: re.Match[str]) -> bool:
    digits = re.sub(r"\D", "", m.group(0))
    return 13 <= len(digits) <= 19 and luhn_ok(digits) and _not_inside_hash(m)


def _id_run_is_real(m: re.Match[str]) -> bool:
    digits = m.group(0)
    return _not_inside_hash(m) and not gs1_ok(digits)


def _masked_card_is_real(m: re.Match[str]) -> bool:
    # A synthetic receipt may show a masked tender line; it must end in 0000.
    return m.group("last4") != "0000"


def _coordinate_is_real(m: re.Match[str]) -> bool:
    lat, lon = float(m.group("a")), float(m.group("b"))
    if abs(lat) > 90 and abs(lon) <= 90:
        lat, lon = lon, lat  # GeoJSON order: [lon, lat]
    if abs(lat) > 90 or abs(lon) > 180:
        return False
    # (0, 0) and other axis points are placeholders, not places.
    return not (lat == 0 or lon == 0)


RULES: tuple[Rule, ...] = (
    Rule(
        name="EMAIL",
        pattern=re.compile(
            r"\b[A-Za-z0-9._%+-]+@(?P<domain>[A-Za-z0-9.-]+\.[A-Za-z]{2,})\b"
        ),
        hint="Use an example.com / example.org address instead.",
        accept=_email_is_real,
    ),
    Rule(
        name="PHONE",
        pattern=re.compile(
            r"(?<!\d)(?:\+?1[-. ]?)?\(?\d{3}\)?[-. ](?P<exchange>\d{3})[-. ]\d{4}(?!\d)"
        ),
        hint="Fabricated numbers must use the reserved 555 exchange.",
        accept=_phone_is_real,
    ),
    Rule(
        name="STREET_ADDRESS",
        pattern=re.compile(
            rf"\b\d{{1,6}}\s+(?:[A-Z][A-Za-z.'-]*\s+){{0,3}}(?:{STREET_TYPES})\b\.?",
        ),
        hint="Fabricated addresses must not resolve to a real location. Inside a "
        "fixture, suppress with a reason naming the invented street.",
    ),
    Rule(
        name="ZIP_WITH_STATE",
        pattern=re.compile(rf"\b(?:{US_STATES})\b[ ,]+\d{{5}}(?:-\d{{4}})?\b"),
        hint="A state abbreviation next to a ZIP is an address fragment.",
    ),
    Rule(
        name="ZIP_PLUS_FOUR",
        pattern=re.compile(r"(?<!\d)\d{5}-\d{4}(?!\d)"),
        hint="ZIP+4 identifies a household on its own.",
    ),
    Rule(
        name="PAYMENT_CARD",
        pattern=re.compile(r"(?<!\d)(?:\d[ -]?){12,18}\d(?!\d)"),
        hint="Digit run passes a Luhn check; treat it as a card number.",
        accept=_card_is_real,
    ),
    Rule(
        name="MASKED_CARD",
        pattern=re.compile(
            r"(?:(?:[*Xx#]{4,}|\b(?:ending|ending in|last four|last 4)\b)[ -]?(?P<last4>\d{4})\b)"
        ),
        hint="Masked tender line from a receipt. Synthetic receipts must end in 0000.",
        accept=_masked_card_is_real,
    ),
    Rule(
        name="SSN",
        pattern=re.compile(
            r"(?<!\d)(?!000|666|9\d\d)\d{3}-(?!00)\d{2}-(?!0000)\d{4}(?!\d)"
        ),
        hint="Never store or fabricate SSN-shaped values.",
    ),
    Rule(
        name="ID_NUMBER",
        pattern=re.compile(r"(?<![\d.])\d{9,14}(?![\d.])"),
        hint="Loyalty-card-length digit run without a valid GS1 check digit. A real "
        "barcode passes the check; suppress inline for any other placeholder.",
        accept=_id_run_is_real,
    ),
    Rule(
        name="COORDINATE_PAIR",
        pattern=re.compile(
            r"(?<![\d.-])(?P<a>-?\d{1,3}\.\d{3,})\s*,\s*(?P<b>-?\d{1,3}\.\d{3,})(?![\d.])"
        ),
        hint="A decimal coordinate pair. Home and habitual-vendor coordinates are "
        "personal data. Tests and fixtures use the synthetic geography documented "
        "in SECURITY.md; anywhere else, suppress inline with a reason.",
        accept=_coordinate_is_real,
        applies_in_fixtures=False,
        applies_in_tests=False,
    ),
    Rule(
        name="LOCAL_PATH",
        pattern=re.compile(r"(?:/Users|/home)/[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+)*"),
        hint="An absolute path under a home directory leaks the account name and "
        "neighbouring project names. Use a relative path, $HOME, or a placeholder.",
    ),
)


def classify(path: str) -> tuple[bool, bool]:
    """Return (is_fixture, is_test) for a repo-relative path."""
    p = Path(path)
    is_fixture = "fixtures" in p.parts
    is_test = (
        "tests" in p.parts
        or "__tests__" in p.parts
        or p.name.startswith("test_")
        or p.name.endswith((".test.ts", ".test.tsx", ".spec.ts", ".spec.tsx"))
    )
    return is_fixture, is_test


def opaque_in_fixtures(path: str) -> bool:
    """True for a fixture file this scanner cannot read and cannot vouch for."""
    return "fixtures" in Path(path).parts


def suppressed(line: str, preceding: str = "") -> bool:
    """A marker on this line, or the line directly above it, silences findings here.

    A bare marker does not suppress: a reason is required, so that skipping a rule
    is always a decision somebody wrote down.
    """
    for candidate in (line, preceding):
        m = SUPPRESSION.search(candidate)
        if m and m.group("reason").strip():
            return True
    return False


def scan_lines(
    lines: Sequence[str],
    origin: str,
    *,
    is_fixture: bool = False,
    is_test: bool = False,
    denylist: Denylist = EMPTY_DENYLIST,
    line_offset: int = 1,
    preceding: str = "",
) -> list[Finding]:
    """Scan `lines`. `preceding` is the line above lines[0], supplied when the caller
    holds only a window into a larger file, as the history walker does."""
    active = [
        r
        for r in RULES
        if (r.applies_in_fixtures or not is_fixture)
        and (r.applies_in_tests or not is_test)
    ]
    found: list[Finding] = []
    for idx, line in enumerate(lines):
        if suppressed(line, lines[idx - 1] if idx > 0 else preceding):
            continue
        lowered = line.lower()
        for matched in denylist.find(lowered):
            found.append(
                Finding(
                    origin,
                    idx + line_offset,
                    "DENYLIST",
                    matched,
                    "Literal string from the denylist. See SECURITY.md.",
                )
            )
        for rule in active:
            for m in rule.pattern.finditer(line):
                if rule.accept(m):
                    found.append(
                        Finding(
                            origin, idx + line_offset, rule.name, m.group(0), rule.hint
                        )
                    )
    return found


def is_scannable(path: Path) -> bool:
    if any(part in SKIP_DIR_PARTS for part in path.parts):
        return False
    if path.suffix.lower() in SKIP_SUFFIXES:
        return False
    if not path.is_file():
        return False
    try:
        with path.open("rb") as fh:
            if b"\x00" in fh.read(8192):
                return False
    except OSError:
        return False
    return True


def git(*args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def tracked_and_untracked() -> list[str]:
    """Everything git would consider part of the repo. Ignored paths, data/ above
    all, are excluded by construction: this tool never reads the deployment's store."""
    listed = git("ls-files", "--cached", "--others", "--exclude-standard").splitlines()
    return sorted({p for p in listed if p})


def scan_paths(paths: Iterable[str], denylist: Denylist) -> list[Finding]:
    findings: list[Finding] = []
    for rel in paths:
        path = (REPO_ROOT / rel).resolve()
        if not is_scannable(path):
            if path.is_file() and opaque_in_fixtures(rel):
                findings.append(
                    Finding(rel, 1, "OPAQUE_FIXTURE", path.name, OPAQUE_FIXTURE_HINT)
                )
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        is_fixture, is_test = classify(rel)
        findings.extend(
            scan_lines(
                text.splitlines(),
                rel,
                is_fixture=is_fixture,
                is_test=is_test,
                denylist=denylist,
            )
        )
    return findings


def scan_history(rev_range: str | None, denylist: Denylist) -> list[Finding]:
    """Scan commit messages and added lines across history.

    A contributor who commits a real receipt and then amends it away still gets
    caught here, before merge. Only added lines are scanned; context lines are
    already covered by the working-tree scan.
    """
    args = ["log", "-p", "--no-color", "--no-merges", LOG_FORMAT]
    if rev_range:
        args.append(rev_range)
    try:
        out = git(*args)
    except subprocess.CalledProcessError as exc:
        print(
            f"scan_pii: could not read history ({exc.stderr.strip()})", file=sys.stderr
        )
        return []
    return scan_diff_stream(out, denylist)


def scan_diff_stream(out: str, denylist: Denylist = EMPTY_DENYLIST) -> list[Finding]:
    """Walk `git log -p` output produced with LOG_FORMAT."""
    findings: list[Finding] = []
    commit = "HEAD"
    current_file = ""
    in_message = False
    lineno = 0
    msg_lineno = 0
    prev_line = ""
    prev_msg = ""

    for line in out.splitlines():
        if line.startswith("\x01"):
            commit, current_file, msg_lineno = line[1:][:12], "", 0
            in_message, prev_msg = True, ""
            continue
        if line.startswith("diff --git"):
            current_file, in_message, prev_line = "", False, ""
            continue
        if line.startswith("+++ b/"):
            current_file, prev_line = line[6:], ""
            continue
        if line.startswith("@@"):
            m = HUNK_HEADER.match(line)
            lineno = int(m.group(1)) if m else 0
            continue
        if line.startswith(
            (
                "--- ",
                "index ",
                "old mode",
                "new mode",
                "similarity ",
                "rename ",
                "new file",
                "deleted file",
                "Binary files",
            )
        ):
            continue

        if in_message:
            msg_lineno += 1
            findings.extend(
                scan_lines(
                    [line],
                    f"{commit}:<message>",
                    denylist=denylist,
                    line_offset=msg_lineno,
                    preceding=prev_msg,
                )
            )
            prev_msg = line
            continue
        if not current_file:
            continue

        if line.startswith("-"):
            continue
        if line.startswith("+"):
            content, at = line[1:], lineno
            lineno += 1
        elif line.startswith(" ") or line == "":
            prev_line = line[1:] if line else ""
            lineno += 1
            continue
        else:
            continue

        is_fixture, is_test = classify(current_file)
        findings.extend(
            scan_lines(
                [content],
                f"{commit}:{current_file}",
                is_fixture=is_fixture,
                is_test=is_test,
                denylist=denylist,
                line_offset=at,
                preceding=prev_line,
            )
        )
        prev_line = content
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("paths", nargs="*", help="specific paths to scan")
    parser.add_argument(
        "--history",
        nargs="?",
        const="",
        default=None,
        metavar="RANGE",
        help="scan commit messages and diffs, optionally limited to a revision range",
    )
    args = parser.parse_args(argv)

    denylist = load_denylist()
    if args.history is not None:
        findings = scan_history(args.history or None, denylist)
        target = f"history {args.history}" if args.history else "full history"
    else:
        paths = args.paths or tracked_and_untracked()
        findings = scan_paths(paths, denylist)
        target = f"{len(paths)} path(s)"

    if not findings:
        note = (
            f"  (denylist: {denylist.size} entries via {denylist.source})"
            if denylist
            else "  (NO DENYLIST — literal tier not running; see SECURITY.md)"
        )
        print(f"scan_pii: clean — {target}{note}")
        return 0

    print(f"scan_pii: {len(findings)} finding(s) in {target}\n", file=sys.stderr)
    for f in findings:
        print(f.render(), file=sys.stderr)
    print(
        "\nMatches are masked above. If a finding is genuinely benign, add"
        "\n  # pii-scan: allow <reason>"
        "\non the offending line or the line directly above it. Never bypass this"
        "\ncheck with --no-verify.",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
